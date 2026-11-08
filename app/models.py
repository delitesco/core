from __future__ import annotations

import dataclasses
import enum
import os
import random
import uuid
from typing import List, Tuple, Optional

import arrow
import sqlalchemy as sa
from arrow import Arrow
from email_validator import validate_email
from flanker.addresslib import address
from jinja2 import FileSystemLoader, Environment
from sqlalchemy import orm
from sqlalchemy import text, desc, Index, Column
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import deferred
from sqlalchemy_utils import ArrowType

from app import config, rate_limiter
from app.db import Session
from app.errors import (
    AliasInTrashError,
    DirectoryInTrashError,
    SubdomainInTrashError,
    CannotCreateContactForReverseAlias,
)
from app.log import LOG
from app.utils import (
    convert_to_id,
    random_string,
    random_words,
    sanitize_email,
)

Base = declarative_base()

_PARTNER_SUBSCRIPTION_GRACE_DAYS = 14


class TSVector(sa.types.TypeDecorator):
    impl = TSVECTOR


class ModelMixin(object):
    id = sa.Column(sa.Integer, primary_key=True, autoincrement=True)
    created_at = sa.Column(ArrowType, default=arrow.utcnow, nullable=False)
    updated_at = sa.Column(ArrowType, default=None, onupdate=arrow.utcnow)

    _repr_hide = ["created_at", "updated_at"]

    @classmethod
    def query(cls):
        return Session.query(cls)

    @classmethod
    def yield_per_query(cls, page=1000):
        """to be used when iterating on a big table to avoid taking all the memory"""
        return Session.query(cls).yield_per(page).enable_eagerloads(False)

    @classmethod
    def get(cls, id):
        return Session.query(cls).get(id)

    @classmethod
    def get_by(cls, **kw):
        return Session.query(cls).filter_by(**kw).first()

    @classmethod
    def filter_by(cls, **kw):
        return Session.query(cls).filter_by(**kw)

    @classmethod
    def filter(cls, *args, **kw):
        return Session.query(cls).filter(*args, **kw)

    @classmethod
    def order_by(cls, *args, **kw):
        return Session.query(cls).order_by(*args, **kw)

    @classmethod
    def all(cls):
        return Session.query(cls).all()

    @classmethod
    def count(cls):
        return Session.query(cls).count()

    @classmethod
    def get_or_create(cls, **kw):
        r = cls.get_by(**kw)
        if not r:
            r = cls(**kw)
            Session.add(r)

        return r

    @classmethod
    def create(cls, **kw):
        # whether to call Session.commit
        commit = kw.pop("commit", False)
        flush = kw.pop("flush", False)

        r = cls(**kw)
        Session.add(r)

        if commit:
            Session.commit()

        if flush:
            Session.flush()

        return r

    def save(self):
        Session.add(self)

    @classmethod
    def delete(cls, obj_id, commit=False):
        Session.query(cls).filter(cls.id == obj_id).delete()

        if commit:
            Session.commit()

    @classmethod
    def first(cls):
        return Session.query(cls).first()

    def __repr__(self):
        values = ", ".join(
            "%s=%r" % (n, getattr(self, n))
            for n in self.__table__.c.keys()
            if n not in self._repr_hide
        )
        return "%s(%s)" % (self.__class__.__name__, values)


class EnumE(enum.Enum):
    @classmethod
    def has_value(cls, value: int) -> bool:
        return value in set(item.value for item in cls)

    @classmethod
    def get_name(cls, value: int) -> Optional[str]:
        for item in cls:
            if item.value == value:
                return item.name

        return None

    @classmethod
    def has_name(cls, name: str) -> bool:
        for item in cls:
            if item.name == name:
                return True

        return False

    @classmethod
    def get_value(cls, name: str) -> Optional[int]:
        for item in cls:
            if item.name == name:
                return item.value

        return None


# Specify the format for sender address
class SenderFormatEnum(EnumE):
    AT = 0  # John Wick - john at wick.com
    A = 2  # John Wick - john(a)wick.com
    NAME_ONLY = 5  # John Wick
    AT_ONLY = 6  # john at wick.com
    NO_NAME = 7


class AliasGeneratorEnum(EnumE):
    word = 1  # aliases are generated based on random words
    uuid = 2  # aliases are generated based on uuid


class AliasSuffixEnum(EnumE):
    word = 0  # Random word from dictionary file
    random_string = 1  # Completely random string


class BlockBehaviourEnum(EnumE):
    return_2xx = 0
    return_5xx = 1


class Phase(EnumE):
    unknown = 0
    forward = 1
    reply = 2


class VerpType(EnumE):
    bounce_forward = 0
    bounce_reply = 1
    transactional = 2


class JobState(EnumE):
    ready = 0
    taken = 1
    done = 2
    error = 3


class UnsubscribeBehaviourEnum(EnumE):
    DisableAlias = 0
    BlockContact = 1
    PreserveOriginal = 2


class AliasDeleteReason(EnumE):
    Unspecified = 0
    UserHasBeenDeleted = 1
    ManualAction = 2
    DirectoryDeleted = 3
    MailboxDeleted = 4
    CustomDomainDeleted = 5


class UserAliasDeleteAction(EnumE):
    MoveToTrash = 0
    DeleteImmediately = 1


class JobPriority(EnumE):
    Low = 1
    Default = 50
    High = 100


class IntEnumType(sa.types.TypeDecorator):
    impl = sa.Integer

    def __init__(self, enumtype, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._enum_type = enumtype

    def process_bind_param(self, enum_obj, dialect):
        if enum_obj is None:
            return None
        return enum_obj.value

    def process_result_value(self, enum_value, dialect):
        if enum_value is None:
            return None
        return self._enum_type(enum_value)


@dataclasses.dataclass
class AliasOptions:
    show_sl_domains: bool = True


class Hibp(Base, ModelMixin):
    __tablename__ = "hibp"
    name = sa.Column(sa.String(), nullable=False, unique=True, index=True)
    breached_aliases = orm.relationship("Alias", secondary="alias_hibp")

    description = sa.Column(sa.Text)
    date = sa.Column(ArrowType, nullable=True)

    def __repr__(self):
        return f"<HIBP Breach {self.id} {self.name}>"


class HibpNotifiedAlias(Base, ModelMixin):
    """Contain list of aliases that have been notified to users
    So that we can only notify users of new aliases.
    """

    __tablename__ = "hibp_notified_alias"
    alias_id = sa.Column(
        sa.ForeignKey("alias.id", ondelete="cascade"), nullable=False, index=True
    )
    user_id = sa.Column(sa.ForeignKey("users.id", ondelete="cascade"), nullable=False)

    notified_at = sa.Column(ArrowType, default=arrow.utcnow, nullable=False)

    __table_args__ = (sa.Index("ix_hibp_notified_alias_user_id", "user_id"),)


class AbuserData(Base, ModelMixin):
    __tablename__ = "abuser_data"

    user_id = sa.Column(sa.Integer, nullable=False, index=True)
    encrypted_bundle = sa.Column(sa.LargeBinary(), nullable=False)

    __table_args__ = (sa.Index("ix_abuser_data_id", "id"),)


class AbuserLookup(Base, ModelMixin):
    __tablename__ = "abuser_lookup"

    hashed_address = sa.Column(sa.String(64), nullable=False, index=True)
    abuser_data_id = sa.Column(
        sa.Integer,
        sa.ForeignKey("abuser_data.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )
    bundle_k = sa.Column(sa.LargeBinary(), nullable=False)


class AbuserAuditLog(Base, ModelMixin):
    __tablename__ = "abuser_audit_log"

    user_id = sa.Column(sa.Integer, nullable=False, index=True)
    admin_id = sa.Column(sa.Integer, nullable=True)
    action = sa.Column(sa.String(255), nullable=False)
    message = sa.Column(sa.Text, default=None, nullable=True)


class User(Base, ModelMixin):
    __tablename__ = "users"

    # bits 0-3 of `flags` held free-plan and partner flags; keep them unused

    email = sa.Column(sa.String(256), unique=True, nullable=False)

    name = sa.Column(sa.String(128), nullable=True)
    alias_generator = sa.Column(
        sa.Integer,
        nullable=False,
        default=AliasGeneratorEnum.word.value,
        server_default=str(AliasGeneratorEnum.word.value),
    )
    activated = sa.Column(sa.Boolean, default=False, nullable=False)

    # an account can be disabled if having harmful behavior
    disabled = sa.Column(sa.Boolean, default=False, nullable=False, server_default="0")

    # the default domain that's used when user creates a new random alias
    # default_alias_custom_domain_id XOR default_alias_public_domain_id
    default_alias_custom_domain_id = sa.Column(
        sa.ForeignKey("custom_domain.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    default_alias_public_domain_id = sa.Column(
        sa.ForeignKey("public_domain.id", ondelete="SET NULL"),
        nullable=True,
        default=None,
    )

    # the mailbox used when create random alias
    # this field is nullable but in practice, it's always set
    # it cannot be set to non-nullable though
    # as this will create foreign key cycle between User and Mailbox
    default_mailbox_id = sa.Column(
        sa.ForeignKey("mailbox.id"), nullable=True, default=None
    )

    # Specify the format for sender address
    # for the full list, see SenderFormatEnum
    sender_format = sa.Column(
        sa.Integer, default="0", nullable=False, server_default="0"
    )
    # to know whether user has explicitly chosen a sender format as opposed to those who use the default ones.
    # users who haven't chosen a sender format and are using 1 or 3 format, their sender format will be set to 0
    sender_format_updated_at = sa.Column(ArrowType, default=None)

    replace_reverse_alias = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # whether intro has been shown to user
    intro_shown = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    default_mailbox = orm.relationship("Mailbox", foreign_keys=[default_mailbox_id])

    # user can set a more strict max_spam score to block spams more aggressively
    max_spam_score = sa.Column(sa.Integer, nullable=True)

    # whether to include the sender address in reverse-alias
    include_sender_in_reverse_alias = sa.Column(
        sa.Boolean, default=True, nullable=False, server_default="0"
    )

    # whether to use random string or random word as suffix
    # Random word from dictionary file -> 0
    # Completely random string -> 1
    random_alias_suffix = sa.Column(
        sa.Integer,
        nullable=False,
        default=AliasSuffixEnum.word.value,
        server_default=str(AliasSuffixEnum.random_string.value),
    )

    # always expand the alias info, i.e. without needing to press "More"
    expand_alias_info = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # ignore emails send from a mailbox to its alias. This can happen when replying all to a forwarded email
    # can automatically re-includes the alias
    ignore_loop_email = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # by default, when an alias is automatically created, a note like "Created with ...." is created
    # If this field is True, the note won't be created.
    disable_automatic_alias_note = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # By default, the one-click unsubscribe disable the alias
    # If set to true, it will block the sender instead
    one_click_unsubscribe_block_sender = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # automatically include the website name when user creates an alias via the SimpleLogin icon in the email field
    include_website_in_one_click_alias = sa.Column(
        sa.Boolean,
        # new user will have this option turned on automatically
        default=True,
        nullable=False,
        # old user will have this option turned off
        server_default="0",
    )

    _directory_quota = sa.Column(
        "directory_quota", sa.Integer, default=50, nullable=False, server_default="50"
    )

    _subdomain_quota = sa.Column(
        "subdomain_quota", sa.Integer, default=5, nullable=False, server_default="5"
    )

    # user can use import to import too many aliases
    disable_import = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # Status code to return if is blocked
    block_behaviour = sa.Column(
        sa.Enum(BlockBehaviourEnum),
        nullable=False,
        server_default=BlockBehaviourEnum.return_2xx.name,
    )

    include_header_email_header = sa.Column(
        sa.Boolean, default=True, nullable=False, server_default="1"
    )

    # user opted in for data breach check
    enable_data_breach_check = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # bitwise flags. Allow for future expansion
    flags = sa.Column(
        sa.BigInteger,
        default=0,
        server_default="0",
        nullable=False,
    )

    # Keep original unsub behaviour
    unsub_behaviour = sa.Column(
        IntEnumType(UnsubscribeBehaviourEnum),
        default=UnsubscribeBehaviourEnum.PreserveOriginal,
        server_default=str(UnsubscribeBehaviourEnum.DisableAlias.value),
        nullable=False,
    )

    # Trigger hard deletion of the account at this time
    delete_on = sa.Column(ArrowType, default=None)

    # Action to perform when deleting an alias
    alias_delete_action = sa.Column(
        IntEnumType(UserAliasDeleteAction),
        default=UserAliasDeleteAction.MoveToTrash,
        server_default=str(UserAliasDeleteAction.MoveToTrash.value),
        nullable=False,
    )

    __table_args__ = (
        sa.Index("ix_users_delete_on", delete_on),
        sa.Index("ix_users_default_mailbox_id", default_mailbox_id),
        sa.Index(
            "ix_users_default_alias_custom_domain_id", default_alias_custom_domain_id
        ),
        sa.Index(
            "idx_users_email_trgm",
            "email",
            postgresql_ops={"email": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
    )

    @property
    def directory_quota(self):
        return min(
            self._directory_quota,
            config.MAX_NB_DIRECTORY - Directory.filter_by(user_id=self.id).count(),
        )

    @property
    def subdomain_quota(self):
        return min(
            self._subdomain_quota,
            config.MAX_NB_SUBDOMAIN
            - CustomDomain.filter_by(user_id=self.id, is_sl_subdomain=True).count(),
        )

    @staticmethod
    def subdomain_is_available():
        return SLDomain.filter_by(can_use_subdomain=True).count() > 0

    @classmethod
    def create(cls, email, name="", **kwargs):
        email = sanitize_email(email)
        user: User = super(User, cls).create(email=email, name=name[:100], **kwargs)
        Session.flush()

        mb = Mailbox.create(user_id=user.id, email=user.email, verified=True)
        Session.flush()
        user.default_mailbox_id = mb.id

        from app.user_audit_log_utils import emit_user_audit_log, UserAuditLogAction

        emit_user_audit_log(
            user=user,
            action=UserAuditLogAction.CreateUser,
            message=f"Created user {email}",
        )

        return user

    @classmethod
    def delete(cls, obj_id, commit=False):
        # Internal import to avoid global import cycles

        user: User = cls.get(obj_id)

        # Manually delete all aliases for the user that is about to be deleted
        from app.alias_delete import delete_alias

        for alias in Alias.filter_by(user_id=user.id):
            delete_alias(alias, user, AliasDeleteReason.UserHasBeenDeleted)

        res = super(User, cls).delete(obj_id)
        if commit:
            Session.commit()

        return res

    def is_active(self) -> bool:
        if self.delete_on is None:
            return True
        return self.delete_on < arrow.now()

    def is_premium(self) -> bool:
        """Every account is premium: billing is handled by the backend in front of this service."""
        return True

    def can_create_new_alias(self) -> bool:
        """
        Whether user can create a new alias: the account must be active and not disabled.
        """
        return self.can_create_num_aliases(1)

    def can_create_num_aliases(self, num_aliases: int) -> bool:
        if not self.is_active():
            return False

        if self.disabled:
            return False

        return True

    def can_send_or_receive(self) -> bool:
        if self.disabled:
            LOG.i(f"User {self} is disabled. Cannot receive or send emails")
            return False
        if self.delete_on is not None:
            LOG.i(
                f"User {self} is scheduled to be deleted. Cannot receive or send emails"
            )
            return False
        return True

    def suggested_emails(self, website_name) -> (str, [str]):
        """return suggested email and other email choices"""
        website_name = convert_to_id(website_name)

        all_aliases = [
            ge.email for ge in Alias.filter_by(user_id=self.id, enabled=True)
        ]
        if self.can_create_new_alias():
            suggested_alias = Alias.create_new(self, prefix=website_name).email
        else:
            # pick an email from the list of gen emails
            suggested_alias = random.choice(all_aliases)

        return (
            suggested_alias,
            list(set(all_aliases).difference({suggested_alias})),
        )

    def suggested_names(self) -> (str, [str]):
        """return suggested name and other name choices"""
        other_name = convert_to_id(self.name)

        return self.name, [other_name, "Anonymous", "whoami"]

    def get_name_initial(self) -> str:
        if not self.name:
            return ""
        names = self.name.split(" ")
        return "".join([n[0].upper() for n in names if n])

    def verified_custom_domains(self) -> List["CustomDomain"]:
        return (
            CustomDomain.filter_by(user_id=self.id, ownership_verified=True)
            .order_by(CustomDomain.domain.asc())
            .all()
        )

    def mailboxes(self) -> List["Mailbox"]:
        """list of mailbox that user own"""
        mailboxes = []

        for mailbox in Mailbox.filter_by(user_id=self.id, verified=True):
            mailboxes.append(mailbox)

        return mailboxes

    def nb_directory(self):
        return Directory.filter_by(user_id=self.id).count()

    def has_custom_domain(self):
        return CustomDomain.filter_by(user_id=self.id, verified=True).count() > 0

    def custom_domains(self) -> List["CustomDomain"]:
        return (
            CustomDomain.filter_by(user_id=self.id, verified=True)
            .order_by(CustomDomain.id.asc())
            .all()
        )

    def available_domains_for_random_alias(
        self, alias_options: Optional[AliasOptions] = None
    ) -> List[Tuple[bool, str]]:
        """Return available domains for user to create random aliases
        Each result record contains:
        - whether the domain belongs to SimpleLogin
        - the domain
        """
        res = []
        for domain in self.get_sl_domains(alias_options=alias_options):
            res.append((True, domain.domain))

        for custom_domain in self.verified_custom_domains():
            res.append((False, custom_domain.domain))

        return res

    def default_random_alias_domain(self) -> str:
        """return the domain used for the random alias"""
        if self.default_alias_custom_domain_id:
            custom_domain = CustomDomain.get(self.default_alias_custom_domain_id)
            # sanity check
            if (
                not custom_domain
                or not custom_domain.verified
                or custom_domain.user_id != self.id
            ):
                LOG.w("Problem with %s default random alias domain", self)
                return config.FIRST_ALIAS_DOMAIN

            return custom_domain.domain

        if self.default_alias_public_domain_id:
            sl_domain = SLDomain.get(self.default_alias_public_domain_id)
            # sanity check
            if not sl_domain:
                LOG.e("Problem with %s public random alias domain", self)
                return config.FIRST_ALIAS_DOMAIN

            if sl_domain.premium_only and not self.is_premium():
                LOG.w(
                    "%s is not premium and cannot use %s. Reset default random alias domain setting",
                    self,
                    sl_domain,
                )
                self.default_alias_custom_domain_id = None
                self.default_alias_public_domain_id = None
                Session.commit()
                return config.FIRST_ALIAS_DOMAIN

            return sl_domain.domain

        return config.FIRST_ALIAS_DOMAIN

    def available_sl_domains(
        self, alias_options: Optional[AliasOptions] = None
    ) -> [str]:
        """
        Return all SimpleLogin domains that user can use when creating a new alias, including:
        - SimpleLogin public domains, available for all users (ALIAS_DOMAIN)
        - SimpleLogin premium domains, only available for Premium accounts (PREMIUM_ALIAS_DOMAIN)
        """
        return [
            sl_domain.domain
            for sl_domain in self.get_sl_domains(alias_options=alias_options)
        ]

    def get_sl_domains(
        self, alias_options: Optional[AliasOptions] = None
    ) -> list["SLDomain"]:
        if alias_options is None:
            alias_options = AliasOptions()
        # every account is premium, so premium_only domains are always allowed
        if alias_options.show_sl_domains:
            visible = sa.true()
        elif self.default_alias_public_domain_id is not None:
            visible = SLDomain.id == self.default_alias_public_domain_id
        else:
            return []
        query = (
            Session.query(SLDomain)
            .filter(SLDomain.hidden == False, visible)  # noqa: E712
            .order_by(SLDomain.order)
        )
        return query.all()

    def available_alias_domains(
        self, alias_options: Optional[AliasOptions] = None
    ) -> [str]:
        """return all domains that user can use when creating a new alias, including:
        - SimpleLogin public domains, available for all users (ALIAS_DOMAIN)
        - SimpleLogin premium domains, only available for Premium accounts (PREMIUM_ALIAS_DOMAIN)
        - Verified custom domains

        """
        domains = [
            sl_domain.domain
            for sl_domain in self.get_sl_domains(alias_options=alias_options)
        ]

        for custom_domain in self.verified_custom_domains():
            domains.append(custom_domain.domain)

        # can have duplicate where a "root" user has a domain that's also listed in SL domains
        return list(set(domains))

    def get_random_alias_suffix(self, custom_domain: Optional["CustomDomain"] = None):
        """Get random suffix for an alias based on user's preference.

        Use a shorter suffix in case of custom domain

        Returns:
            str: the random suffix generated
        """
        if self.random_alias_suffix == AliasSuffixEnum.random_string.value:
            return random_string(config.ALIAS_RANDOM_SUFFIX_LENGTH, include_digits=True)

        if custom_domain is None:
            return random_words(1, 3)

        return random_words(1)

    def can_create_contacts(self) -> bool:
        return True

    def __repr__(self):
        return f"<User {self.id} {self.name} {self.email}>"


def _expiration_1h():
    return arrow.now().shift(hours=1)


def _expiration_12h():
    return arrow.now().shift(hours=12)


def _expiration_7d():
    return arrow.now().shift(days=7)


# <<< OAUTH models >>>


def available_sl_email(email: str) -> bool:
    if (
        Alias.get_by(email=email)
        or Contact.get_by(reply_email=email)
        or DeletedAlias.get_by(email=email)
    ):
        return False
    return True


def generate_random_alias_email(
    scheme: int = AliasGeneratorEnum.word.value,
    in_hex: bool = False,
    alias_domain: str = config.FIRST_ALIAS_DOMAIN,
    retries: int = 10,
) -> str:
    """generate an email address that does not exist before
    :param alias_domain: the domain used to generate the alias.
    :param scheme: int, value of AliasGeneratorEnum, indicate how the email is generated
    :param retries: int, How many times we can try to generate an alias in case of collision
    :type in_hex: bool, if the generate scheme is uuid, is hex favorable?
    """
    if retries <= 0:
        raise Exception("Cannot generate alias after many retries")
    if scheme == AliasGeneratorEnum.uuid.value:
        name = uuid.uuid4().hex if in_hex else uuid.uuid4().__str__()
        random_email = name + "@" + alias_domain
    else:
        random_email = random_words(2, 3) + "@" + alias_domain

    random_email = random_email.lower().strip()

    # check that the client does not exist yet
    if available_sl_email(random_email):
        LOG.d("generate email %s", random_email)
        return random_email

    # Rerun the function
    LOG.w("email %s already exists, generate a new email", random_email)
    return generate_random_alias_email(
        scheme=scheme, in_hex=in_hex, retries=retries - 1
    )


class Alias(Base, ModelMixin):
    __tablename__ = "alias"

    user_id = sa.Column(
        sa.ForeignKey(User.id, ondelete="cascade"), nullable=False, index=True
    )
    email = sa.Column(sa.String(128), unique=True, nullable=False)

    # the name to use when user replies/sends from alias
    name = sa.Column(sa.String(128), nullable=True, default=None)

    enabled = sa.Column(sa.Boolean(), default=True, nullable=False)
    flags = sa.Column(
        sa.BigInteger(), default=0, server_default="0", nullable=False, index=True
    )

    custom_domain_id = sa.Column(
        sa.ForeignKey("custom_domain.id", ondelete="cascade"), nullable=True, index=True
    )

    custom_domain = orm.relationship("CustomDomain", foreign_keys=[custom_domain_id])

    # To know whether an alias is created "on the fly", i.e. via the custom domain catch-all feature
    automatic_creation = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # to know whether an alias belongs to a directory
    directory_id = sa.Column(
        sa.ForeignKey("directory.id", ondelete="cascade"), nullable=True, index=True
    )

    note = sa.Column(sa.Text, default=None, nullable=True)

    # an alias can be owned by another mailbox
    mailbox_id = sa.Column(
        sa.ForeignKey("mailbox.id", ondelete="cascade"), nullable=False, index=True
    )

    # prefix _ to avoid this object being used accidentally.
    # To have the list of all mailboxes, should use AliasInfo instead
    _mailboxes = orm.relationship("Mailbox", secondary="alias_mailbox", lazy="joined")

    # If the mailbox has PGP-enabled, user can choose disable the PGP on the alias
    # this is useful when some senders already support PGP
    disable_pgp = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # a way to bypass the bounce automatic disable mechanism
    cannot_be_disabled = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # when a mailbox wants to send an email on behalf of the alias via the reverse-alias
    # several checks are performed to avoid email spoofing
    # this option allow disabling these checks
    disable_email_spoofing_check = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # to know whether an alias is added using a batch import
    # set in case of alias transfer.
    original_owner_id = sa.Column(
        sa.ForeignKey(User.id, ondelete="SET NULL"), nullable=True
    )

    # alias is pinned on top
    pinned = sa.Column(sa.Boolean, nullable=False, default=False, server_default="0")

    # used to transfer an alias to another user
    transfer_token = sa.Column(sa.String(64), default=None, unique=True, nullable=True)
    transfer_token_expiration = sa.Column(
        ArrowType, default=arrow.utcnow, nullable=True
    )

    # have I been pwned
    hibp_last_check = sa.Column(ArrowType, default=None, index=True)
    hibp_breaches = orm.relationship("Hibp", secondary="alias_hibp")

    # to use Postgres full text search. Only applied on "note" column for now
    # this is a generated Postgres column
    ts_vector = sa.Column(
        TSVector(), sa.Computed("to_tsvector('english', note)", persisted=True)
    )

    last_email_log_id = sa.Column(sa.Integer, default=None, nullable=True)

    delete_on = sa.Column(ArrowType, default=None, server_default=None, nullable=True)
    delete_reason = sa.Column(
        IntEnumType(AliasDeleteReason),
        default=None,
        server_default=None,
        nullable=True,
    )

    __table_args__ = (
        Index("ix_video___ts_vector__", ts_vector, postgresql_using="gin"),
        # index on note column using pg_trgm
        Index(
            "note_pg_trgm_index",
            "note",
            postgresql_ops={"note": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
        Index("ix_alias_original_owner_id", "original_owner_id"),
        Index("ix_alias_delete_on", "delete_on"),
    )

    user = orm.relationship(User, foreign_keys=[user_id])
    mailbox = orm.relationship("Mailbox", lazy="joined")

    @property
    def mailboxes(self):
        ret = [self.mailbox]
        for m in self._mailboxes:
            if m.id is not self.mailbox.id:
                ret.append(m)

        ret = [mb for mb in ret if mb.verified]
        ret = sorted(ret, key=lambda mb: mb.email)

        return ret

    def authorized_addresses(self) -> [str]:
        """return addresses that can send on behalf of this alias, i.e. can send emails to this alias's reverse-aliases
        Including its mailboxes and their authorized addresses
        """
        mailboxes = self.mailboxes
        ret = [mb.email for mb in mailboxes]
        for mailbox in mailboxes:
            for aa in mailbox.authorized_addresses:
                ret.append(aa.email)

        return ret

    def mailbox_support_pgp(self) -> bool:
        """return True of one of the mailboxes support PGP"""
        for mb in self.mailboxes:
            if mb.pgp_enabled():
                return True
        return False

    def pgp_enabled(self) -> bool:
        if self.mailbox_support_pgp() and not self.disable_pgp:
            return True
        return False

    def is_trashed(self) -> bool:
        return self.delete_on is not None

    @staticmethod
    def get_custom_domain(alias_address: str) -> Optional["CustomDomain"]:
        alias_domain = validate_email(
            alias_address, check_deliverability=False, allow_smtputf8=False
        ).domain

        # handle the case a SLDomain is also a CustomDomain
        if SLDomain.get_by(domain=alias_domain) is None:
            custom_domain = CustomDomain.get_by(domain=alias_domain)
            if custom_domain:
                return custom_domain

    @classmethod
    def create(cls, **kw):
        commit = kw.pop("commit", False)
        # always flushed: the creation audit log needs the alias id
        kw.pop("flush", False)

        new_alias = cls(**kw)
        user = User.get(new_alias.user_id)
        if user.is_premium():
            limits = config.ALIAS_CREATE_RATE_LIMIT_PAID
        else:
            limits = config.ALIAS_CREATE_RATE_LIMIT_FREE
        # limits is array of (hits,days)
        for limit in limits:
            key = f"alias_create_{limit[1]}:{user.id}"
            rate_limiter.check_bucket_limit(key, limit[0], limit[1])

        email = kw["email"]
        # make sure email is lowercase and doesn't have any whitespace
        email = sanitize_email(email)

        # make sure alias is not in global trash, i.e. DeletedAlias table
        if DeletedAlias.get_by(email=email):
            raise AliasInTrashError

        if DomainDeletedAlias.get_by(email=email):
            raise AliasInTrashError

        # detect whether alias should belong to a custom domain
        if "custom_domain_id" not in kw:
            custom_domain = Alias.get_custom_domain(email)
            if custom_domain:
                new_alias.custom_domain_id = custom_domain.id
        else:
            custom_domain = CustomDomain.get(kw["custom_domain_id"])

        Session.add(new_alias)
        # the audit log below needs new_alias.id
        Session.flush()

        if commit:
            Session.commit()

        # Internal import to avoid global import cycles
        from app.alias_audit_log_utils import AliasAuditLogAction, emit_alias_audit_log

        emit_alias_audit_log(
            new_alias, AliasAuditLogAction.CreateAlias, "New alias created"
        )

        return new_alias

    @classmethod
    def create_new(cls, user, prefix, note=None, mailbox_id=None):
        prefix = prefix.lower().strip().replace(" ", "")

        if not prefix:
            raise Exception("alias prefix cannot be empty")

        # find the right suffix - avoid infinite loop by running this at max 1000 times
        for _ in range(1000):
            suffix = user.get_random_alias_suffix()
            email = f"{prefix}.{suffix}@{config.FIRST_ALIAS_DOMAIN}"

            if available_sl_email(email):
                break

        return Alias.create(
            user_id=user.id,
            email=email,
            note=note,
            mailbox_id=mailbox_id or user.default_mailbox_id,
        )

    @classmethod
    def delete(cls, obj_id):
        raise Exception("should use delete_alias(alias,user) instead")

    @classmethod
    def create_new_random(
        cls,
        user,
        scheme: int = AliasGeneratorEnum.word.value,
        in_hex: bool = False,
        note: str = None,
    ):
        """create a new random alias"""
        custom_domain = None

        random_email = None

        if user.default_alias_custom_domain_id:
            custom_domain = CustomDomain.get(user.default_alias_custom_domain_id)
            random_email = generate_random_alias_email(
                scheme=scheme, in_hex=in_hex, alias_domain=custom_domain.domain
            )
        elif user.default_alias_public_domain_id:
            sl_domain: SLDomain = SLDomain.get(user.default_alias_public_domain_id)
            if sl_domain.premium_only and not user.is_premium():
                LOG.w("%s not premium, cannot use %s", user, sl_domain)
            else:
                random_email = generate_random_alias_email(
                    scheme=scheme, in_hex=in_hex, alias_domain=sl_domain.domain
                )

        if not random_email:
            random_email = generate_random_alias_email(scheme=scheme, in_hex=in_hex)

        alias = Alias.create(
            user_id=user.id,
            email=random_email,
            mailbox_id=user.default_mailbox_id,
            note=note,
        )

        if custom_domain:
            alias.custom_domain_id = custom_domain.id

        return alias

    def mailbox_email(self):
        if self.mailbox_id:
            return self.mailbox.email
        else:
            return self.user.email

    def __repr__(self):
        return f"<Alias {self.id} {self.email}>"


class Contact(Base, ModelMixin):
    """
    Store configuration of sender (website-email) and alias.
    """

    MAX_NAME_LENGTH = 512

    __tablename__ = "contact"

    __table_args__ = (
        sa.UniqueConstraint("alias_id", "website_email", name="uq_contact"),
        sa.Index("ix_contact_user_id_id", "user_id", "id"),
    )

    user_id = sa.Column(
        sa.ForeignKey(User.id, ondelete="cascade"),
        nullable=False,
    )
    alias_id = sa.Column(
        sa.ForeignKey(Alias.id, ondelete="cascade"),
        nullable=False,
    )

    name = sa.Column(
        sa.String(512), nullable=True, default=None, server_default=text("NULL")
    )

    website_email = sa.Column(sa.String(512), nullable=False)

    # the email from header, e.g. AB CD <ab@cd.com>
    # nullable as this field is added after website_email
    website_from = sa.Column(sa.String(1024), nullable=True)

    # when user clicks on "reply", they will reply to this address.
    # This address allows to hide user personal email
    # this reply email is created every time a website sends an email to user
    # it used to have the prefix "reply+" or "ra+"
    reply_email = sa.Column(sa.String(512), nullable=False, index=True)

    # whether a contact is created via CC
    is_cc = sa.Column(sa.Boolean, nullable=False, default=False, server_default="0")

    pgp_public_key = sa.Column(sa.Text, nullable=True)
    pgp_finger_print = sa.Column(sa.String(512), nullable=True, index=True)

    alias = orm.relationship(Alias, backref="contacts")
    user = orm.relationship(User)

    # the latest reply sent to this contact
    latest_reply: Optional[Arrow] = None

    # to investigate why the website_email is sometimes not correctly parsed
    # the envelope mail_from
    mail_from = sa.Column(sa.Text, nullable=True, default=None)

    # a contact can have an empty email address, in this case it can't receive emails
    invalid_email = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # emails sent from this contact will be blocked
    block_forward = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # whether contact is created automatically during the forward phase
    automatic_created = sa.Column(sa.Boolean, nullable=True, default=False)

    # contact flags
    flags = sa.Column(sa.Integer, nullable=False, default=0, server_default="0")

    @property
    def email(self):
        return self.website_email

    @classmethod
    def create(cls, **kw):
        commit = kw.pop("commit", False)
        flush = kw.pop("flush", False)

        new_contact = cls(**kw)

        website_email = kw["website_email"]
        # make sure email is lowercase and doesn't have any whitespace
        website_email = sanitize_email(website_email)

        # make sure contact.website_email isn't a reverse alias
        if website_email != config.NOREPLY:
            orig_contact = Contact.get_by(reply_email=website_email)
            if orig_contact:
                raise CannotCreateContactForReverseAlias(str(orig_contact))

        Session.add(new_contact)

        if commit:
            Session.commit()

        if flush:
            Session.flush()

        return new_contact

    def website_send_to(self):
        """return the email address with name.
        to use when user wants to send an email from the alias
        Return
        "First Last | email at example.com" <reverse-alias@SL>
        """

        # Prefer using contact name if possible
        user = self.user
        name = self.name
        email = self.website_email

        if (
            not user
            or not SenderFormatEnum.has_value(user.sender_format)
            or user.sender_format == SenderFormatEnum.AT.value
        ):
            email = email.replace("@", " at ")
        elif user.sender_format == SenderFormatEnum.A.value:
            email = email.replace("@", "(a)")

        # if no name, try to parse it from website_from
        if not name and self.website_from:
            try:
                name = address.parse(self.website_from).display_name
            except Exception:
                # Skip if website_from is wrongly formatted
                LOG.e(
                    "Cannot parse contact %s website_from %s", self, self.website_from
                )
                name = ""

        # remove all double quote
        if name:
            name = name.replace('"', "")

        if name:
            name = name + " | " + email
        else:
            name = email

        # cannot use formataddr here as this field is for email client, not for MTA
        return f'"{name}" <{self.reply_email}>'

    def new_addr(self):
        """
        Replace original email by reply_email. Possible formats:
        - First Last - first at example.com <reply_email> OR
        - First Last - first(a)example.com <reply_email> OR
        - First Last <reply_email>
        - first at example.com <reply_email>
        - reply_email
        And return new address with RFC 2047 format
        """
        user = self.user
        sender_format = user.sender_format if user else SenderFormatEnum.AT.value

        if sender_format == SenderFormatEnum.NO_NAME.value:
            return self.reply_email

        if sender_format == SenderFormatEnum.NAME_ONLY.value:
            new_name = self.name
        elif sender_format == SenderFormatEnum.AT_ONLY.value:
            new_name = self.website_email.replace("@", " at ").strip()
        elif sender_format == SenderFormatEnum.AT.value:
            formatted_email = self.website_email.replace("@", " at ").strip()
            new_name = (
                (self.name + " - " + formatted_email)
                if self.name and self.name != self.website_email.strip()
                else formatted_email
            )
        else:  # SenderFormatEnum.A.value
            formatted_email = self.website_email.replace("@", "(a)").strip()
            new_name = (
                (self.name + " - " + formatted_email)
                if self.name and self.name != self.website_email.strip()
                else formatted_email
            )

        from app.email_utils import sl_formataddr

        new_addr = sl_formataddr((new_name, self.reply_email)).strip()
        return new_addr.strip()

    def last_reply(self) -> "EmailLog":
        """return the most recent reply"""
        return (
            EmailLog.filter_by(contact_id=self.id, is_reply=True)
            .order_by(desc(EmailLog.created_at))
            .first()
        )

    def __repr__(self):
        return f"<Contact {self.id} {self.website_email} {self.alias_id}>"


class EmailLog(Base, ModelMixin):
    __tablename__ = "email_log"
    __table_args__ = (
        Index("ix_email_log_created_at", "created_at"),
        Index("ix_email_log_mailbox_id", "mailbox_id"),
        Index("ix_email_log_bounced_mailbox_id", "bounced_mailbox_id"),
        Index("ix_email_log_refused_email_id", "refused_email_id"),
        Index("ix_email_log_user_id_email_log_id", "user_id", "id"),
    )

    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)
    contact_id = sa.Column(
        sa.ForeignKey(Contact.id, ondelete="cascade"), nullable=False, index=True
    )
    alias_id = sa.Column(
        sa.ForeignKey(Alias.id, ondelete="cascade"), nullable=True, index=True
    )

    # whether this is a reply
    is_reply = sa.Column(sa.Boolean, nullable=False, default=False)

    # for ex if alias is disabled, this forwarding is blocked
    blocked = sa.Column(sa.Boolean, nullable=False, default=False)

    # can happen when user mailbox refuses the forwarded email
    # usually because the forwarded email is too spammy
    bounced = sa.Column(sa.Boolean, nullable=False, default=False, server_default="0")

    # happen when an email with auto (holiday) reply
    auto_replied = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # SpamAssassin result
    is_spam = sa.Column(sa.Boolean, nullable=False, default=False, server_default="0")
    spam_score = sa.Column(sa.Float, nullable=True)
    spam_status = sa.Column(sa.Text, nullable=True, default=None)
    # do not load this column
    spam_report = deferred(sa.Column(sa.JSON, nullable=True))

    # Point to the email that has been refused
    refused_email_id = sa.Column(
        sa.ForeignKey("refused_email.id", ondelete="SET NULL"), nullable=True
    )

    # in forward phase, this is the mailbox that will receive the email
    # in reply phase, this is the mailbox (or a mailbox's authorized address) that sends the email
    mailbox_id = sa.Column(
        sa.ForeignKey("mailbox.id", ondelete="cascade"), nullable=True
    )

    # in case of bounce, record on what mailbox the email has been bounced
    # useful when an alias has several mailboxes
    bounced_mailbox_id = sa.Column(
        sa.ForeignKey("mailbox.id", ondelete="cascade"), nullable=True
    )

    # the Message ID
    message_id = deferred(sa.Column(sa.String(1024), nullable=True))
    # in the reply phase, the original message_id is replaced by the SL message_id
    sl_message_id = deferred(sa.Column(sa.String(512), nullable=True))

    refused_email = orm.relationship("RefusedEmail")
    forward = orm.relationship(Contact)

    contact = orm.relationship(Contact, backref="email_logs")
    alias = orm.relationship(Alias)
    mailbox = orm.relationship("Mailbox", lazy="joined", foreign_keys=[mailbox_id])
    user = orm.relationship(User)

    def bounced_mailbox(self) -> str:
        if self.bounced_mailbox_id:
            return Mailbox.get(self.bounced_mailbox_id).email
        # retro-compatibility
        return self.contact.alias.mailboxes[0].email

    def get_action(self) -> str:
        """return the action name: forward|reply|block|bounced"""
        if self.is_reply:
            return "reply"
        elif self.bounced:
            return "bounced"
        elif self.blocked:
            return "block"
        else:
            return "forward"

    def get_phase(self) -> str:
        if self.is_reply:
            return "reply"
        else:
            return "forward"

    @classmethod
    def create(cls, *args, **kwargs):
        commit = kwargs.pop("commit", False)
        email_log = super().create(*args, **kwargs)
        Session.flush()
        if "alias_id" in kwargs:
            sql = "UPDATE alias SET last_email_log_id = :el_id WHERE id = :alias_id"
            Session.execute(
                sql, {"el_id": email_log.id, "alias_id": kwargs["alias_id"]}
            )
        if commit:
            Session.commit()
        return email_log

    def __repr__(self):
        return f"<EmailLog {self.id}>"


# https://help.apple.com/app-store-connect/#/dev58bda3212
class DeletedAlias(Base, ModelMixin):
    """Store all deleted alias to make sure they are NOT reused"""

    __tablename__ = "deleted_alias"

    email = sa.Column(sa.String(256), unique=True, nullable=False)
    reason = sa.Column(
        IntEnumType(AliasDeleteReason),
        nullable=False,
        default=AliasDeleteReason.Unspecified,
        server_default=str(AliasDeleteReason.Unspecified.value),
    )

    @classmethod
    def create(cls, **kw):
        raise Exception("should use delete_alias(alias,user) instead")

    def __repr__(self):
        return f"<Deleted Alias {self.email}>"


class AliasUsedOn(Base, ModelMixin):
    """Used to know where an alias is created"""

    __tablename__ = "alias_used_on"

    __table_args__ = (
        sa.UniqueConstraint("alias_id", "hostname", name="uq_alias_used"),
        sa.Index("ix_alias_used_on_user_id", "user_id"),
    )

    alias_id = sa.Column(
        sa.ForeignKey(Alias.id, ondelete="cascade"),
        nullable=False,
    )
    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)

    alias = orm.relationship(Alias)

    hostname = sa.Column(sa.String(1024), nullable=False)


class ApiKey(Base, ModelMixin):
    """used in browser extension to identify user"""

    __tablename__ = "api_key"

    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)
    code = sa.Column(sa.String(128), unique=True, nullable=False)
    name = sa.Column(sa.String(128), nullable=True)
    last_used = sa.Column(ArrowType, default=None)
    times = sa.Column(sa.Integer, default=0, nullable=False)
    user = orm.relationship(User)

    __table_args__ = (sa.Index("ix_api_key_user_id", "user_id"),)

    @classmethod
    def create(cls, user_id, name=None, **kwargs):
        code = random_string(60)
        if cls.get_by(code=code):
            code = str(uuid.uuid4())

        return super().create(user_id=user_id, name=name, code=code, **kwargs)

    @classmethod
    def delete_all(cls, user_id):
        Session.query(cls).filter(cls.user_id == user_id).delete()


class CustomDomain(Base, ModelMixin):
    __tablename__ = "custom_domain"

    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)
    domain = sa.Column(sa.String(128), unique=True, nullable=False)

    # default name to use when user replies/sends from alias
    name = sa.Column(sa.String(128), nullable=True, default=None)

    # mx verified
    verified = sa.Column(sa.Boolean, nullable=False, default=False)
    dkim_verified = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )
    spf_verified = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )
    dmarc_verified = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    _mailboxes = orm.relationship("Mailbox", secondary="domain_mailbox", lazy="joined")

    # an alias is created automatically the first time it receives an email
    catch_all = sa.Column(sa.Boolean, nullable=False, default=False, server_default="0")

    # option to generate random prefix version automatically
    random_prefix_generation = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # incremented when a check is failed on the domain
    # alert when the number exceeds a threshold
    # used in check_custom_domain()
    nb_failed_checks = sa.Column(
        sa.Integer, default=0, server_default="0", nullable=False
    )

    # only domain has the ownership verified can go the next DNS step
    # MX verified domains before this change don't have to do the TXT check
    # and therefore have ownership_verified=True
    ownership_verified = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # randomly generated TXT value for verifying domain ownership
    # the TXT record should be sl-verification=txt_token
    ownership_txt_token = sa.Column(sa.String(128), nullable=True)

    # if the domain is SimpleLogin subdomain, no need for the ownership, SPF, DKIM, DMARC check
    is_sl_subdomain = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    pending_deletion = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    __table_args__ = (
        Index(
            "ix_unique_domain",  # Index name
            "domain",  # Columns which are part of the index
            unique=True,
            postgresql_where=Column("ownership_verified"),
        ),  # The condition
        Index("ix_custom_domain_user_id", "user_id"),
        Index("ix_custom_domain_pending_deletion", "pending_deletion"),
    )

    user = orm.relationship(User, foreign_keys=[user_id], backref="custom_domains")

    @property
    def mailboxes(self):
        if self._mailboxes:
            return self._mailboxes
        else:
            return [self.user.default_mailbox]

    def nb_alias(self):
        from app.custom_domain_utils import count_custom_domain_aliases

        return count_custom_domain_aliases(self)

    @classmethod
    def create(cls, **kwargs):
        domain = kwargs.get("domain")
        kwargs["domain"] = domain.replace("\n", "")
        if DeletedSubdomain.get_by(domain=domain):
            raise SubdomainInTrashError

        domain: CustomDomain = super(CustomDomain, cls).create(**kwargs)

        # generate a domain ownership txt token
        if not domain.ownership_txt_token:
            domain.ownership_txt_token = random_string(30)
            Session.commit()

        if domain.is_sl_subdomain:
            user = domain.user
            user._subdomain_quota -= 1
            Session.flush()

        return domain

    @classmethod
    def delete(cls, obj_id):
        obj: CustomDomain = cls.get(obj_id)
        if obj.is_sl_subdomain:
            DeletedSubdomain.create(domain=obj.domain)

        from app.alias_delete import perform_alias_deletion

        for alias in Alias.filter_by(custom_domain_id=obj_id):
            perform_alias_deletion(
                alias, alias.user, AliasDeleteReason.CustomDomainDeleted
            )

        return super(CustomDomain, cls).delete(obj_id)

    @property
    def auto_create_rules(self):
        return sorted(self._auto_create_rules, key=lambda rule: rule.order)

    def __repr__(self):
        return f"<Custom Domain {self.id} {self.domain}>"


class AutoCreateRule(Base, ModelMixin):
    """Alias auto creation rule for custom domain"""

    __tablename__ = "auto_create_rule"

    __table_args__ = (
        sa.UniqueConstraint(
            "custom_domain_id", "order", name="uq_auto_create_rule_order"
        ),
    )

    custom_domain_id = sa.Column(
        sa.ForeignKey(CustomDomain.id, ondelete="cascade"), nullable=False
    )
    # an alias is auto created if it matches the regex
    regex = sa.Column(sa.String(512), nullable=False)

    # the order in which rules are evaluated in case there are multiple rules
    order = sa.Column(sa.Integer, default=0, nullable=False)

    custom_domain = orm.relationship(CustomDomain, backref="_auto_create_rules")

    mailboxes = orm.relationship(
        "Mailbox", secondary="auto_create_rule__mailbox", lazy="joined"
    )


class AutoCreateRuleMailbox(Base, ModelMixin):
    """store auto create rule - mailbox association"""

    __tablename__ = "auto_create_rule__mailbox"
    __table_args__ = (
        sa.UniqueConstraint(
            "auto_create_rule_id", "mailbox_id", name="uq_auto_create_rule_mailbox"
        ),
    )

    auto_create_rule_id = sa.Column(
        sa.ForeignKey(AutoCreateRule.id, ondelete="cascade"), nullable=False
    )
    mailbox_id = sa.Column(
        sa.ForeignKey("mailbox.id", ondelete="cascade"), nullable=False
    )


class DomainDeletedAlias(Base, ModelMixin):
    """Store all deleted alias for a domain"""

    __tablename__ = "domain_deleted_alias"

    __table_args__ = (
        sa.UniqueConstraint("domain_id", "email", name="uq_domain_trash"),
        sa.Index("ix_domain_deleted_alias_user_id", "user_id"),
    )

    email = sa.Column(sa.String(256), nullable=False)
    domain_id = sa.Column(
        sa.ForeignKey("custom_domain.id", ondelete="cascade"), nullable=False
    )
    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)

    domain = orm.relationship(CustomDomain)
    user = orm.relationship(User, foreign_keys=[user_id])
    reason = sa.Column(
        IntEnumType(AliasDeleteReason),
        nullable=False,
        default=AliasDeleteReason.Unspecified,
        server_default=str(AliasDeleteReason.Unspecified.value),
    )

    @classmethod
    def create(cls, **kw):
        raise Exception("should use delete_alias(alias,user) instead")

    def __repr__(self):
        return f"<DomainDeletedAlias {self.id} {self.email}>"


class Directory(Base, ModelMixin):
    __tablename__ = "directory"
    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)
    name = sa.Column(sa.String(128), unique=True, nullable=False)
    # when a directory is disabled, new alias can't be created on the fly
    disabled = sa.Column(sa.Boolean, default=False, nullable=False, server_default="0")

    user = orm.relationship(User, backref="directories")

    _mailboxes = orm.relationship(
        "Mailbox", secondary="directory_mailbox", lazy="joined"
    )

    __table_args__ = (sa.Index("ix_directory_user_id", "user_id"),)

    @property
    def mailboxes(self):
        if self._mailboxes:
            return self._mailboxes
        else:
            return [self.user.default_mailbox]

    def nb_alias(self):
        return Alias.filter_by(directory_id=self.id).count()

    @classmethod
    def create(cls, *args, **kwargs):
        name = kwargs.get("name")
        if DeletedDirectory.get_by(name=name):
            raise DirectoryInTrashError

        directory = super(Directory, cls).create(*args, **kwargs)
        Session.flush()

        user = directory.user
        user._directory_quota -= 1

        Session.flush()
        return directory

    @classmethod
    def delete(cls, obj_id):
        obj: Directory = cls.get(obj_id)
        user = obj.user
        # Put all aliases belonging to this directory to global or domain trash
        for alias in Alias.filter_by(directory_id=obj_id):
            from app import alias_delete

            alias_delete.delete_alias(alias, user, AliasDeleteReason.DirectoryDeleted)

        DeletedDirectory.create(name=obj.name)
        cls.filter(cls.id == obj_id).delete()

        Session.commit()

    def __repr__(self):
        return f"<Directory {self.name}>"


class Job(Base, ModelMixin):
    """Used to schedule one-time job in the future"""

    __tablename__ = "job"

    name = sa.Column(sa.String(128), nullable=False)
    payload = sa.Column(sa.JSON)

    # whether the job has been taken by the job runner
    taken = sa.Column(sa.Boolean, default=False, nullable=False)
    run_at = sa.Column(ArrowType)
    state = sa.Column(
        sa.Integer,
        nullable=False,
        server_default=str(JobState.ready.value),
        default=JobState.ready.value,
    )
    attempts = sa.Column(sa.Integer, nullable=False, server_default="0", default=0)
    taken_at = sa.Column(ArrowType, nullable=True)
    priority = sa.Column(
        IntEnumType(JobPriority),
        default=JobPriority.Default,
        server_default=str(JobPriority.Default.value),
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_state_run_at_taken_at_priority_attempts",
            state,
            run_at,
            taken_at,
            priority,
            attempts,
        ),
    )

    def __repr__(self):
        return f"<Job {self.id} {self.name} {self.payload}>"


class Mailbox(Base, ModelMixin):
    __tablename__ = "mailbox"
    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)
    email = sa.Column(sa.String(256), nullable=False, index=True)
    verified = sa.Column(sa.Boolean, default=False, nullable=False)
    force_spf = sa.Column(sa.Boolean, default=True, server_default="1", nullable=False)

    # used when user wants to update mailbox email
    # pending address change; unique per user, not globally, so users cannot
    # block or probe each other's pending changes
    new_email = sa.Column(sa.String(256), nullable=True)

    pgp_public_key = sa.Column(sa.Text, nullable=True)
    pgp_finger_print = sa.Column(sa.String(512), nullable=True)
    disable_pgp = sa.Column(
        sa.Boolean, default=False, nullable=False, server_default="0"
    )

    # incremented when a check is failed on the mailbox
    # alert when the number exceeds a threshold
    # used in sanity_check()
    nb_failed_checks = sa.Column(
        sa.Integer, default=0, server_default="0", nullable=False
    )

    # a mailbox can be disabled if it can't be reached
    disabled = sa.Column(sa.Boolean, default=False, nullable=False, server_default="0")

    generic_subject = sa.Column(sa.String(78), nullable=True)

    __table_args__ = (
        sa.UniqueConstraint("user_id", "email", name="uq_mailbox_user"),
        sa.UniqueConstraint("user_id", "new_email", name="uq_mailbox_user_new_email"),
        sa.Index("ix_mailbox_pgp_finger_print", "pgp_finger_print"),
        # index on email column using pg_trgm
        Index(
            "ix_mailbox_email_trgm_idx",
            "email",
            postgresql_ops={"email": "gin_trgm_ops"},
            postgresql_using="gin",
        ),
    )

    user = orm.relationship(User, foreign_keys=[user_id])

    def pgp_enabled(self) -> bool:
        if self.pgp_finger_print and not self.disable_pgp:
            return True

        return False

    def nb_alias(self):
        from app.mailbox_utils import count_mailbox_aliases

        return count_mailbox_aliases(self)

    @classmethod
    def delete(cls, obj_id):
        mailbox: Mailbox = cls.get(obj_id)
        user = mailbox.user

        # Put all aliases belonging to this mailbox to global or domain trash
        for alias in Alias.filter_by(mailbox_id=obj_id):
            # special handling for alias that has several mailboxes and has mailbox_id=obj_id
            if len(alias.mailboxes) > 1:
                # use the first mailbox found in alias._mailboxes
                first_mb = alias._mailboxes[0]
                alias.mailbox_id = first_mb.id
                alias._mailboxes.remove(first_mb)
            else:
                from app.alias_delete import perform_alias_deletion

                # only put aliases that have mailbox as a single mailbox into trash
                perform_alias_deletion(alias, user, AliasDeleteReason.MailboxDeleted)
            Session.commit()

        cls.filter(cls.id == obj_id).delete()
        Session.commit()

    @property
    def aliases(self) -> [Alias]:
        ret = dict(
            (alias.id, alias) for alias in Alias.filter_by(mailbox_id=self.id).all()
        )

        for am in AliasMailbox.filter_by(mailbox_id=self.id):
            if am.alias_id not in ret:
                ret[am.alias_id] = am.alias

        return list(ret.values())

    @classmethod
    def create(cls, **kw):
        if "email" in kw:
            kw["email"] = sanitize_email(kw["email"])
        return super().create(**kw)

    def __repr__(self):
        return f"<Mailbox {self.id} {self.email}>"


class MailboxActivation(Base, ModelMixin):
    __tablename__ = "mailbox_activation"

    mailbox_id = sa.Column(
        sa.ForeignKey(Mailbox.id, ondelete="cascade"), nullable=False, index=True
    )
    code = sa.Column(sa.String(32), nullable=False, index=True)
    tries = sa.Column(sa.Integer, default=0, nullable=False)


class RefusedEmail(Base, ModelMixin):
    """Store emails that have been refused, i.e. bounced or classified as spams"""

    __tablename__ = "refused_email"

    # Store the full report, including logs from Sending & Receiving MTA
    full_report_path = sa.Column(sa.String(128), unique=True, nullable=False)

    # The original email, to display to user
    path = sa.Column(sa.String(128), unique=True, nullable=True)

    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)

    # the email content will be deleted at this date
    delete_at = sa.Column(ArrowType, nullable=False, default=_expiration_7d)

    # toggle this when email content (stored at full_report_path & path are deleted)
    deleted = sa.Column(sa.Boolean, nullable=False, default=False, server_default="0")

    __table_args__ = (sa.Index("ix_refused_email_user_id", "user_id"),)

    def __repr__(self):
        return f"<Refused Email {self.id} {self.path} {self.delete_at}>"


class SentAlert(Base, ModelMixin):
    """keep track of alerts sent to user.
    User can receive an alert when there's abnormal activity on their aliases such as
    - reverse-alias not used by the owning mailbox
    - SPF fails when using the reverse-alias
    - bounced email
    - ...

    Different rate controls can then be implemented based on SentAlert:
    - only once alert: an alert type should be sent only once
    - max number of sent per 24H: an alert type should not be sent more than X times in 24h
    """

    __tablename__ = "sent_alert"

    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)
    to_email = sa.Column(sa.String(256), nullable=False)
    alert_type = sa.Column(sa.String(256), nullable=False)

    __table_args__ = (
        sa.Index("ix_sent_alert_user_id", "user_id"),
        sa.Index("ix_sent_alert_to_email", "to_email"),
        sa.Index("ix_sent_alert_alert_type", "alert_type"),
    )


class AliasMailbox(Base, ModelMixin):
    __tablename__ = "alias_mailbox"
    __table_args__ = (
        sa.UniqueConstraint("alias_id", "mailbox_id", name="uq_alias_mailbox"),
    )

    alias_id = sa.Column(
        sa.ForeignKey(Alias.id, ondelete="cascade"),
        nullable=False,
    )
    mailbox_id = sa.Column(
        sa.ForeignKey(Mailbox.id, ondelete="cascade"), nullable=False, index=True
    )

    alias = orm.relationship(Alias)


class AliasHibp(Base, ModelMixin):
    __tablename__ = "alias_hibp"

    __table_args__ = (sa.UniqueConstraint("alias_id", "hibp_id", name="uq_alias_hibp"),)

    alias_id = sa.Column(
        sa.Integer(),
        sa.ForeignKey("alias.id", ondelete="cascade"),
    )
    hibp_id = sa.Column(
        sa.Integer(), sa.ForeignKey("hibp.id", ondelete="cascade"), index=True
    )

    alias = orm.relationship(
        "Alias", backref=orm.backref("alias_hibp", cascade="all, delete-orphan")
    )
    hibp = orm.relationship(
        "Hibp", backref=orm.backref("alias_hibp", cascade="all, delete-orphan")
    )


class DirectoryMailbox(Base, ModelMixin):
    __tablename__ = "directory_mailbox"
    __table_args__ = (
        sa.UniqueConstraint("directory_id", "mailbox_id", name="uq_directory_mailbox"),
    )

    directory_id = sa.Column(
        sa.ForeignKey(Directory.id, ondelete="cascade"), nullable=False
    )
    mailbox_id = sa.Column(
        sa.ForeignKey(Mailbox.id, ondelete="cascade"), nullable=False
    )


class DomainMailbox(Base, ModelMixin):
    """store the owning mailboxes for a domain"""

    __tablename__ = "domain_mailbox"

    __table_args__ = (
        sa.UniqueConstraint("domain_id", "mailbox_id", name="uq_domain_mailbox"),
    )

    domain_id = sa.Column(
        sa.ForeignKey(CustomDomain.id, ondelete="cascade"), nullable=False
    )
    mailbox_id = sa.Column(
        sa.ForeignKey(Mailbox.id, ondelete="cascade"), nullable=False
    )


_NB_RECOVERY_CODE = 8
_RECOVERY_CODE_LENGTH = 8


class Notification(Base, ModelMixin):
    __tablename__ = "notification"
    user_id = sa.Column(
        sa.ForeignKey(User.id, ondelete="cascade"), nullable=False, index=True
    )
    message = sa.Column(sa.Text, nullable=False)
    title = sa.Column(sa.String(512))

    # whether user has marked the notification as read
    read = sa.Column(sa.Boolean, nullable=False, default=False)

    @staticmethod
    def render(template_name, **kwargs) -> str:
        templates_dir = os.path.join(config.ROOT_DIR, "templates")
        env = Environment(loader=FileSystemLoader(templates_dir))

        template = env.get_template(template_name)

        return template.render(
            URL=config.URL,
            LANDING_PAGE_URL=config.LANDING_PAGE_URL,
            YEAR=arrow.now().year,
            **kwargs,
        )


class SLDomain(Base, ModelMixin):
    """SimpleLogin domains"""

    __tablename__ = "public_domain"

    domain = sa.Column(sa.String(128), unique=True, nullable=False)

    # only available for premium accounts
    premium_only = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # if True, the domain can be used for the subdomain feature
    can_use_subdomain = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    # if enabled, do not show this domain when user creates a custom alias
    hidden = sa.Column(sa.Boolean, nullable=False, default=False, server_default="0")

    # the order in which the domains are shown when user creates a custom alias
    order = sa.Column(sa.Integer, nullable=False, default=0, server_default="0")

    use_as_reverse_alias = sa.Column(
        sa.Boolean, nullable=False, default=False, server_default="0"
    )

    def __repr__(self):
        return f"<SLDomain {self.id} {self.domain} {'Premium' if self.premium_only else 'Free'}>"


class AuthorizedAddress(Base, ModelMixin):
    """Authorize other addresses to send emails from aliases that are owned by a mailbox"""

    __tablename__ = "authorized_address"

    user_id = sa.Column(sa.ForeignKey(User.id, ondelete="cascade"), nullable=False)
    mailbox_id = sa.Column(
        sa.ForeignKey(Mailbox.id, ondelete="cascade"), nullable=False
    )
    email = sa.Column(sa.String(256), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("mailbox_id", "email", name="uq_authorize_address"),
        sa.Index("ix_authorized_address_user_id", "user_id"),
    )

    mailbox = orm.relationship(Mailbox, backref="authorized_addresses")

    def __repr__(self):
        return f"<AuthorizedAddress {self.id} {self.email} {self.mailbox_id}>"


class Bounce(Base, ModelMixin):
    """Record all bounces. Deleted after 7 days"""

    __tablename__ = "bounce"
    email = sa.Column(sa.String(256), nullable=False, index=True)
    info = sa.Column(sa.Text, nullable=True)

    __table_args__ = (sa.Index("ix_bounce_created_at", "created_at"),)


class TransactionalEmail(Base, ModelMixin):
    """Storing all email addresses that receive transactional emails, including account email and mailboxes.
    Deleted after 7 days
    """

    __tablename__ = "transactional_email"
    email = sa.Column(sa.String(256), nullable=False, unique=False)

    __table_args__ = (sa.Index("ix_transactional_email_created_at", "created_at"),)

    @classmethod
    def create(cls, **kw):
        # whether to call Session.commit
        commit = kw.pop("commit", False)

        r = cls(**kw)
        if not config.STORE_TRANSACTIONAL_EMAILS:
            return r

        Session.add(r)
        if commit:
            Session.commit()
        return r


class IgnoredEmail(Base, ModelMixin):
    """If an email has mail_from and rcpt_to present in this table, discard it by returning 250 status."""

    __tablename__ = "ignored_email"

    mail_from = sa.Column(sa.String(512), nullable=False)
    rcpt_to = sa.Column(sa.String(512), nullable=False)


class IgnoreBounceSender(Base, ModelMixin):
    """Ignore sender that doesn't correctly handle bounces, for example noreply@github.com"""

    __tablename__ = "ignore_bounce_sender"

    mail_from = sa.Column(sa.String(512), nullable=False, unique=True)

    def __repr__(self):
        return f"<NoReplySender {self.mail_from}"


class MessageIDMatching(Base, ModelMixin):
    """Store the SL Message ID and the original Message ID"""

    __tablename__ = "message_id_matching"

    # SimpleLogin Message ID
    sl_message_id = sa.Column(sa.String(512), unique=True, nullable=False)
    original_message_id = sa.Column(sa.String(1024), unique=True, nullable=False)

    # to track what email_log that has created this matching
    email_log_id = sa.Column(
        sa.ForeignKey("email_log.id", ondelete="cascade"), nullable=True, index=True
    )

    email_log = orm.relationship("EmailLog")


class DeletedDirectory(Base, ModelMixin):
    """To avoid directory from being reused"""

    __tablename__ = "deleted_directory"

    name = sa.Column(sa.String(128), unique=True, nullable=False)


class DeletedSubdomain(Base, ModelMixin):
    """To avoid directory from being reused"""

    __tablename__ = "deleted_subdomain"

    domain = sa.Column(sa.String(128), unique=True, nullable=False)


class InvalidMailboxDomain(Base, ModelMixin):
    """Domains that can't be used as mailbox"""

    __tablename__ = "invalid_mailbox_domain"

    domain = sa.Column(sa.String(256), unique=True, nullable=False)


class ForbiddenMxIp(Base, ModelMixin):
    """MX IPs that we don't allow to create mailboxes for"""

    __tablename__ = "forbidden_mx_ip"

    ip = sa.Column(sa.String(16), unique=True, nullable=False)
    comment = sa.Column(sa.Text, unique=False, nullable=True)


class ProviderComplaintState(EnumE):
    new = 0
    reviewed = 1


class ProviderComplaint(Base, ModelMixin):
    __tablename__ = "provider_complaint"

    user_id = sa.Column(sa.ForeignKey("users.id", ondelete="cascade"), nullable=False)
    state = sa.Column(
        sa.Integer, nullable=False, server_default=str(ProviderComplaintState.new.value)
    )
    phase = sa.Column(
        sa.Integer, nullable=False, server_default=str(Phase.unknown.value)
    )
    # Point to the email that has been refused
    refused_email_id = sa.Column(
        sa.ForeignKey("refused_email.id", ondelete="cascade"), nullable=True
    )

    user = orm.relationship(User, foreign_keys=[user_id])
    refused_email = orm.relationship(RefusedEmail, foreign_keys=[refused_email_id])

    __table_args__ = (
        sa.Index("ix_provider_complaint_user_id", "user_id"),
        sa.Index("ix_provider_complaint_refused_email_id", "refused_email_id"),
    )


# endregion


class AliasAuditLog(Base, ModelMixin):
    """This model holds an audit log for all the actions performed to an alias"""

    __tablename__ = "alias_audit_log"

    user_id = sa.Column(sa.Integer, nullable=False)
    alias_id = sa.Column(sa.Integer, nullable=False)
    alias_email = sa.Column(sa.String(255), nullable=False)
    action = sa.Column(sa.String(255), nullable=False)
    message = sa.Column(sa.Text, default=None, nullable=True)

    __table_args__ = (
        sa.Index("ix_alias_audit_log_user_id", "user_id"),
        sa.Index("ix_alias_audit_log_alias_id", "alias_id"),
        sa.Index("ix_alias_audit_log_alias_email", "alias_email"),
        sa.Index("ix_alias_audit_log_created_at", "created_at"),
    )


class UserAuditLog(Base, ModelMixin):
    """This model holds an audit log for all the actions performed by a user"""

    __tablename__ = "user_audit_log"

    user_id = sa.Column(sa.Integer, nullable=False)
    user_email = sa.Column(sa.String(255), nullable=False)
    action = sa.Column(sa.String(255), nullable=False)
    message = sa.Column(sa.Text, default=None, nullable=True)

    __table_args__ = (
        sa.Index("ix_user_audit_log_user_id", "user_id"),
        sa.Index("ix_user_audit_log_user_email", "user_email"),
        sa.Index("ix_user_audit_log_created_at", "created_at"),
    )

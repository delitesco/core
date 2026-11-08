import os


from app import file_storage
from app.config import ROOT_DIR, get_abs_path, FIRST_ALIAS_DOMAIN
from app.db import Session
from app.log import LOG
from app.models import (
    User,
    Alias,
    RefusedEmail,
    Contact,
    EmailLog,
    ApiKey,
    Mailbox,
    AliasMailbox,
    CustomDomain,
    Directory,
    Notification,
    SLDomain,
    Hibp,
    AliasHibp,
)
from app.pgp_utils import load_public_key


def fake_data():
    LOG.d("create fake data")

    # Create a user
    user = User.create(
        email="john@wick.com",
        name="John Wick",
        activated=True,
        intro_shown=True,
    )
    Session.commit()

    # create a bounced email
    alias = Alias.create_new_random(user)
    Session.commit()

    bounce_email_file_path = "bounce.eml"
    file_storage.upload_email_from_bytesio(
        bounce_email_file_path,
        open(os.path.join(ROOT_DIR, "local_data", "email_tests", "2.eml"), "rb"),
        "download.eml",
    )
    refused_email = RefusedEmail.create(
        path=bounce_email_file_path,
        full_report_path=bounce_email_file_path,
        user_id=user.id,
        commit=True,
    )

    contact = Contact.create(
        user_id=user.id,
        alias_id=alias.id,
        website_email="hey@google.com",
        reply_email="rep@sl.lan",
        commit=True,
    )
    EmailLog.create(
        user_id=user.id,
        contact_id=contact.id,
        alias_id=contact.alias_id,
        refused_email_id=refused_email.id,
        bounced=True,
        commit=True,
    )

    api_key = ApiKey.create(user_id=user.id, name="Chrome")
    api_key.code = "code"

    api_key = ApiKey.create(user_id=user.id, name="Firefox")
    api_key.code = "codeFF"

    pgp_public_key = open(get_abs_path("local_data/public-pgp.asc")).read()
    m1 = Mailbox.create(
        user_id=user.id,
        email="pgp@example.org",
        verified=True,
        pgp_public_key=pgp_public_key,
    )
    m1.pgp_finger_print = load_public_key(pgp_public_key)
    Session.commit()

    # example@example.com is in a LOT of data breaches
    Alias.create(email="example@example.com", user_id=user.id, mailbox_id=m1.id)

    for i in range(3):
        if i % 2 == 0:
            a = Alias.create(
                email=f"e{i}@{FIRST_ALIAS_DOMAIN}", user_id=user.id, mailbox_id=m1.id
            )
        else:
            a = Alias.create(
                email=f"e{i}@{FIRST_ALIAS_DOMAIN}",
                user_id=user.id,
                mailbox_id=user.default_mailbox_id,
            )
        Session.commit()

        if i % 5 == 0:
            if i % 2 == 0:
                AliasMailbox.create(alias_id=a.id, mailbox_id=user.default_mailbox_id)
            else:
                AliasMailbox.create(alias_id=a.id, mailbox_id=m1.id)
        Session.commit()

        # some aliases don't have any activity
        # if i % 3 != 0:
        #     contact = Contact.create(
        #         user_id=user.id,
        #         alias_id=a.id,
        #         website_email=f"contact{i}@example.com",
        #         reply_email=f"rep{i}@sl.lan",
        #     )
        #     Session.commit()
        #     for _ in range(3):
        #         EmailLog.create(user_id=user.id, contact_id=contact.id, alias_id=contact.alias_id)
        #         Session.commit()

        # have some disabled alias
        if i % 5 == 0:
            a.enabled = False
            Session.commit()

    custom_domain1 = CustomDomain.create(user_id=user.id, domain="ab.cd", verified=True)
    Session.commit()

    Alias.create(
        user_id=user.id,
        email="first@ab.cd",
        mailbox_id=user.default_mailbox_id,
        custom_domain_id=custom_domain1.id,
        commit=True,
    )

    Alias.create(
        user_id=user.id,
        email="second@ab.cd",
        mailbox_id=user.default_mailbox_id,
        custom_domain_id=custom_domain1.id,
        commit=True,
    )

    Directory.create(user_id=user.id, name="abcd")
    Directory.create(user_id=user.id, name="xyzt")
    Session.commit()

    for i in range(6):
        Notification.create(user_id=user.id, message=f"""Hey hey <b>{i}</b> """ * 10)
    Session.commit()

    user2 = User.create(
        email="winston@continental.com",
        activated=True,
    )
    Mailbox.create(user_id=user2.id, email="winston2@high.table", verified=True)
    Session.commit()

    SLDomain.create(domain="premium.com", premium_only=True, commit=True)

    hibp1 = Hibp.create(
        name="first breach", description="breach description", commit=True
    )
    hibp2 = Hibp.create(
        name="second breach", description="breach description", commit=True
    )
    breached_alias1 = Alias.create(
        email="john@example.com", user_id=user.id, mailbox_id=m1.id, commit=True
    )
    breached_alias2 = Alias.create(
        email="wick@example.com", user_id=user.id, mailbox_id=m1.id, commit=True
    )
    AliasHibp.create(hibp_id=hibp1.id, alias_id=breached_alias1.id)
    AliasHibp.create(hibp_id=hibp2.id, alias_id=breached_alias2.id)

    # old domain will have ownership_verified=True
    CustomDomain.create(
        user_id=user.id, domain="old.com", verified=True, ownership_verified=True
    )

    Session.commit()

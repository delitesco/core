import arrow
from aiosmtpd.smtp import Envelope

import email_handler
from app import config
from app.abuse_report import daily_report
from app.abuser_audit_log_utils import AbuserAuditLogAction
from app.db import Session
from app.email import status
from app.mail_sender import mail_sender
from app.models import AbuserAuditLog, Alias, Contact, EmailLog, User
from app.reply_limits import (
    block_replies,
    block_replies_if_abusive,
    reply_refusal,
    unblock_replies,
)
from tests.utils import create_new_user, load_eml_file, random_email, random_token


def _contact(user: User) -> Contact:
    alias = Alias.filter_by(user_id=user.id).first()
    return Contact.create(
        user_id=user.id,
        alias_id=alias.id,
        website_email=random_email(),
        reply_email=f"ra_{random_token(10)}@{config.EMAIL_DOMAIN}",
        commit=True,
    )


def _sent_reply(contact: Contact, **flags) -> EmailLog:
    return EmailLog.create(
        user_id=contact.user_id,
        contact_id=contact.id,
        alias_id=contact.alias_id,
        is_reply=True,
        commit=True,
        **flags,
    )


def _old_user() -> User:
    user = create_new_user()
    user.created_at = arrow.now().shift(days=-config.NEW_ACCOUNT_DAYS - 1)
    Session.commit()
    return user


def test_new_account_reply_cap(flask_client, monkeypatch):
    monkeypatch.setattr(config, "NEW_ACCOUNT_MAX_REPLIES_PER_DAY", 2)
    user = create_new_user()
    contact = _contact(user)
    _sent_reply(contact)
    assert reply_refusal(user, contact) is None

    _sent_reply(contact)
    assert reply_refusal(user, contact) == status.E529


def test_old_account_gets_the_higher_cap(flask_client, monkeypatch):
    monkeypatch.setattr(config, "NEW_ACCOUNT_MAX_REPLIES_PER_DAY", 1)
    monkeypatch.setattr(config, "MAX_REPLIES_PER_DAY", 3)
    user = _old_user()
    contact = _contact(user)
    _sent_reply(contact)
    _sent_reply(contact)
    assert reply_refusal(user, contact) is None


def test_replies_older_than_a_day_do_not_count(flask_client, monkeypatch):
    monkeypatch.setattr(config, "NEW_ACCOUNT_MAX_REPLIES_PER_DAY", 1)
    user = create_new_user()
    contact = _contact(user)
    _sent_reply(contact, created_at=arrow.now().shift(hours=-25))
    assert reply_refusal(user, contact) is None


def test_new_recipients_cap_keeps_known_ones(flask_client, monkeypatch):
    monkeypatch.setattr(config, "NEW_ACCOUNT_MAX_REPLY_RECIPIENTS_PER_DAY", 1)
    user = create_new_user()
    known = _contact(user)
    _sent_reply(known)

    assert reply_refusal(user, known) is None
    assert reply_refusal(user, _contact(user)) == status.E530


@mail_sender.store_emails_test_decorator
def test_blocked_replies_are_refused_and_forwarding_goes_on(flask_client):
    user = create_new_user()
    block_replies(user, "test")
    contact = _contact(user)

    msg = load_eml_file("no_spamd_header.eml", {"alias_email": contact.reply_email})
    envelope = Envelope()
    envelope.mail_from = user.default_mailbox.email
    envelope.rcpt_tos = [contact.reply_email]
    mail_sender.purge_stored_emails()
    assert email_handler.handle_reply(envelope, msg, contact.reply_email) == (
        False,
        status.E528,
    )
    assert mail_sender.get_stored_emails() == []
    assert EmailLog.filter_by(user_id=user.id).count() == 0

    alias = Alias.filter_by(user_id=user.id).first()
    msg = load_eml_file("no_spamd_header.eml", {"alias_email": alias.email})
    envelope = Envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = [alias.email]
    results = email_handler.handle_forward(envelope, msg, alias.email)
    assert all(success for success, _ in results), results


@mail_sender.store_emails_test_decorator
def test_spam_replies_block_the_account(flask_client, monkeypatch):
    monkeypatch.setattr(config, "REPLY_BLOCK_AFTER_SPAM", 2)
    monkeypatch.setattr(config, "ABUSE_REPORT_EMAIL", "abuse@example.com")
    user = create_new_user()
    contact = _contact(user)

    _sent_reply(contact, is_spam=True)
    block_replies_if_abusive(user)
    assert user.replies_blocked_at is None

    _sent_reply(contact, is_spam=True)
    block_replies_if_abusive(user)
    assert user.replies_blocked_at is not None

    log = AbuserAuditLog.filter_by(user_id=user.id).one()
    assert log.action == AbuserAuditLogAction.BlockReplies.value
    assert log.message.startswith("2 replies refused as spam")
    recipients = {sent.envelope_to for sent in mail_sender.get_stored_emails()}
    assert recipients == {user.default_mailbox.email, "abuse@example.com"}


@mail_sender.store_emails_test_decorator
def test_bounced_replies_block_the_account(flask_client, monkeypatch):
    monkeypatch.setattr(config, "REPLY_BLOCK_AFTER_BOUNCES", 2)
    user = create_new_user()
    contact = _contact(user)
    _sent_reply(contact, bounced=True)
    _sent_reply(contact, bounced=True)

    block_replies_if_abusive(user)
    assert user.replies_blocked_at is not None


@mail_sender.store_emails_test_decorator
def test_unblock_lets_replies_out_again(flask_client):
    user = create_new_user()
    contact = _contact(user)
    block_replies(user, "test")
    unblock_replies(user, "checked")

    assert reply_refusal(user, contact) is None
    actions = [
        log.action
        for log in AbuserAuditLog.filter_by(user_id=user.id).order_by(AbuserAuditLog.id)
    ]
    assert actions == ["block_replies", "unblock_replies"]


@mail_sender.store_emails_test_decorator
def test_block_notes_go_with_the_account(flask_client):
    user = create_new_user()
    user_id = user.id
    block_replies(user, "test")
    User.delete(user_id, commit=True)
    assert AbuserAuditLog.filter_by(user_id=user_id).count() == 0


@mail_sender.store_emails_test_decorator
def test_daily_report_lists_senders_and_blocks(flask_client):
    user = create_new_user()
    contact = _contact(user)
    _sent_reply(contact, is_spam=True)
    block_replies(user, "test reason")

    report = daily_report()
    assert "Most replies refused as spam or bounced" in report
    assert f"  {user.id:<8}  " in report
    assert f"user {user.id} at " in report
    assert "test reason" in report

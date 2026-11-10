"""Replies are the abuse channel: they send mail from our IPs to any address.

An account has daily caps on replies and on new recipients, and its replies are blocked
once too many of a day's replies are refused as spam or bounce. Forwarding never stops here.
"""

from typing import Optional

import arrow
from sqlalchemy import func

from app import config
from app.abuser_audit_log_utils import AbuserAuditLogAction, emit_abuser_audit_log
from app.db import Session
from app.email import status
from app.email_utils import render, send_email
from app.log import LOG
from app.models import Contact, EmailLog, User


def _replies_of_last_day(user: User):
    return EmailLog.filter(
        EmailLog.user_id == user.id,
        EmailLog.is_reply,
        EmailLog.created_at > arrow.now().shift(days=-1),
    )


def reply_refusal(user: User, contact: Contact) -> Optional[str]:
    """The SMTP status to refuse a reply to `contact` with, None when it can go out."""
    if user.replies_blocked_at:
        return status.E528

    is_new = user.created_at > arrow.now().shift(days=-config.NEW_ACCOUNT_DAYS)
    if is_new:
        max_replies = config.NEW_ACCOUNT_MAX_REPLIES_PER_DAY
        max_recipients = config.NEW_ACCOUNT_MAX_REPLY_RECIPIENTS_PER_DAY
    else:
        max_replies = config.MAX_REPLIES_PER_DAY
        max_recipients = config.MAX_REPLY_RECIPIENTS_PER_DAY

    replies = _replies_of_last_day(user)
    if replies.count() >= max_replies:
        LOG.w("%s reached the daily reply limit of %s", user, max_replies)
        return status.E529

    recipients = {
        contact_id
        for (contact_id,) in replies.with_entities(EmailLog.contact_id).distinct()
    }
    if contact.id not in recipients and len(recipients) >= max_recipients:
        LOG.w("%s reached the daily limit of %s reply recipients", user, max_recipients)
        return status.E530

    return None


def block_replies(user: User, reason: str) -> None:
    user.replies_blocked_at = arrow.now()
    emit_abuser_audit_log(
        user_id=user.id, action=AbuserAuditLogAction.BlockReplies, message=reason
    )
    Session.commit()
    LOG.w("Blocked replies of %s: %s", user, reason)

    send_email(
        user.default_mailbox.email,
        "Replies from your aliases are paused",
        render("transactional/replies-blocked.txt.jinja2", user=user),
    )


def unblock_replies(user: User, reason: str) -> None:
    user.replies_blocked_at = None
    emit_abuser_audit_log(
        user_id=user.id, action=AbuserAuditLogAction.UnblockReplies, message=reason
    )
    Session.commit()
    LOG.i("Unblocked replies of %s: %s", user, reason)


def block_replies_if_abusive(user: User) -> None:
    """Run after a reply of `user` was refused as spam or bounced."""
    if user.replies_blocked_at:
        return

    spam, bounced = (
        _replies_of_last_day(user)
        .with_entities(
            func.count().filter(EmailLog.is_spam),
            func.count().filter(EmailLog.bounced),
        )
        .one()
    )
    if (
        spam < config.REPLY_BLOCK_AFTER_SPAM
        and bounced < config.REPLY_BLOCK_AFTER_BOUNCES
    ):
        return

    reason = f"{spam} replies refused as spam and {bounced} bounced in 24 hours"
    block_replies(user, reason)
    if config.ABUSE_REPORT_EMAIL:
        send_email(
            config.ABUSE_REPORT_EMAIL,
            f"Replies blocked for user {user.id}",
            f"{reason}.\n\n"
            f"Details: python -m commands.abuse show {user.id}\n"
            f"Unblock: python -m commands.abuse unblock-replies {user.id} --reason ...\n",
        )

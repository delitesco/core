"""Who sends what: the daily report to ABUSE_REPORT_EMAIL and the tables of commands/abuse.py.

Only counts per account, never addresses or message content.
"""

from typing import List

import arrow
from sqlalchemy import distinct, func

from app import config
from app.abuser_audit_log_utils import AbuserAuditLogAction
from app.db import Session
from app.email_utils import send_email
from app.models import AbuserAuditLog, EmailLog, User

REPLY_COLUMNS = "  user      replies  recipients  spam  bounced  account"


def reply_senders(since: arrow.Arrow, worst_first: bool, limit: int = 10) -> List[str]:
    """Table rows of the accounts that replied since `since`: by number of replies, or
    with `worst_first` by replies refused as spam or bounced, leaving out those with none."""
    spam = func.count().filter(EmailLog.is_spam)
    bounced = func.count().filter(EmailLog.bounced)
    query = (
        Session.query(
            EmailLog.user_id,
            func.count(),
            func.count(distinct(EmailLog.contact_id)),
            spam,
            bounced,
        )
        .filter(EmailLog.is_reply, EmailLog.created_at > since)
        .group_by(EmailLog.user_id)
    )
    if worst_first:
        query = query.having(spam + bounced > 0).order_by((spam + bounced).desc())
    else:
        query = query.order_by(func.count().desc())

    rows = []
    for user_id, replies, recipients, nb_spam, nb_bounced in query.limit(limit):
        user = User.get(user_id)
        rows.append(
            f"  {user_id:<8}  {replies:>7}  {recipients:>10}  {nb_spam:>4}  {nb_bounced:>7}"
            f"  {describe_account(user)}"
        )
    return rows


def activity_of(user: User, since: arrow.Arrow) -> str:
    forwards, replies, recipients, spam, bounced = (
        Session.query(
            func.count().filter(~EmailLog.is_reply),
            func.count().filter(EmailLog.is_reply),
            func.count(distinct(EmailLog.contact_id)).filter(EmailLog.is_reply),
            func.count().filter(EmailLog.is_reply, EmailLog.is_spam),
            func.count().filter(EmailLog.is_reply, EmailLog.bounced),
        )
        .filter(EmailLog.user_id == user.id, EmailLog.created_at > since)
        .one()
    )
    return (
        f"forwarded {forwards}, replies {replies} to {recipients} recipients, "
        f"refused as spam {spam}, bounced {bounced}"
    )


def describe_account(user: User) -> str:
    days = (arrow.now() - user.created_at).days
    text = f"{days} days old"
    if user.replies_blocked_at:
        text += ", replies blocked"
    if user.disabled:
        text += ", disabled"
    return text


def daily_report() -> str:
    now = arrow.now()
    since = now.shift(days=-1)

    forwards, replies, spam, bounced = (
        Session.query(
            func.count().filter(~EmailLog.is_reply),
            func.count().filter(EmailLog.is_reply),
            func.count().filter(EmailLog.is_reply, EmailLog.is_spam),
            func.count().filter(EmailLog.is_reply, EmailLog.bounced),
        )
        .filter(EmailLog.created_at > since)
        .one()
    )
    lines = [
        f"24 hours to {now.format('YYYY-MM-DD HH:mm')} UTC",
        "",
        f"Forwarded: {forwards}. Replies: {replies}, refused as spam: {spam}, "
        f"bounced: {bounced}.",
    ]

    top = reply_senders(since, worst_first=False)
    if top:
        lines += ["", "Most replies", REPLY_COLUMNS, *top]

    worst = reply_senders(since, worst_first=True)
    if worst:
        lines += ["", "Most replies refused as spam or bounced", REPLY_COLUMNS, *worst]

    blocks = AbuserAuditLog.filter(
        AbuserAuditLog.action == AbuserAuditLogAction.BlockReplies.value,
        AbuserAuditLog.created_at > since,
    ).order_by(AbuserAuditLog.created_at)
    block_lines = [
        f"  user {block.user_id} at {block.created_at.format('HH:mm')}: {block.message}"
        for block in blocks
    ]
    if block_lines:
        lines += ["", "Replies blocked in these 24 hours", *block_lines]

    blocked_ids = [
        user_id
        for (user_id,) in Session.query(User.id)
        .filter(User.replies_blocked_at.isnot(None))
        .order_by(User.id)
    ]
    if blocked_ids:
        lines += [
            "",
            f"Replies blocked now: {', '.join(str(user_id) for user_id in blocked_ids)}",
        ]

    lines += ["", "Details: python -m commands.abuse show <user>"]
    return "\n".join(lines) + "\n"


def send_daily_report() -> None:
    # sent even on a quiet day: a missing report means the cron job stopped
    send_email(
        config.ABUSE_REPORT_EMAIL,
        f"{config.BRAND_NAME} abuse report {arrow.now().format('YYYY-MM-DD')}",
        daily_report(),
    )

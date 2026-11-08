import arrow

from app.config import PAGE_LIMIT
from app.db import Session
from app.models import Alias, EmailLog, Contact


class AliasLog:
    website_email: str
    reverse_alias: str
    alias: str
    when: arrow.Arrow
    is_reply: bool
    blocked: bool
    bounced: bool
    email_log: EmailLog
    contact: Contact

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def get_alias_log(alias: Alias, page_id=0) -> [AliasLog]:
    logs: [AliasLog] = []

    q = (
        Session.query(Contact, EmailLog)
        .filter(Contact.id == EmailLog.contact_id)
        .filter(Contact.alias_id == alias.id)
        .order_by(EmailLog.id.desc())
        .limit(PAGE_LIMIT)
        .offset(page_id * PAGE_LIMIT)
    )

    for contact, email_log in q:
        al = AliasLog(
            website_email=contact.website_email,
            reverse_alias=contact.website_send_to(),
            alias=alias.email,
            when=email_log.created_at,
            is_reply=email_log.is_reply,
            blocked=email_log.blocked,
            bounced=email_log.bounced,
            email_log=email_log,
            contact=contact,
        )
        logs.append(al)
    logs = sorted(logs, key=lambda log: log.when, reverse=True)

    return logs

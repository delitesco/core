from email.message import EmailMessage

from app import email_utils
from app.email import headers
from app.email_utils import spf_pass
from app.models import Alias, Mailbox
from tests.utils import create_new_user


def test_spf_uses_the_ip_prepended_by_postfix(monkeypatch):
    user = create_new_user()
    alias = Alias.create_new_random(user)
    mailbox = Mailbox.get(user.default_mailbox_id)
    seen = []

    def check2(i, s, h):
        seen.append(i)
        return ("pass", 250, "")

    monkeypatch.setattr(email_utils.spf, "check2", check2)

    msg = EmailMessage()
    # Postfix prepends its header; a header sent by the client comes after it
    msg[headers.SL_CLIENT_IP] = "198.51.100.7"
    msg[headers.SL_CLIENT_IP] = "209.85.220.41"

    class Envelope:
        mail_from = "someone@example.com"

    assert spf_pass(Envelope(), mailbox, user, alias, "contact@example.com", msg)
    assert seen == ["198.51.100.7"]


def test_spf_refuses_ip_not_allowed(monkeypatch):
    user = create_new_user()
    alias = Alias.create_new_random(user)
    mailbox = Mailbox.get(user.default_mailbox_id)
    monkeypatch.setattr(
        email_utils.spf, "check2", lambda i, s, h: ("softfail", 250, "")
    )
    msg = EmailMessage()
    msg[headers.SL_CLIENT_IP] = "198.51.100.7"
    msg[headers.SUBJECT] = "hello"

    class Envelope:
        mail_from = "someone@example.com"

    assert not spf_pass(Envelope(), mailbox, user, alias, "contact@example.com", msg)

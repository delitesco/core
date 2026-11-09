import os
import stat
from io import BytesIO

import arrow
import pytest

from app import config, file_storage
from app.db import Session
from app.email_utils import notify_user_by_placeholder_email
from app.mailbox_utils import (
    TooManyVerificationEmails,
    check_verification_email_limit,
    normalize_email_for_limit,
)
from app.models import AccountState, Alias, Notification, RefusedEmail
from cron import delete_refused_emails
from tests.utils import create_new_user, login, random_token


@pytest.mark.parametrize(
    "address, inbox",
    [
        ("User+news@Example.com", "user@example.com"),
        ("u.s.e.r+x@gmail.com", "user@gmail.com"),
        ("u.ser@googlemail.com", "user@gmail.com"),
        ("first.last@example.com", "first.last@example.com"),
    ],
)
def test_normalize_email_for_limit(address, inbox):
    assert normalize_email_for_limit(address) == inbox


def test_verification_limit_counts_tagged_addresses_together(monkeypatch):
    monkeypatch.setattr(config, "MAILBOX_VERIFICATION_EMAIL_LIMITS", [(2, 3600)])
    base = random_token(10)
    check_verification_email_limit(f"{base}+1@example.com")
    check_verification_email_limit(f"{base}+2@example.com")
    with pytest.raises(TooManyVerificationEmails):
        check_verification_email_limit(f"{base}+3@example.com")


@pytest.mark.parametrize("state", [AccountState.paused, AccountState.grace])
def test_inactive_account_cannot_create_resources(flask_client, state):
    user = login(flask_client)
    alias = Alias.create_new_random(user)
    user.account_state = state
    Session.commit()

    r = flask_client.post("/api/mailboxes", json={"email": "new@example.com"})
    assert r.status_code == 400
    r = flask_client.post("/api/custom_domains", json={"domain": "example-new.com"})
    assert r.status_code == 400
    r = flask_client.post("/api/directories", json={"name": "newdir", "mailbox_ids": []})
    assert r.status_code == 400
    r = flask_client.post(
        f"/api/aliases/{alias.id}/contacts", json={"contact": "c@example.com"}
    )
    assert r.status_code in (400, 403)


def test_notifications_are_capped(monkeypatch):
    monkeypatch.setattr(config, "MAX_NOTIFICATIONS_PER_USER", 3)
    user = create_new_user()
    for i in range(5):
        notify_user_by_placeholder_email(user.email, f"subject {i}", "text")
    titles = [
        n.title
        for n in Notification.filter_by(user_id=user.id).order_by(Notification.id)
    ]
    assert titles == ["subject 2", "subject 3", "subject 4"]


def test_stored_emails_are_private():
    path = f"test/{random_token(10)}.eml"
    file_storage.upload_email_from_bytesio(path, BytesIO(b"secret"))
    full_path = os.path.join(config.UPLOAD_DIR, path)
    assert stat.S_IMODE(os.stat(full_path).st_mode) == 0o600
    file_storage.delete(path)


def test_overdue_refused_emails_are_deleted():
    user = create_new_user()
    path = f"refused/{random_token(10)}.eml"
    file_storage.upload_email_from_bytesio(path, BytesIO(b"refused"))
    refused = RefusedEmail.create(
        user_id=user.id,
        full_report_path=path,
        delete_at=arrow.now().shift(days=-3),
        commit=True,
    )
    delete_refused_emails()
    assert RefusedEmail.get(refused.id).deleted
    assert not os.path.exists(os.path.join(config.UPLOAD_DIR, path))

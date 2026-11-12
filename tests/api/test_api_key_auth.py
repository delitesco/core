from flask import url_for

from app.db import Session
from app.email_utils import send_email
from app.mail_sender import mail_sender
from app.models import ApiKey
from tests.utils import create_new_user


def test_missing_api_key(flask_client):
    r = flask_client.get(url_for("api.user_info"))
    assert r.status_code == 401


def test_unknown_api_key(flask_client):
    r = flask_client.get(
        url_for("api.user_info"), headers={"Authentication": "not-a-key"}
    )
    assert r.status_code == 401


def test_disabled_user(flask_client):
    user = create_new_user()
    api_key = ApiKey.create(user.id, name="test")
    user.disabled = True
    Session.commit()
    r = flask_client.get(
        url_for("api.user_info"), headers={"Authentication": api_key.code}
    )
    assert r.status_code == 403


def test_auth_endpoints_are_gone(flask_client):
    for path in ["/api/auth/login", "/api/auth/register", "/api/auth/mfa"]:
        assert flask_client.post(path, json={}).status_code == 404
    assert flask_client.get("/api/logout").status_code == 404
    assert flask_client.post("/api/api_key", json={}).status_code == 404


@mail_sender.store_emails_test_decorator
def test_emails_to_invalid_domain_are_not_sent(flask_client):
    send_email("someone@users.invalid", "subject", "body")
    assert len(mail_sender.get_stored_emails()) == 0
    send_email("someone@mailbox.lan", "subject", "body")
    assert len(mail_sender.get_stored_emails()) == 1


def test_failed_api_key_attempts_are_limited(flask_client, monkeypatch):
    from app.api import base

    monkeypatch.setattr(base, "MAX_FAILED_API_KEY_ATTEMPTS", 3)
    address = {"REMOTE_ADDR": "198.51.100.77"}
    codes = [
        flask_client.get(
            "/api/user_info",
            headers={"Authentication": f"wrong-{i}"},
            environ_base=address,
        ).status_code
        for i in range(5)
    ]
    assert codes[:3] == [401, 401, 401]
    assert codes[3:] == [429, 429]

    # a valid key from the same address still works
    user = create_new_user()
    key = ApiKey.create(user.id, name="ok", commit=True)
    r = flask_client.get(
        "/api/user_info", headers={"Authentication": key.code}, environ_base=address
    )
    assert r.status_code == 200


def test_api_key_is_stored_hashed(flask_client):
    user = create_new_user()
    key = ApiKey.create(user.id, name="hashed", commit=True)
    assert ApiKey.get(key.id).code_hash == ApiKey.hash_code(key.code)
    assert ApiKey.get_by(code_hash=key.code) is None

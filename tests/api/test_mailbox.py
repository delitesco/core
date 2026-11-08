import pytest
from flask import url_for

from app import config
from app.config import get_abs_path
from app.db import Session
from app.dns_utils import InMemoryDNSClient, set_global_dns_client
from app.models import Mailbox, MailboxActivation
from tests.utils import login, random_token


def test_create_mailbox_valid(flask_client):
    login(flask_client)

    r = flask_client.post(
        "/api/mailboxes",
        json={"email": "mailbox@gmail.com"},
    )

    assert r.status_code == 201

    assert r.json["email"] == "mailbox@gmail.com"
    assert r.json["verified"] is False
    assert r.json["id"] > 0
    assert r.json["default"] is False
    assert r.json["nb_alias"] == 0


def test_create_mailbox_invalid_email(flask_client):
    login(flask_client)
    r = flask_client.post(
        "/api/mailboxes",
        json={"email": "gmail.com"},  # not an email address
    )

    assert r.status_code == 400
    assert r.json == {"error": "Invalid email"}


def test_create_mailbox_empty_payload(flask_client):
    login(flask_client)
    r = flask_client.post(
        "/api/mailboxes",
        json={},
    )

    assert r.status_code == 400
    assert r.json == {"error": "Invalid email"}


def test_create_mailbox_empty_email(flask_client):
    login(flask_client)
    r = flask_client.post(
        "/api/mailboxes",
        json={"email": ""},
    )

    assert r.status_code == 400
    assert r.json == {"error": "Invalid email"}


def test_delete_mailbox(flask_client):
    user = login(flask_client)

    # create a mailbox
    mb = Mailbox.create(user_id=user.id, email="mb@gmail.com")
    Session.commit()

    r = flask_client.delete(
        f"/api/mailboxes/{mb.id}",
    )

    assert r.status_code == 200


def test_delete_default_mailbox(flask_client):
    user = login(flask_client)

    # assert user cannot delete the default mailbox
    r = flask_client.delete(
        url_for("api.delete_mailbox", mailbox_id=user.default_mailbox_id),
    )

    assert r.status_code == 400


def test_set_mailbox_as_default(flask_client):
    user = login(flask_client)

    mb = Mailbox.create(
        user_id=user.id, email="mb@gmail.com", verified=True, commit=True
    )
    assert user.default_mailbox_id != mb.id

    r = flask_client.put(
        f"/api/mailboxes/{mb.id}",
        json={"default": True},
    )

    assert r.status_code == 200
    assert user.default_mailbox_id == mb.id

    # <<< Cannot set an unverified mailbox as default >>>
    mb.verified = False
    Session.commit()

    r = flask_client.put(
        f"/api/mailboxes/{mb.id}",
        json={"default": True},
    )

    assert r.status_code == 400
    assert r.json == {"error": "Unverified mailbox cannot be used as default mailbox"}


def test_update_mailbox_email(flask_client):
    user = login(flask_client)

    # create a mailbox
    mb = Mailbox.create(user_id=user.id, email="mb@gmail.com")
    Session.commit()

    r = flask_client.put(
        f"/api/mailboxes/{mb.id}",
        json={"email": "new-email@gmail.com"},
    )

    assert r.status_code == 200

    mb = Mailbox.get(mb.id)
    assert mb.new_email == "new-email@gmail.com"


def test_cancel_mailbox_email_change(flask_client):
    user = login(flask_client)

    # create a mailbox
    mb = Mailbox.create(user_id=user.id, email="mb@gmail.com")
    Session.commit()

    # update mailbox email
    r = flask_client.put(
        f"/api/mailboxes/{mb.id}",
        json={"email": "new-email@gmail.com"},
    )
    assert r.status_code == 200

    mb = Mailbox.get(mb.id)
    assert mb.new_email == "new-email@gmail.com"

    # cancel mailbox email change
    r = flask_client.put(
        url_for("api.delete_mailbox", mailbox_id=mb.id),
        json={"cancel_email_change": True},
    )
    assert r.status_code == 200

    mb = Mailbox.get(mb.id)
    assert mb.new_email is None


def test_get_mailboxes(flask_client):
    user = login(flask_client)

    Mailbox.create(user_id=user.id, email="m1@example.com", verified=True)
    Mailbox.create(user_id=user.id, email="m2@example.com", verified=False)
    Session.commit()

    r = flask_client.get(
        "/api/mailboxes",
    )
    assert r.status_code == 200
    # m2@example.com is not returned as it's not verified
    assert len(r.json["mailboxes"]) == 2

    for mb in r.json["mailboxes"]:
        assert "email" in mb
        assert "id" in mb
        assert "default" in mb
        assert "creation_timestamp" in mb
        assert "nb_alias" in mb
        assert "verified" in mb


def test_get_mailboxes_v2(flask_client):
    user = login(flask_client)

    Mailbox.create(user_id=user.id, email="m1@example.com", verified=True)
    Mailbox.create(user_id=user.id, email="m2@example.com", verified=False)
    Session.commit()

    r = flask_client.get(
        "/api/v2/mailboxes",
    )
    assert r.status_code == 200
    # 3 mailboxes: the default, m1 and m2
    assert len(r.json["mailboxes"]) == 3

    for mb in r.json["mailboxes"]:
        assert "email" in mb
        assert "id" in mb
        assert "default" in mb
        assert "creation_timestamp" in mb
        assert "nb_alias" in mb
        assert "verified" in mb


@pytest.fixture(autouse=True)
def offline_dns():
    """Mailbox domains need MX records: answer DNS from memory so the tests don't
    depend on the network."""
    dns_client = InMemoryDNSClient()
    dns_client.set_mx_records("gmail.com", {10: ["gmail-smtp-in.l.google.com."]})
    set_global_dns_client(dns_client)
    yield
    set_global_dns_client(None)


def _mailbox_address() -> str:
    # mailbox domains must have MX records
    return f"mb_{random_token(10)}@gmail.com"


def _create_unverified_mailbox(flask_client) -> int:
    r = flask_client.post("/api/mailboxes", json={"email": _mailbox_address()})
    assert r.status_code == 201
    return r.json["id"]


def _activation_code(mailbox_id: int) -> str:
    return (
        MailboxActivation.filter(MailboxActivation.mailbox_id == mailbox_id)
        .order_by(MailboxActivation.created_at.desc())
        .first()
        .code
    )


def test_verify_mailbox(flask_client):
    login(flask_client)
    mailbox_id = _create_unverified_mailbox(flask_client)

    r = flask_client.post(
        f"/api/mailboxes/{mailbox_id}/verify",
        json={"code": _activation_code(mailbox_id)},
    )

    assert r.status_code == 200
    assert r.json["id"] == mailbox_id
    assert r.json["verified"] is True
    assert Mailbox.get(mailbox_id).verified


def test_verify_mailbox_wrong_code(flask_client):
    login(flask_client)
    mailbox_id = _create_unverified_mailbox(flask_client)

    for payload in [{"code": "wrong"}, {}, {"code": 123}]:
        r = flask_client.post(f"/api/mailboxes/{mailbox_id}/verify", json=payload)
        assert r.status_code == 400
    assert not Mailbox.get(mailbox_id).verified


def test_verify_mailbox_code_is_burnt_after_3_wrong_attempts(flask_client):
    login(flask_client)
    mailbox_id = _create_unverified_mailbox(flask_client)
    code = _activation_code(mailbox_id)

    for _ in range(3):
        r = flask_client.post(f"/api/mailboxes/{mailbox_id}/verify", json={"code": "x"})
        assert r.status_code == 400
    r = flask_client.post(f"/api/mailboxes/{mailbox_id}/verify", json={"code": code})
    assert r.status_code == 400
    assert not Mailbox.get(mailbox_id).verified


def test_cannot_verify_mailbox_of_another_user(flask_client):
    login(flask_client)
    mailbox_id = _create_unverified_mailbox(flask_client)
    code = _activation_code(mailbox_id)

    login(flask_client)  # another user
    r = flask_client.post(f"/api/mailboxes/{mailbox_id}/verify", json={"code": code})

    assert r.status_code == 400
    assert not Mailbox.get(mailbox_id).verified


def test_verify_mailbox_email_change(flask_client):
    user = login(flask_client)
    mailbox = Mailbox.create(user_id=user.id, email=_mailbox_address(), verified=True)
    Session.commit()
    new_email = _mailbox_address()

    r = flask_client.put(f"/api/mailboxes/{mailbox.id}", json={"email": new_email})
    assert r.status_code == 200
    assert Mailbox.get(mailbox.id).new_email == new_email

    r = flask_client.post(
        f"/api/mailboxes/{mailbox.id}/verify",
        json={"code": _activation_code(mailbox.id)},
    )
    assert r.status_code == 200
    assert r.json["email"] == new_email
    assert Mailbox.get(mailbox.id).new_email is None


def test_verification_emails_are_limited_per_address(flask_client):
    """Several users cannot flood one address with verification emails."""
    target = _mailbox_address()
    max_hits = config.MAILBOX_VERIFICATION_EMAIL_LIMITS[0][0]
    for _ in range(max_hits):
        login(flask_client)  # a different user each time
        r = flask_client.post("/api/mailboxes", json={"email": target})
        assert r.status_code == 201

    login(flask_client)
    r = flask_client.post("/api/mailboxes", json={"email": target})
    assert r.status_code == 429
    assert Mailbox.filter_by(email=target).count() == max_hits


def _own_verified_mailbox(flask_client):
    user = login(flask_client)
    mailbox = Mailbox.create(user_id=user.id, email=_mailbox_address(), verified=True)
    Session.commit()
    return mailbox


def test_mailbox_pgp_key(flask_client):
    mailbox = _own_verified_mailbox(flask_client)
    public_key = open(get_abs_path("local_data/public-pgp.asc")).read()

    r = flask_client.put(
        f"/api/mailboxes/{mailbox.id}", json={"pgp_public_key": public_key}
    )
    assert r.status_code == 200
    assert Mailbox.get(mailbox.id).pgp_finger_print
    assert Mailbox.get(mailbox.id).pgp_enabled()

    r = flask_client.put(f"/api/mailboxes/{mailbox.id}", json={"disable_pgp": True})
    assert r.status_code == 200
    assert not Mailbox.get(mailbox.id).pgp_enabled()

    r = flask_client.get("/api/v2/mailboxes")
    listed = next(m for m in r.json["mailboxes"] if m["id"] == mailbox.id)
    assert listed["pgp_fingerprint"] == Mailbox.get(mailbox.id).pgp_finger_print
    assert listed["disable_pgp"] is True

    r = flask_client.put(f"/api/mailboxes/{mailbox.id}", json={"pgp_public_key": None})
    assert r.status_code == 200
    mailbox = Mailbox.get(mailbox.id)
    assert mailbox.pgp_finger_print is None and mailbox.pgp_public_key is None


def test_mailbox_invalid_pgp_key(flask_client):
    mailbox = _own_verified_mailbox(flask_client)
    r = flask_client.put(
        f"/api/mailboxes/{mailbox.id}", json={"pgp_public_key": "not a key"}
    )
    assert r.status_code == 400
    assert Mailbox.get(mailbox.id).pgp_finger_print is None


def test_mailbox_generic_subject(flask_client):
    mailbox = _own_verified_mailbox(flask_client)
    r = flask_client.put(
        f"/api/mailboxes/{mailbox.id}", json={"generic_subject": "New email"}
    )
    assert r.status_code == 200
    assert Mailbox.get(mailbox.id).generic_subject == "New email"

    r = flask_client.put(
        f"/api/mailboxes/{mailbox.id}", json={"generic_subject": "x" * 79}
    )
    assert r.status_code == 400

    r = flask_client.put(f"/api/mailboxes/{mailbox.id}", json={"generic_subject": None})
    assert r.status_code == 200
    assert Mailbox.get(mailbox.id).generic_subject is None


def test_mailbox_dict_shows_pending_new_email(flask_client):
    mailbox = _own_verified_mailbox(flask_client)
    new_email = _mailbox_address()
    r = flask_client.put(f"/api/mailboxes/{mailbox.id}", json={"email": new_email})
    assert r.status_code == 200

    r = flask_client.get("/api/v2/mailboxes")
    listed = next(m for m in r.json["mailboxes"] if m["id"] == mailbox.id)
    assert listed["email"] == mailbox.email
    assert listed["new_email"] == new_email

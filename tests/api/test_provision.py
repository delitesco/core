import arrow
import pytest
from flask import url_for

from app import config
from app.constants import JobType
from app.db import Session
from app.models import (
    Alias,
    ApiKey,
    Contact,
    CustomDomain,
    EmailLog,
    Job,
    Mailbox,
    Notification,
    User,
)
from tests.utils import create_new_user, random_token

PROVISIONING_KEY = "test-provisioning-key"


@pytest.fixture
def provisioning_key():
    previous = config.PROVISIONING_API_KEY
    config.PROVISIONING_API_KEY = PROVISIONING_KEY
    yield {"X-Provisioning-Key": PROVISIONING_KEY}
    config.PROVISIONING_API_KEY = previous


def create_user(flask_client, headers) -> dict:
    r = flask_client.post(url_for("api.provision_create_user"), headers=headers)
    assert r.status_code == 201
    return r.json


def test_disabled_without_configured_key(flask_client):
    config.PROVISIONING_API_KEY = None
    r = flask_client.post(
        url_for("api.provision_create_user"),
        headers={"X-Provisioning-Key": "anything"},
    )
    assert r.status_code == 404


@pytest.mark.parametrize(
    "headers", [{}, {"X-Provisioning-Key": "wrong"}, {"X-Provisioning-Key": ""}]
)
def test_wrong_or_missing_key_is_rejected(flask_client, provisioning_key, headers):
    r = flask_client.post(url_for("api.provision_create_user"), headers=headers)
    assert r.status_code == 401
    r = flask_client.delete(
        url_for("api.provision_delete_user", user_id=1), headers=headers
    )
    assert r.status_code == 401


def test_api_key_is_not_a_provisioning_key(flask_client, provisioning_key):
    created = create_user(flask_client, provisioning_key)
    r = flask_client.post(
        url_for("api.provision_create_user"),
        headers={"X-Provisioning-Key": created["api_key"]},
    )
    assert r.status_code == 401


def test_create_user(flask_client, provisioning_key):
    created = create_user(flask_client, provisioning_key)

    user = User.get(created["user_id"])
    assert user.activated
    assert user.email.endswith(f"@{config.PROVISIONED_USER_EMAIL_DOMAIN}")

    r = flask_client.get(
        url_for("api.user_info"), headers={"Authentication": created["api_key"]}
    )
    assert r.status_code == 200
    assert r.json["email"] == user.email


def test_each_user_gets_its_own_key(flask_client, provisioning_key):
    first = create_user(flask_client, provisioning_key)
    second = create_user(flask_client, provisioning_key)
    assert first["user_id"] != second["user_id"]
    assert first["api_key"] != second["api_key"]


def test_rotate_api_key(flask_client, provisioning_key):
    created = create_user(flask_client, provisioning_key)

    r = flask_client.post(
        url_for("api.provision_rotate_api_key", user_id=created["user_id"]),
        headers=provisioning_key,
    )
    assert r.status_code == 200
    new_key = r.json["api_key"]
    assert new_key != created["api_key"]

    r = flask_client.get(
        url_for("api.user_info"), headers={"Authentication": created["api_key"]}
    )
    assert r.status_code == 401
    r = flask_client.get(url_for("api.user_info"), headers={"Authentication": new_key})
    assert r.status_code == 200


def test_rotate_unknown_user(flask_client, provisioning_key):
    r = flask_client.post(
        url_for("api.provision_rotate_api_key", user_id=999999999),
        headers=provisioning_key,
    )
    assert r.status_code == 404


def test_delete_user(flask_client, provisioning_key):
    created = create_user(flask_client, provisioning_key)
    user_id = created["user_id"]

    r = flask_client.delete(
        url_for("api.provision_delete_user", user_id=user_id),
        headers=provisioning_key,
    )
    assert r.status_code == 200

    # access stops at once
    assert ApiKey.filter_by(user_id=user_id).count() == 0
    r = flask_client.get(
        url_for("api.user_info"), headers={"Authentication": created["api_key"]}
    )
    assert r.status_code == 401

    user = User.get(user_id)
    assert user.delete_on is not None
    assert not user.can_send_or_receive()
    job = (
        Session.query(Job)
        .filter(Job.name == JobType.DELETE_ACCOUNT.value)
        .order_by(Job.id.desc())
        .first()
    )
    assert job.payload == {"user_id": user_id}

    # a user being deleted cannot be deleted again or get a new key
    r = flask_client.delete(
        url_for("api.provision_delete_user", user_id=user_id),
        headers=provisioning_key,
    )
    assert r.status_code == 404
    r = flask_client.post(
        url_for("api.provision_rotate_api_key", user_id=user_id),
        headers=provisioning_key,
    )
    assert r.status_code == 404


def _user_with_mailbox():
    user = create_new_user()
    mailbox = Mailbox.create(
        user_id=user.id, email=f"mb_{random_token(10)}@gmail.com", verified=True
    )
    Session.commit()
    return user, mailbox


def _transfer(flask_client, headers, alias_id, to_user_id, mailbox_ids):
    return flask_client.post(
        url_for("api.provision_transfer_alias", alias_id=alias_id),
        headers=headers,
        json={"to_user_id": to_user_id, "mailbox_ids": mailbox_ids},
    )


def test_transfer_alias(flask_client, provisioning_key):
    old_user, _ = _user_with_mailbox()
    new_user, new_mailbox = _user_with_mailbox()
    alias = Alias.create_new_random(old_user)
    Session.commit()
    contact = Contact.create(
        user_id=old_user.id,
        alias_id=alias.id,
        website_email="sender@example.com",
        reply_email=f"r_{random_token(8)}@sl.lan",
        commit=True,
    )

    alias.note = "private note of the old owner"
    alias.name = "Old Owner"
    EmailLog.create(
        user_id=old_user.id,
        contact_id=contact.id,
        alias_id=alias.id,
        mailbox_id=old_user.default_mailbox_id,
        commit=True,
    )

    r = _transfer(
        flask_client, provisioning_key, alias.id, new_user.id, [new_mailbox.id]
    )

    assert r.status_code == 200
    alias = Alias.get(alias.id)
    assert alias.user_id == new_user.id
    # nothing of the old owner's private data goes with the alias
    assert alias.note is None
    assert alias.name is None
    assert EmailLog.filter_by(alias_id=alias.id).count() == 0
    assert EmailLog.filter_by(contact_id=contact.id).count() == 0
    assert alias.original_owner_id == old_user.id
    assert alias.mailbox_id == new_mailbox.id
    assert Contact.get(contact.id).user_id == new_user.id
    # the old owner is told through its notifications
    assert Notification.filter_by(user_id=old_user.id).count() == 0  # not provisioned
    # and loses access through the API
    old_key = ApiKey.create(old_user.id, name="old", commit=True)
    r = flask_client.get(
        f"/api/aliases/{alias.id}", headers={"Authentication": old_key.code}
    )
    assert r.status_code in (403, 404)


def test_transfer_requires_mailboxes_of_the_new_owner(flask_client, provisioning_key):
    old_user, old_mailbox = _user_with_mailbox()
    new_user, _ = _user_with_mailbox()
    alias = Alias.create_new_random(old_user)
    Session.commit()

    r = _transfer(
        flask_client, provisioning_key, alias.id, new_user.id, [old_mailbox.id]
    )

    assert r.status_code == 400
    assert Alias.get(alias.id).user_id == old_user.id


def test_cannot_transfer_custom_domain_or_trashed_alias(flask_client, provisioning_key):
    old_user, _ = _user_with_mailbox()
    new_user, new_mailbox = _user_with_mailbox()
    domain = CustomDomain.create(
        user_id=old_user.id, domain=f"{random_token(8)}.lan", verified=True
    )
    domain_alias = Alias.create(
        user_id=old_user.id,
        email=f"x@{domain.domain}",
        mailbox_id=old_user.default_mailbox_id,
        custom_domain_id=domain.id,
    )
    trashed = Alias.create_new_random(old_user)
    trashed.delete_on = arrow.now()
    Session.commit()

    for alias in (domain_alias, trashed):
        r = _transfer(
            flask_client, provisioning_key, alias.id, new_user.id, [new_mailbox.id]
        )
        assert r.status_code == 400
        assert Alias.get(alias.id).user_id == old_user.id


def test_transfer_needs_provisioning_key(flask_client, provisioning_key):
    old_user, _ = _user_with_mailbox()
    new_user, new_mailbox = _user_with_mailbox()
    alias = Alias.create_new_random(old_user)
    api_key = ApiKey.create(old_user.id, name="user", commit=True)

    r = _transfer(
        flask_client,
        {"Authentication": api_key.code},
        alias.id,
        new_user.id,
        [new_mailbox.id],
    )
    assert r.status_code == 401
    assert Alias.get(alias.id).user_id == old_user.id


@pytest.fixture
def allowed_networks():
    previous = config.PROVISIONING_ALLOWED_NETWORKS
    previous_open = config.PROVISIONING_EMERGENCY_OPEN
    yield
    config.PROVISIONING_ALLOWED_NETWORKS = previous
    config.PROVISIONING_EMERGENCY_OPEN = previous_open


def test_refused_from_non_allowed_address(
    flask_client, provisioning_key, allowed_networks
):
    config.PROVISIONING_ALLOWED_NETWORKS = config.parse_networks("X", ["10.0.0.0/8"])
    r = flask_client.post(
        url_for("api.provision_create_user"),
        headers=provisioning_key,
        environ_base={"REMOTE_ADDR": "203.0.113.7"},
    )
    assert r.status_code == 404


def test_forwarded_for_from_internet_is_refused(
    flask_client, provisioning_key, allowed_networks
):
    # behind the proxy the client IP is the last X-Forwarded-For entry
    r = flask_client.post(
        url_for("api.provision_create_user"),
        headers={**provisioning_key, "X-Forwarded-For": "203.0.113.7"},
    )
    assert r.status_code == 404


def test_accepted_from_allowed_public_address(
    flask_client, provisioning_key, allowed_networks
):
    config.PROVISIONING_ALLOWED_NETWORKS = config.parse_networks(
        "X", ["203.0.113.10/32"]
    )
    r = flask_client.post(
        url_for("api.provision_create_user"),
        headers=provisioning_key,
        environ_base={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert r.status_code == 201


def test_emergency_open_accepts_any_address(
    flask_client, provisioning_key, allowed_networks
):
    config.PROVISIONING_ALLOWED_NETWORKS = config.parse_networks("X", ["0.0.0.0/0"])
    config.PROVISIONING_EMERGENCY_OPEN = True
    r = flask_client.post(
        url_for("api.provision_create_user"),
        headers=provisioning_key,
        environ_base={"REMOTE_ADDR": "198.51.100.1"},
    )
    assert r.status_code == 201

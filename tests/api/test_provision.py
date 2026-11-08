import pytest
from flask import url_for

from app import config
from app.constants import JobType
from app.db import Session
from app.models import ApiKey, Job, User

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

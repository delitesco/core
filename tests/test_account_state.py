"""Subscription stages set by the frontend backend: active, grace, paused."""

import pytest
from aiosmtpd.smtp import Envelope
from flask import url_for

import email_handler
from app import config
from app.db import Session
from app.email import status
from app.models import AccountState, Alias, ApiKey, Contact, CustomDomain, User
from tests.utils import create_new_user, load_eml_file, random_token


def _user(state: AccountState) -> User:
    user = create_new_user()
    user.account_state = state
    Session.commit()
    return user


def _forward(alias_email: str):
    msg = load_eml_file("no_spamd_header.eml", {"alias_email": alias_email})
    envelope = Envelope()
    envelope.mail_from = "sender@example.com"
    envelope.rcpt_tos = [alias_email]
    return email_handler.handle_forward(envelope, msg, alias_email)


def _reply(user: User):
    alias = Alias.filter_by(user_id=user.id).first()
    contact = Contact.create(
        user_id=user.id,
        alias_id=alias.id,
        website_email="someone@example.com",
        reply_email=f"ra_{random_token(10)}@{config.EMAIL_DOMAIN}",
        commit=True,
    )
    msg = load_eml_file("no_spamd_header.eml", {"alias_email": contact.reply_email})
    envelope = Envelope()
    envelope.mail_from = user.default_mailbox.email
    envelope.rcpt_tos = [contact.reply_email]
    return email_handler.handle_reply(envelope, msg, contact.reply_email)


def test_paused_user_mail_is_deferred(flask_client):
    user = _user(AccountState.paused)
    alias = Alias.filter_by(user_id=user.id).first()
    assert _forward(alias.email) == [(False, status.E410)]
    assert status.E410.startswith("4")


@pytest.mark.parametrize("state", [AccountState.active, AccountState.grace])
def test_active_and_grace_users_get_their_mail(flask_client, state):
    user = _user(state)
    alias = Alias.filter_by(user_id=user.id).first()
    results = _forward(alias.email)
    assert all(success for success, _ in results), results


def test_paused_user_replies_are_deferred(flask_client):
    user = _user(AccountState.paused)
    assert _reply(user) == (False, status.E410)


def test_grace_user_replies_are_refused(flask_client):
    user = _user(AccountState.grace)
    assert _reply(user) == (False, status.E527)
    assert status.E527.startswith("5")


@pytest.mark.parametrize("state", [AccountState.grace, AccountState.paused])
def test_no_catch_all_alias_outside_active(flask_client, state):
    user = _user(state)
    domain = CustomDomain.create(
        user_id=user.id,
        domain=f"{random_token(8)}.lan",
        verified=True,
        ownership_verified=True,
        catch_all=True,
        commit=True,
    )
    address = f"new@{domain.domain}"
    assert _forward(address) == [(False, status.E411)]
    assert Alias.get_by(email=address) is None


def test_catch_all_still_works_for_active_user(flask_client):
    user = _user(AccountState.active)
    domain = CustomDomain.create(
        user_id=user.id,
        domain=f"{random_token(8)}.lan",
        verified=True,
        ownership_verified=True,
        catch_all=True,
        commit=True,
    )
    address = f"new@{domain.domain}"
    _forward(address)
    assert Alias.get_by(email=address) is not None


@pytest.mark.parametrize("state", [AccountState.grace, AccountState.paused])
def test_no_new_alias_through_api_outside_active(flask_client, state):
    user = _user(state)
    api_key = ApiKey.create(user.id, name="test", commit=True)
    headers = {"Authentication": api_key.code}

    r = flask_client.get("/api/v5/alias/options", headers=headers)
    assert r.json["can_create"] is False
    r = flask_client.post(url_for("api.new_random_alias"), headers=headers)
    assert r.status_code == 400

    # existing aliases can still be managed
    alias = Alias.filter_by(user_id=user.id).first()
    r = flask_client.post(f"/api/aliases/{alias.id}/toggle", headers=headers)
    assert r.status_code == 200

    r = flask_client.get("/api/user_info", headers=headers)
    assert r.json["account_state"] == state.name


def test_provisioning_sets_the_state(flask_client):
    previous = config.PROVISIONING_API_KEY
    config.PROVISIONING_API_KEY = "key"
    headers = {"X-Provisioning-Key": "key"}
    try:
        r = flask_client.post(
            url_for("api.provision_create_user"), headers=headers, json={}
        )
        assert r.status_code == 201
        assert r.json["state"] == "active"
        user_id = r.json["user_id"]

        for state in ("grace", "paused", "active"):
            r = flask_client.patch(
                url_for("api.provision_update_user", user_id=user_id),
                headers=headers,
                json={"state": state},
            )
            assert r.status_code == 200
            assert User.get(user_id).account_state == AccountState[state]

        r = flask_client.patch(
            url_for("api.provision_update_user", user_id=user_id),
            headers=headers,
            json={"state": "frozen"},
        )
        assert r.status_code == 400

        r = flask_client.post(
            url_for("api.provision_create_user"),
            headers=headers,
            json={"state": "paused"},
        )
        assert r.status_code == 201
        assert User.get(r.json["user_id"]).account_state == AccountState.paused
    finally:
        config.PROVISIONING_API_KEY = previous

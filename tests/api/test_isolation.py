"""Cross-user isolation of the endpoints the frontend backend uses.

Each account of the frontend is a separate user with its own API key: one user must
never read, change or use another user's aliases and mailboxes.
"""

from app.alias_suffix import signer
from app.config import EMAIL_DOMAIN
from app.constants import JobType
from app.db import Session
from app.models import Alias, AliasMailbox, ApiKey, CustomDomain, Job, Mailbox
from app.utils import random_word
from tests.utils import create_new_user, random_token


def _user_with_key():
    user = create_new_user()
    api_key = ApiKey.create(user.id, name="test")
    mailbox = Mailbox.create(
        user_id=user.id, email=f"mb_{random_token(10)}@gmail.com", verified=True
    )
    alias = Alias.create_new_random(user)
    Session.commit()
    return user, {"Authentication": api_key.code}, mailbox, alias


def _signed_sl_suffix() -> str:
    return signer.sign(f".{random_word()}@{EMAIL_DOMAIN}").decode()


def test_cannot_read_or_change_other_users_alias(flask_client):
    _, _, _, victim_alias = _user_with_key()
    _, attacker, _, _ = _user_with_key()
    note_before = victim_alias.note
    enabled_before = victim_alias.enabled

    r = flask_client.get(f"/api/aliases/{victim_alias.id}", headers=attacker)
    assert r.status_code in (403, 404)

    r = flask_client.patch(
        f"/api/aliases/{victim_alias.id}", headers=attacker, json={"note": "pwned"}
    )
    assert r.status_code in (403, 404)

    r = flask_client.post(f"/api/aliases/{victim_alias.id}/toggle", headers=attacker)
    assert r.status_code in (403, 404)

    r = flask_client.delete(f"/api/aliases/{victim_alias.id}", headers=attacker)
    assert r.status_code in (403, 404)

    alias = Alias.get(victim_alias.id)
    assert alias is not None
    assert alias.note == note_before
    assert alias.enabled == enabled_before


def test_cannot_point_own_alias_to_other_users_mailbox(flask_client):
    _, _, victim_mailbox, _ = _user_with_key()
    _, attacker, _, attacker_alias = _user_with_key()

    r = flask_client.patch(
        f"/api/aliases/{attacker_alias.id}",
        headers=attacker,
        json={"mailbox_ids": [victim_mailbox.id]},
    )
    assert r.status_code >= 400
    assert (
        AliasMailbox.get_by(alias_id=attacker_alias.id, mailbox_id=victim_mailbox.id)
        is None
    )
    assert Alias.get(attacker_alias.id).mailbox_id != victim_mailbox.id


def test_cannot_create_alias_on_other_users_mailbox(flask_client):
    _, _, victim_mailbox, _ = _user_with_key()
    attacker_user, attacker, _, _ = _user_with_key()
    nb_aliases = Alias.filter_by(user_id=attacker_user.id).count()

    r = flask_client.post(
        "/api/v3/alias/custom/new",
        headers=attacker,
        json={
            "alias_prefix": f"p{random_token(6)}",
            "signed_suffix": _signed_sl_suffix(),
            "mailbox_ids": [victim_mailbox.id],
        },
    )
    assert r.status_code >= 400
    assert Alias.filter_by(user_id=attacker_user.id).count() == nb_aliases


def test_cannot_use_other_users_custom_domain_suffix(flask_client):
    victim_user, victim, _, _ = _user_with_key()
    domain = f"{random_token(10)}.lan"
    CustomDomain.create(
        user_id=victim_user.id, domain=domain, verified=True, ownership_verified=True
    )
    Session.commit()

    r = flask_client.get("/api/v5/alias/options", headers=victim)
    assert r.status_code == 200
    custom = [s for s in r.json["suffixes"] if s["suffix"] == f"@{domain}"]
    assert custom, "the owner sees its custom domain"

    attacker_user, attacker, attacker_mailbox, _ = _user_with_key()
    r = flask_client.get("/api/v5/alias/options", headers=attacker)
    assert all(not s["suffix"].endswith(f"@{domain}") for s in r.json["suffixes"])

    r = flask_client.post(
        "/api/v3/alias/custom/new",
        headers=attacker,
        json={
            "alias_prefix": "stolen",
            "signed_suffix": custom[0]["signed_suffix"],
            "mailbox_ids": [attacker_mailbox.id],
        },
    )
    assert r.status_code >= 400
    assert Alias.get_by(email=f"stolen@{domain}") is None


def test_mailbox_list_only_has_own_mailboxes(flask_client):
    _, _, victim_mailbox, _ = _user_with_key()
    attacker_user, attacker, attacker_mailbox, _ = _user_with_key()

    r = flask_client.get("/api/v2/mailboxes", headers=attacker)
    assert r.status_code == 200
    ids = {m["id"] for m in r.json["mailboxes"]}
    assert attacker_mailbox.id in ids
    assert victim_mailbox.id not in ids
    assert all(
        Mailbox.get(mailbox_id).user_id == attacker_user.id for mailbox_id in ids
    )


def test_cannot_delete_other_users_mailbox(flask_client):
    _, _, victim_mailbox, _ = _user_with_key()
    _, attacker, _, _ = _user_with_key()

    r = flask_client.delete(
        f"/api/mailboxes/{victim_mailbox.id}",
        headers=attacker,
        json={"transfer_aliases_to": -1},
    )
    assert r.status_code >= 400
    assert Mailbox.get(victim_mailbox.id) is not None
    scheduled = [
        job
        for job in Job.filter_by(name=JobType.DELETE_MAILBOX.value).all()
        if job.payload.get("mailbox_id") == victim_mailbox.id
    ]
    assert scheduled == []


def test_cannot_transfer_aliases_to_other_users_mailbox(flask_client):
    _, _, victim_mailbox, _ = _user_with_key()
    attacker_user, attacker, attacker_mailbox, _ = _user_with_key()
    alias = Alias.create_new(
        attacker_user, prefix="transfer", mailbox_id=attacker_mailbox.id
    )
    Session.commit()

    r = flask_client.delete(
        f"/api/mailboxes/{attacker_mailbox.id}",
        headers=attacker,
        json={"transfer_aliases_to": victim_mailbox.id},
    )
    assert r.status_code >= 400
    assert Alias.get(alias.id).mailbox_id != victim_mailbox.id
    assert AliasMailbox.get_by(alias_id=alias.id, mailbox_id=victim_mailbox.id) is None


def test_cannot_verify_other_users_mailbox(flask_client):
    victim_user, _, _, _ = _user_with_key()
    pending = Mailbox.create(
        user_id=victim_user.id, email=f"mb_{random_token(10)}@gmail.com"
    )
    Session.commit()
    _, attacker, _, _ = _user_with_key()

    r = flask_client.post(
        f"/api/mailboxes/{pending.id}/verify", headers=attacker, json={"code": "x"}
    )
    assert r.status_code == 400
    assert not Mailbox.get(pending.id).verified

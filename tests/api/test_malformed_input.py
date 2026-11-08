"""Malformed request bodies must get a 4xx answer, never a 500."""

import pytest

from app.db import Session
from app.models import Alias, ApiKey, Contact, CustomDomain, Directory, Mailbox
from tests.utils import create_new_user, random_token

JUNK_VALUES = ["abc", "12", 12, -5, 1.5, True, None, [], ["x"], [1, "y"], {}, {"a": 1}]
JUNK_BODIES = [None, "not json", "[1, 2]", '"string"', "123", "null"]


@pytest.fixture
def setup(flask_client):
    user = create_new_user()
    api_key = ApiKey.create(user.id, name="test")
    mailbox = Mailbox.create(
        user_id=user.id, email=f"mb_{random_token(10)}@gmail.com", verified=True
    )
    alias = Alias.create_new_random(user)
    contact = Contact.create(
        user_id=user.id,
        alias_id=alias.id,
        website_email=f"c_{random_token(8)}@example.com",
        reply_email=f"r_{random_token(8)}@sl.lan",
    )
    domain = CustomDomain.create(
        user_id=user.id,
        domain=f"{random_token(10)}.lan",
        verified=True,
        ownership_verified=True,
    )
    directory = Directory.create(name=f"dir{random_token(8)}", user_id=user.id)
    Session.commit()
    return {
        "directory": directory,
        "headers": {"Authentication": api_key.code},
        "alias": alias,
        "mailbox": mailbox,
        "contact": contact,
        "domain": domain,
    }


def _requests(s):
    """(method, url, fields) for every endpoint that reads a JSON body."""
    return [
        (
            "patch",
            f"/api/aliases/{s['alias'].id}",
            ["note", "mailbox_id", "mailbox_ids", "name", "disable_pgp", "pinned"],
        ),
        ("post", f"/api/aliases/{s['alias'].id}/contacts", ["contact"]),
        (
            "post",
            "/api/v3/alias/custom/new",
            ["alias_prefix", "signed_suffix", "mailbox_ids", "note", "name"],
        ),
        (
            "post",
            "/api/v2/alias/custom/new",
            ["alias_prefix", "signed_suffix", "note", "name"],
        ),
        ("post", "/api/alias/random/new", ["note"]),
        ("post", "/api/mailboxes", ["email"]),
        (
            "put",
            f"/api/mailboxes/{s['mailbox'].id}",
            [
                "default",
                "email",
                "cancel_email_change",
                "pgp_public_key",
                "disable_pgp",
                "generic_subject",
            ],
        ),
        ("delete", f"/api/mailboxes/{s['mailbox'].id}", ["transfer_aliases_to"]),
        ("post", f"/api/mailboxes/{s['mailbox'].id}/verify", ["code"]),
        (
            "patch",
            f"/api/custom_domains/{s['domain'].id}",
            ["catch_all", "random_prefix_generation", "name", "mailbox_ids"],
        ),
        (
            "patch",
            "/api/setting",
            [
                "alias_generator",
                "sender_format",
                "random_alias_suffix",
                "random_alias_default_domain",
            ],
        ),
        ("patch", "/api/user_info", ["name"]),
        ("post", "/api/custom_domains", ["domain"]),
        ("post", f"/api/custom_domains/{s['domain'].id}/dns", ["check"]),
        ("post", "/api/directories", ["name", "mailbox_ids"]),
        ("patch", f"/api/directories/{s['directory'].id}", ["disabled", "mailbox_ids"]),
    ]


def _call(flask_client, method, url, **kwargs):
    try:
        status = getattr(flask_client, method)(url, **kwargs).status_code
    except Exception as e:  # exceptions propagate in testing mode
        status = f"exception {e.__class__.__name__}: {str(e)[:80]}"
    return status


def test_junk_field_values_never_500(flask_client, setup):
    failures = []
    for method, url, fields in _requests(setup):
        for field in fields:
            for value in JUNK_VALUES:
                status = _call(
                    flask_client,
                    method,
                    url,
                    headers=setup["headers"],
                    json={field: value},
                )
                if not isinstance(status, int) or status >= 500 or status == 401:
                    failures.append((method, url, field, value, status))
    assert failures == []


def test_junk_bodies_never_500(flask_client, setup):
    failures = []
    for method, url, _ in _requests(setup):
        for body in JUNK_BODIES:
            status = _call(
                flask_client,
                method,
                url,
                headers={**setup["headers"], "Content-Type": "application/json"},
                data=body,
            )
            if not isinstance(status, int) or status >= 500 or status == 401:
                failures.append((method, url, body, status))
    assert failures == []

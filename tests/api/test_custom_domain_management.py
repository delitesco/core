import pytest

from app import config
from app.constants import DMARC_RECORD, JobType
from app.db import Session
from app.dns_utils import InMemoryDNSClient, set_global_dns_client
from app.models import CustomDomain, Directory, Job, Mailbox
from tests.utils import login, random_token


@pytest.fixture
def dns():
    client = InMemoryDNSClient()
    set_global_dns_client(client)
    yield client
    set_global_dns_client(None)


def _domain() -> str:
    return f"{random_token(10)}.com"


def test_create_custom_domain(flask_client, dns):
    login(flask_client)
    domain = _domain()

    r = flask_client.post("/api/custom_domains", json={"domain": domain})
    assert r.status_code == 201
    assert r.json["domain_name"] == domain
    assert r.json["ownership_verified"] is False
    assert r.json["is_verified"] is False

    # not twice by the same user
    r = flask_client.post("/api/custom_domains", json={"domain": domain})
    assert r.status_code == 400


def test_unverified_claim_does_not_lock_out_the_owner(flask_client, dns):
    domain = _domain()
    login(flask_client)
    squatter_id = flask_client.post(
        "/api/custom_domains", json={"domain": domain}
    ).json["id"]

    # the real owner can still add the domain, and wins by proving ownership
    login(flask_client)
    r = flask_client.post("/api/custom_domains", json={"domain": domain})
    assert r.status_code == 201
    owner_id = r.json["id"]
    token = flask_client.get(f"/api/custom_domains/{owner_id}/dns").json["ownership"][
        "value"
    ]
    dns.set_txt_record(domain, [token])
    r = flask_client.post(
        f"/api/custom_domains/{owner_id}/dns", json={"check": "ownership"}
    )
    assert r.status_code == 200
    assert "ownership" not in r.json["errors"]
    assert CustomDomain.get(owner_id).ownership_verified
    assert CustomDomain.get_by_domain(domain).id == owner_id

    # once verified, nobody else can add or verify it
    login(flask_client)
    r = flask_client.post("/api/custom_domains", json={"domain": domain})
    assert r.status_code == 400

    squatter = CustomDomain.get(squatter_id)
    login(flask_client, squatter.user)
    squatter_token = flask_client.get(f"/api/custom_domains/{squatter_id}/dns").json[
        "ownership"
    ]["value"]
    dns.set_txt_record(domain, [token, squatter_token])
    r = flask_client.post(
        f"/api/custom_domains/{squatter_id}/dns", json={"check": "ownership"}
    )
    assert "ownership" in r.json["errors"]
    assert not CustomDomain.get(squatter_id).ownership_verified


def test_create_custom_domain_rejects_alias_domains(flask_client, dns):
    login(flask_client)
    r = flask_client.post("/api/custom_domains", json={"domain": config.EMAIL_DOMAIN})
    assert r.status_code == 400


def test_dns_setup_and_checks(flask_client, dns):
    login(flask_client)
    domain = _domain()
    domain_id = flask_client.post("/api/custom_domains", json={"domain": domain}).json[
        "id"
    ]

    r = flask_client.get(f"/api/custom_domains/{domain_id}/dns")
    assert r.status_code == 200
    records = r.json
    assert records["ownership"]["value"].startswith("sl-verification=")
    assert records["dmarc"]["value"] == DMARC_RECORD

    # nothing set yet: ownership fails, other checks need ownership first
    r = flask_client.post(f"/api/custom_domains/{domain_id}/dns", json={})
    assert r.status_code == 200
    assert "ownership" in r.json["errors"]
    r = flask_client.post(f"/api/custom_domains/{domain_id}/dns", json={"check": "mx"})
    assert r.status_code == 400

    dns.set_txt_record(domain, [records["ownership"]["value"], records["spf"]["value"]])
    dns.set_mx_records(
        domain, {r["priority"]: [r["value"]] for r in records["mx"]["records"]}
    )
    for dkim in records["dkim"]["records"]:
        dns.set_cname_record(dkim["host"], dkim["value"])
    dns.set_txt_record(records["dmarc"]["host"], [DMARC_RECORD])

    r = flask_client.post(f"/api/custom_domains/{domain_id}/dns", json={})
    assert r.status_code == 200
    assert r.json["errors"] == {}
    for check in ("ownership", "mx", "spf", "dkim", "dmarc"):
        assert r.json[check]["verified"] is True, check
    custom_domain = CustomDomain.get(domain_id)
    assert custom_domain.ownership_verified and custom_domain.verified


def test_delete_custom_domain(flask_client, dns):
    login(flask_client)
    domain_id = flask_client.post(
        "/api/custom_domains", json={"domain": _domain()}
    ).json["id"]

    r = flask_client.delete(f"/api/custom_domains/{domain_id}")
    assert r.status_code == 200
    assert CustomDomain.get(domain_id).pending_deletion
    assert any(
        job.payload.get("custom_domain_id") == domain_id
        for job in Job.filter_by(name=JobType.DELETE_DOMAIN.value).all()
    )


def test_cannot_manage_other_users_domain(flask_client, dns):
    login(flask_client)
    domain_id = flask_client.post(
        "/api/custom_domains", json={"domain": _domain()}
    ).json["id"]

    login(flask_client)
    assert flask_client.get(f"/api/custom_domains/{domain_id}/dns").status_code == 403
    assert (
        flask_client.post(f"/api/custom_domains/{domain_id}/dns", json={}).status_code
        == 403
    )
    assert flask_client.delete(f"/api/custom_domains/{domain_id}").status_code == 403
    assert not CustomDomain.get(domain_id).pending_deletion


def _verified_mailbox(user) -> Mailbox:
    mailbox = Mailbox.create(
        user_id=user.id, email=f"mb_{random_token(10)}@gmail.com", verified=True
    )
    Session.commit()
    return mailbox


def test_directories(flask_client):
    user = login(flask_client)
    mailbox = _verified_mailbox(user)
    name = f"dir{random_token(6)}"

    # mailboxes are required
    r = flask_client.post("/api/directories", json={"name": name})
    assert r.status_code == 400

    r = flask_client.post(
        "/api/directories", json={"name": name, "mailbox_ids": [mailbox.id]}
    )
    assert r.status_code == 201
    directory_id = r.json["id"]
    assert r.json["mailboxes"] == [{"id": mailbox.id, "email": mailbox.email}]

    r = flask_client.get("/api/directories")
    assert [d["id"] for d in r.json["directories"]] == [directory_id]

    r = flask_client.patch(f"/api/directories/{directory_id}", json={"disabled": True})
    assert r.status_code == 200
    assert Directory.get(directory_id).disabled

    r = flask_client.delete(f"/api/directories/{directory_id}")
    assert r.status_code == 200
    assert Directory.get(directory_id) is None

    # a deleted name cannot be reused
    r = flask_client.post(
        "/api/directories", json={"name": name, "mailbox_ids": [mailbox.id]}
    )
    assert r.status_code == 400


@pytest.mark.parametrize("name", ["ab", "with space", "reply", "a" * 33, "dot.ted"])
def test_invalid_directory_names(flask_client, name):
    user = login(flask_client)
    mailbox = _verified_mailbox(user)
    r = flask_client.post(
        "/api/directories", json={"name": name, "mailbox_ids": [mailbox.id]}
    )
    assert r.status_code == 400


def test_directories_are_isolated(flask_client):
    user = login(flask_client)
    mailbox = _verified_mailbox(user)
    directory_id = flask_client.post(
        "/api/directories",
        json={"name": f"dir{random_token(6)}", "mailbox_ids": [mailbox.id]},
    ).json["id"]

    other = login(flask_client)
    other_mailbox = _verified_mailbox(other)
    assert flask_client.get("/api/directories").json["directories"] == []
    assert (
        flask_client.patch(
            f"/api/directories/{directory_id}", json={"disabled": True}
        ).status_code
        == 403
    )
    assert flask_client.delete(f"/api/directories/{directory_id}").status_code == 403
    # cannot attach another user's mailbox to a new directory
    r = flask_client.post(
        "/api/directories",
        json={"name": f"dir{random_token(6)}", "mailbox_ids": [mailbox.id]},
    )
    assert r.status_code == 400
    assert other_mailbox.user_id == other.id
    assert Directory.get(directory_id) is not None

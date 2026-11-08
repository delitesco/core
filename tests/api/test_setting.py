from app.models import (
    CustomDomain,
    AliasGeneratorEnum,
    SenderFormatEnum,
    AliasSuffixEnum,
    UnsubscribeBehaviourEnum,
)
from tests.utils import login, random_domain


def test_get_setting(flask_client):
    login(flask_client)

    r = flask_client.get("/api/setting")
    assert r.status_code == 200
    assert r.json == {
        "alias_generator": "word",
        "random_alias_default_domain": "sl.lan",
        "sender_format": "AT",
        "random_alias_suffix": "word",
        "unsubscribe_behaviour": "preserve_original",
    }


def test_update_settings_alias_generator(flask_client):
    user = login(flask_client)
    assert user.alias_generator == AliasGeneratorEnum.word.value

    r = flask_client.patch("/api/setting", json={"alias_generator": "invalid"})
    assert r.status_code == 400

    r = flask_client.patch("/api/setting", json={"alias_generator": "uuid"})
    assert r.status_code == 200
    assert user.alias_generator == AliasGeneratorEnum.uuid.value


def test_update_settings_random_alias_default_domain(flask_client):
    user = login(flask_client)
    custom_domain = CustomDomain.create(
        domain=random_domain(), verified=True, user_id=user.id, flush=True
    )
    assert user.default_random_alias_domain() == "sl.lan"

    r = flask_client.patch(
        "/api/setting", json={"random_alias_default_domain": "invalid"}
    )
    assert r.status_code == 400

    r = flask_client.patch(
        "/api/setting", json={"random_alias_default_domain": "d1.lan"}
    )
    assert r.status_code == 200
    assert user.default_random_alias_domain() == "d1.lan"

    r = flask_client.patch(
        "/api/setting", json={"random_alias_default_domain": custom_domain.domain}
    )
    assert r.status_code == 200
    assert user.default_random_alias_domain() == custom_domain.domain


def test_update_settings_sender_format(flask_client):
    user = login(flask_client)
    assert user.sender_format == SenderFormatEnum.AT.value

    r = flask_client.patch("/api/setting", json={"sender_format": "invalid"})
    assert r.status_code == 400

    r = flask_client.patch("/api/setting", json={"sender_format": "A"})
    assert r.status_code == 200
    assert user.sender_format == SenderFormatEnum.A.value

    r = flask_client.patch("/api/setting", json={"sender_format": "NAME_ONLY"})
    assert r.status_code == 200
    assert user.sender_format == SenderFormatEnum.NAME_ONLY.value


def test_get_setting_domains(flask_client):
    user = login(flask_client)
    domain = random_domain()
    CustomDomain.create(user_id=user.id, domain=domain, verified=True, commit=True)

    r = flask_client.get("/api/setting/domains")
    assert r.status_code == 200


def test_get_setting_domains_v2(flask_client):
    user = login(flask_client)
    domain = random_domain()
    CustomDomain.create(user_id=user.id, domain=domain, verified=True, commit=True)

    r = flask_client.get("/api/v2/setting/domains")
    assert r.status_code == 200


def test_update_settings_random_alias_suffix(flask_client):
    user = login(flask_client)
    # default random_alias_suffix is random_string
    assert user.random_alias_suffix == AliasSuffixEnum.word.value

    r = flask_client.patch("/api/setting", json={"random_alias_suffix": "invalid"})
    assert r.status_code == 400

    r = flask_client.patch(
        "/api/setting", json={"random_alias_suffix": "random_string"}
    )
    assert r.status_code == 200
    assert user.random_alias_suffix == AliasSuffixEnum.random_string.value


def test_update_unsubscribe_behaviour(flask_client):
    user = login(flask_client)
    for name, value in [
        ("disable_alias", UnsubscribeBehaviourEnum.DisableAlias),
        ("block_contact", UnsubscribeBehaviourEnum.BlockContact),
        ("preserve_original", UnsubscribeBehaviourEnum.PreserveOriginal),
    ]:
        r = flask_client.patch("/api/setting", json={"unsubscribe_behaviour": name})
        assert r.status_code == 200
        assert r.json["unsubscribe_behaviour"] == name
        assert user.unsub_behaviour == value

    r = flask_client.patch("/api/setting", json={"unsubscribe_behaviour": "nope"})
    assert r.status_code == 400

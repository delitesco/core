import ipaddress

import pytest

from app import config


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "secret",
        "paste-flask-secret-here",
        "paste-provisioning-key-here-0123456789abcdef",
        "put_something_secret_here_0123456789abcdef",
        "short-but-random-a8f3",
    ],
)
def test_check_secret_rejects_weak_values(value):
    with pytest.raises(RuntimeError):
        config.check_secret("X", value)


def test_check_secret_accepts_random_value():
    config.check_secret("X", "9f2c4e1ab37d4c0e8a5b6f7d1e2c3b4a")


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_env_flag_on(monkeypatch, value):
    monkeypatch.setenv("SOME_FLAG", value)
    assert config.env_flag("SOME_FLAG")


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
def test_env_flag_off(monkeypatch, value):
    monkeypatch.setenv("SOME_FLAG", value)
    assert not config.env_flag("SOME_FLAG")


def test_env_flag_unset(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert not config.env_flag("SOME_FLAG")


def test_parse_networks():
    networks = config.parse_networks("X", ["10.0.0.0/8", "203.0.113.10", "::1"])
    assert ipaddress.ip_address("10.1.2.3") in networks[0]
    assert ipaddress.ip_address("203.0.113.10") in networks[1]
    with pytest.raises(RuntimeError):
        config.parse_networks("X", ["not-an-ip"])


def test_is_open_to_any_ip():
    assert config.is_open_to_any_ip(config.parse_networks("X", ["0.0.0.0/0"]))
    assert config.is_open_to_any_ip(config.parse_networks("X", ["10.0.0.0/8", "::/0"]))
    assert not config.is_open_to_any_ip(
        config.parse_networks("X", ["10.0.0.0/8", "203.0.113.10/32"])
    )

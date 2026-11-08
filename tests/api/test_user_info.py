from flask import url_for

from app.models import User
from tests.utils import login


def test_wrong_api_key(flask_client):
    r = flask_client.get(
        url_for("api.user_info"), headers={"Authentication": "Invalid code"}
    )

    assert r.status_code == 401

    assert r.json == {"error": "Wrong api key"}


def test_create_api_key(flask_client):
    login(flask_client)

    # create api key
    r = flask_client.post(url_for("api.create_api_key"), json={"device": "Test device"})

    assert r.status_code == 201
    assert r.json["api_key"]


def test_change_profile_picture(flask_client):
    user = login(flask_client)
    assert not user.profile_picture_id

    # <<< Set the profile picture >>>
    img_base64 = """iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="""
    r = flask_client.patch(
        "/api/user_info",
        json={"profile_picture": img_base64},
    )

    assert r.status_code == 200
    assert r.json["profile_picture_url"] is not None

    user = User.get(user.id)
    assert user.profile_picture_id

    # <<< remove the profile picture >>>
    r = flask_client.patch(
        "/api/user_info",
        json={"profile_picture": None},
    )
    assert r.status_code == 200
    assert r.json["profile_picture_url"] is None

    user = User.get(user.id)
    assert not user.profile_picture_id


def test_change_name(flask_client):
    user = login(flask_client)
    assert user.name != "new name"

    r = flask_client.patch(
        "/api/user_info",
        json={"name": "new name"},
    )

    assert r.status_code == 200
    assert r.json["name"] == "new name"

    assert user.name == "new name"


def test_stats(flask_client):
    login(flask_client)

    r = flask_client.get("/api/stats")

    assert r.status_code == 200
    assert r.json == {"nb_alias": 1, "nb_block": 0, "nb_forward": 0, "nb_reply": 0}

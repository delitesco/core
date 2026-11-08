from flask import url_for

from tests.utils import login


def test_wrong_api_key(flask_client):
    r = flask_client.get(
        url_for("api.user_info"), headers={"Authentication": "Invalid code"}
    )

    assert r.status_code == 401

    assert r.json == {"error": "Wrong api key"}


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

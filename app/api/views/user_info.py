import dataclasses

from flask import jsonify, g, request, make_response

from app.api.base import api_bp, require_api_auth
from app.config import SESSION_COOKIE_NAME
from app.db import Session
from app.user_stats import get_stats
from app.models import ApiKey, User
from app.session import logout_session


def user_to_dict(user: User) -> dict:
    ret = {
        "name": user.name or "",
        "is_premium": user.is_premium(),
        "email": user.email,
        "can_create_reverse_alias": user.can_create_contacts(),
    }

    return ret


@api_bp.route("/user_info")
@require_api_auth
def user_info():
    """
    Return user info given the api-key

    Output as json
    - name
    - is_premium
    - email
    - max_alias_free
    - can_create_reverse_alias
    """
    user = g.user

    return jsonify(user_to_dict(user))


@api_bp.route("/user_info", methods=["PATCH"])
@require_api_auth
def update_user_info():
    """
    Input
    - name (optional)
    """
    user = g.user
    data = request.get_json() or {}

    if "name" in data:
        user.name = data["name"]

    Session.commit()

    return jsonify(user_to_dict(user))


@api_bp.route("/api_key", methods=["POST"])
@require_api_auth
def create_api_key():
    """Used to create a new api key
    Input:
    - device

    Output:
    - api_key
    """
    data = request.get_json()
    if not data:
        return jsonify(error="request body cannot be empty"), 400

    device = data.get("device")

    api_key = ApiKey.create(user_id=g.user.id, name=device)
    Session.commit()

    return jsonify(api_key=api_key.code), 201


@api_bp.route("/logout", methods=["GET"])
@require_api_auth
def logout():
    """
    Log user out on the web, i.e. remove the cookie

    Output:
    - 200
    """
    logout_session()
    response = make_response(jsonify(msg="User is logged out"), 200)
    response.delete_cookie(SESSION_COOKIE_NAME)

    return response


@api_bp.route("/stats")
@require_api_auth
def user_stats():
    """
    Return stats

    Output as json
    - nb_alias
    - nb_forward
    - nb_reply
    - nb_block

    """
    user = g.user
    stats = get_stats(user)

    return jsonify(dataclasses.asdict(stats))

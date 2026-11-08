import dataclasses

from flask import jsonify, g

from app.api.base import api_bp, is_optional_str, json_body, require_api_auth
from app.db import Session
from app.user_stats import get_stats
from app.models import User


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
    data = json_body()

    if "name" in data:
        name = data["name"]
        if not is_optional_str(name) or (name and len(name) > 128):
            return jsonify(error="Invalid name"), 400
        user.name = name

    Session.commit()

    return jsonify(user_to_dict(user))


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

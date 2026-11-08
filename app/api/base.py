from functools import wraps
from typing import Tuple, Optional

import arrow
from flask import Blueprint, request, jsonify, g

from app.db import Session
from app.models import ApiKey

api_bp = Blueprint(name="api", import_name=__name__, url_prefix="/api")


def authorize_request() -> Optional[Tuple[str, int]]:
    """Authenticate the request with the API key from the Authentication header.

    API keys are the only way to authenticate: there are no sessions or cookies.
    """
    api_code = request.headers.get("Authentication")
    api_key = ApiKey.get_by(code=api_code) if api_code else None
    if not api_key:
        return jsonify(error="Wrong api key"), 401

    # Update api key stats
    api_key.last_used = arrow.now()
    api_key.times += 1
    Session.commit()

    g.user = api_key.user
    g.api_key = api_key

    if g.user.disabled:
        return jsonify(error="Disabled account"), 403

    if not g.user.is_active():
        return jsonify(error="Account does not exist"), 401

    return None


def require_api_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        error_return = authorize_request()
        if error_return:
            return error_return
        return f(*args, **kwargs)

    return decorated

from functools import wraps
from typing import List, Tuple, Optional

import arrow
import werkzeug.exceptions
from flask import Blueprint, request, jsonify, g

from app import rate_limiter
from app.db import Session
from app.log import LOG
from app.models import ApiKey

# failed API key attempts per client address and minute
MAX_FAILED_API_KEY_ATTEMPTS = 30

api_bp = Blueprint(name="api", import_name=__name__, url_prefix="/api")


def json_body() -> dict:
    """The JSON object of the request body, or {} when the body is missing, is not
    JSON, or is not an object (e.g. a list)."""
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def parse_int(value) -> Optional[int]:
    """An int from a JSON value (int or numeric string), None if it is anything else."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def is_optional_str(value) -> bool:
    return value is None or isinstance(value, str)


def parse_int_list(value) -> Optional[List[int]]:
    """A list of ints from a JSON list, None if it is not a list of ints."""
    if not isinstance(value, list):
        return None
    ints = [parse_int(item) for item in value]
    return None if any(i is None for i in ints) else ints


def lookup_api_key(code: Optional[str]) -> Optional[ApiKey]:
    """The API key with this code, looked up once per request (the rate limiter's key
    function needs it too)."""
    if not code:
        return None
    cache = g.setdefault("api_key_lookups", {})
    if code not in cache:
        cache[code] = ApiKey.get_by(code=code)
    return cache[code]


def authorize_request() -> Optional[Tuple[str, int]]:
    """Authenticate the request with the API key from the Authentication header.

    API keys are the only way to authenticate: there are no sessions or cookies.
    """
    api_key = lookup_api_key(request.headers.get("Authentication"))
    if not api_key:
        # Keys cannot be guessed, but repeated failures are noise (or a stale key in a
        # loop): only failed attempts count, so valid requests from the same address pass.
        try:
            rate_limiter.check_bucket_limit(
                f"bad_api_key:{request.remote_addr}", MAX_FAILED_API_KEY_ATTEMPTS, 60
            )
        except werkzeug.exceptions.TooManyRequests:
            LOG.w("Too many wrong API keys from %s", request.remote_addr)
            return jsonify(error="Too many attempts"), 429
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

from flask import request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app import config


# We want to rate limit based on:
# - If the request carries a valid API key: the key's user_id.
#   All API calls come from the UI backend, so limiting them by IP would make
#   every user share one bucket.
# - Otherwise: request source IP
def __key_func():
    api_code = request.headers.get("Authentication")
    if api_code:
        # imported here to avoid a circular import with app.models
        from app.models import ApiKey

        api_key = ApiKey.get_by(code=api_code)
        if api_key:
            return f"userid:{api_key.user_id}"
    ip_addr = get_remote_address()
    return f"ip:{ip_addr}"


# Setup rate limit facility
limiter = Limiter(key_func=__key_func)


@limiter.request_filter
def disable_rate_limit():
    return config.DISABLE_RATE_LIMIT


# @limiter.request_filter
# def ip_whitelist():
#     # Uncomment line to test rate limit in dev environment
#     # return False
#     # No limit for local development
#     return request.remote_addr == "127.0.0.1"

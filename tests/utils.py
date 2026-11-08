import email
import json
import os
import random
import string
from email.message import EmailMessage
from typing import Optional, Dict

import jinja2

from app.db import Session
from app.models import Alias, ApiKey, User, UserAliasDeleteAction
from app.utils import random_string


def create_new_user(
    email: Optional[str] = None,
    name: Optional[str] = None,
    alias_delete_action: UserAliasDeleteAction = UserAliasDeleteAction.DeleteImmediately,
) -> User:
    if not email:
        email = f"user_{random_token(10)}@mailbox.lan"
    if not name:
        name = "Test User"
    # new user has a different email address
    user = User.create(
        email=email,
        password="password",
        name=name,
        activated=True,
        flush=True,
    )
    user.alias_delete_action = alias_delete_action
    Session.flush()
    # Upstream users got a "newsletter" alias on creation and many tests are written
    # against a user that owns exactly one alias. Keep that starting state explicit here.
    Alias.create_new(user, prefix="first-alias", mailbox_id=user.default_mailbox_id)
    Session.flush()

    return user


def login(flask_client, user: Optional[User] = None) -> User:
    """Authenticate every following request of flask_client with a fresh API key for user."""
    if not user:
        user = create_new_user()

    api_key = ApiKey.create(user_id=user.id, name="test", commit=True)
    flask_client.environ_base["HTTP_AUTHENTICATION"] = api_key.code

    return user


def random_domain() -> str:
    return random_token() + ".lan"


def random_token(length: int = 10) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def pretty(d):
    """pretty print as json"""
    print(json.dumps(d, indent=2))


def load_eml_file(
    filename: str, template_values: Optional[Dict[str, str]] = None
) -> EmailMessage:
    emails_dir = os.path.join(
        os.path.dirname(os.path.realpath(__file__)), "example_emls"
    )
    fullpath = os.path.join(emails_dir, filename)
    with open(fullpath) as fd:
        template = jinja2.Template(fd.read())
        if not template_values:
            template_values = {}
        rendered = template.render(**template_values)
        return email.message_from_bytes(rendered.encode("utf-8"))


def random_email() -> str:
    return "{rand}@{rand}.com".format(rand=random_string(20))


def fix_rate_limit_after_request():
    from flask import g
    from app.extensions import limiter

    g._rate_limiting_complete = False
    setattr(g, "%s_rate_limiting_complete" % limiter._key_prefix, False)

"""User provisioning for the backend in front of this service.

Each account of that backend gets its own user here, with its own API key, so the
ownership checks of every other endpoint isolate accounts from each other.
These endpoints are authenticated with a separate provisioning key
(config.PROVISIONING_API_KEY, header X-Provisioning-Key) and are disabled
(404) when no provisioning key is configured.
"""

import hmac
import uuid
from functools import wraps

import arrow
from flask import jsonify, request

from app import config
from app.alias_utils import AliasTransferError, transfer_alias
from app.api.base import api_bp, json_body, parse_int, parse_int_list
from app.constants import JobType
from app.db import Session
from app.extensions import limiter
from app.log import LOG
from app.models import Alias, ApiKey, Job, Mailbox, User
from app.user_audit_log_utils import emit_user_audit_log, UserAuditLogAction

PROVISIONING_KEY_HEADER = "X-Provisioning-Key"


def require_provisioning_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        expected = config.PROVISIONING_API_KEY
        if not expected:
            return jsonify(error="No such endpoint"), 404
        given = request.headers.get(PROVISIONING_KEY_HEADER) or ""
        if not hmac.compare_digest(given.encode(), expected.encode()):
            LOG.w("Wrong provisioning key from %s", request.remote_addr)
            return jsonify(error="Wrong provisioning key"), 401
        return f(*args, **kwargs)

    return decorated


def _service_email() -> str:
    # never a deliverable address: users have no email of their own here
    return f"{uuid.uuid4().hex}@{config.PROVISIONED_USER_EMAIL_DOMAIN}"


def _get_active_user(user_id: int):
    user = User.get(user_id)
    if not user or user.disabled or user.delete_on is not None:
        return None
    return user


@api_bp.route("/provision/users", methods=["POST"])
@limiter.limit(lambda: config.PROVISIONING_RATE_LIMIT)
@require_provisioning_key
def provision_create_user():
    """
    Create a user and its API key.
    Output: 201 {"user_id": int, "api_key": str}. The key is only returned here
    and by the rotate endpoint.
    """
    user = User.create(email=_service_email(), activated=True)
    api_key = ApiKey.create(user.id, name="provisioned")
    Session.commit()
    LOG.i("Provisioned user %s", user)
    return jsonify(user_id=user.id, api_key=api_key.code), 201


@api_bp.route("/provision/users/<int:user_id>/api_key", methods=["POST"])
@limiter.limit(lambda: config.PROVISIONING_RATE_LIMIT)
@require_provisioning_key
def provision_rotate_api_key(user_id: int):
    """
    Revoke every API key of the user and return a new one.
    Output: 200 {"api_key": str}, 404 if the user does not exist or is being deleted.
    """
    user = _get_active_user(user_id)
    if not user:
        return jsonify(error="User not found"), 404

    ApiKey.delete_all(user.id)
    api_key = ApiKey.create(user.id, name="provisioned")
    Session.commit()
    LOG.i("Rotated API key of %s", user)
    return jsonify(api_key=api_key.code), 200


@api_bp.route("/provision/users/<int:user_id>", methods=["DELETE"])
@limiter.limit(lambda: config.PROVISIONING_RATE_LIMIT)
@require_provisioning_key
def provision_delete_user(user_id: int):
    """
    Delete a user with all its aliases, mailboxes and contacts.
    Access stops immediately (API keys revoked, mail no longer handled); the data
    is deleted by the job runner.
    Output: 200 {"ok": true}, 404 if the user does not exist or is already being deleted.
    """
    user = _get_active_user(user_id)
    if not user:
        return jsonify(error="User not found"), 404

    ApiKey.delete_all(user.id)
    user.delete_on = arrow.now()
    emit_user_audit_log(
        user=user,
        action=UserAuditLogAction.UserMarkedForDeletion,
        message=f"Marked user {user.id} for deletion by provisioning",
    )
    Job.create(
        name=JobType.DELETE_ACCOUNT.value,
        payload={"user_id": user.id},
        run_at=arrow.now(),
    )
    Session.commit()
    LOG.i("Scheduled deletion of %s", user)
    return jsonify(ok=True), 200


@api_bp.route("/provision/aliases/<int:alias_id>/transfer", methods=["POST"])
@limiter.limit(lambda: config.PROVISIONING_RATE_LIMIT)
@require_provisioning_key
def provision_transfer_alias(alias_id: int):
    """
    Give an alias, with its contacts, to another user. The backend in front of this
    service decides who may transfer what (e.g. after the receiving account accepted).
    Input (body):
        to_user_id: the receiving user
        mailbox_ids: verified mailboxes of the receiving user the alias forwards to
    Output: 200 {"ok": true}; 404 if the alias or the user does not exist;
        400 if the alias cannot be transferred (custom domain, directory, trash) or the
        mailboxes are not verified mailboxes of the receiving user.
    """
    data = json_body()
    to_user_id = parse_int(data.get("to_user_id"))
    mailbox_ids = parse_int_list(data.get("mailbox_ids"))
    if to_user_id is None or not mailbox_ids:
        return jsonify(error="to_user_id and mailbox_ids are required"), 400

    alias = Alias.get(alias_id)
    new_user = _get_active_user(to_user_id)
    if not alias or not new_user:
        return jsonify(error="Alias or user not found"), 404

    mailboxes = [Mailbox.get(mailbox_id) for mailbox_id in mailbox_ids]
    if any(mailbox is None for mailbox in mailboxes):
        return jsonify(
            error="Mailboxes must be verified mailboxes of the new owner"
        ), 400

    try:
        transfer_alias(alias, new_user, mailboxes)
    except AliasTransferError as e:
        # raised before anything is changed
        return jsonify(error=e.msg), 400

    LOG.i("Transferred %s to %s", alias, new_user)
    return jsonify(ok=True), 200

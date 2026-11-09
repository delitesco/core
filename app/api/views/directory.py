"""Directories: a name that creates aliases on the fly, e.g. name+anything@ or
name/anything@ on the shared alias domains (see app.alias_utils)."""

import re
from typing import List, Optional

from flask import g, jsonify

from app import config
from app.api.base import api_bp, json_body, parse_int_list, require_api_auth
from app.db import Session
from app.errors import DirectoryInTrashError
from app.extensions import limiter
from app.models import Directory, DirectoryMailbox, Mailbox
from app.user_audit_log_utils import UserAuditLogAction, emit_user_audit_log

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{2,31}$")
_RESERVED_NAMES = {
    "reply",
    "ra",
    "bounces",
    "bounce",
    "transactional",
    config.BOUNCE_PREFIX_FOR_REPLY_PHASE,
}


def directory_to_dict(directory: Directory) -> dict:
    return {
        "id": directory.id,
        "name": directory.name,
        "disabled": directory.disabled,
        "nb_alias": directory.nb_alias(),
        "creation_timestamp": directory.created_at.timestamp,
        "mailboxes": [{"id": mb.id, "email": mb.email} for mb in directory.mailboxes],
    }


def _own_directory(directory_id: int) -> Optional[Directory]:
    directory = Directory.get(directory_id)
    if not directory or directory.user_id != g.user.id:
        return None
    return directory


def _own_verified_mailboxes(value) -> Optional[List[Mailbox]]:
    mailbox_ids = parse_int_list(value)
    if not mailbox_ids:
        return None
    mailboxes = [Mailbox.get(mailbox_id) for mailbox_id in mailbox_ids]
    for mailbox in mailboxes:
        if not mailbox or mailbox.user_id != g.user.id or not mailbox.verified:
            return None
    return mailboxes


def _set_mailboxes(directory: Directory, mailboxes: List[Mailbox]):
    DirectoryMailbox.filter_by(directory_id=directory.id).delete()
    Session.flush()
    for mailbox in mailboxes:
        DirectoryMailbox.create(directory_id=directory.id, mailbox_id=mailbox.id)


@api_bp.route("/directories", methods=["GET"])
@require_api_auth
def get_directories():
    directories = (
        Directory.filter_by(user_id=g.user.id)
        .order_by(Directory.created_at.desc())
        .all()
    )
    return jsonify(directories=[directory_to_dict(d) for d in directories]), 200


@api_bp.route("/directories", methods=["POST"])
@limiter.limit("20/hour")
@require_api_auth
def create_directory():
    """
    Input: name (3-32 of a-z 0-9 _ -), mailbox_ids (verified mailboxes, required)
    Output: 201 and the directory, 400 with an error message
    """
    user = g.user
    if not user.can_create_new_alias():
        return jsonify(
            error="This account cannot create new directories right now"
        ), 400
    data = json_body()
    name = data.get("name")
    if not isinstance(name, str) or not _NAME_RE.match(name.lower()):
        return jsonify(
            error="Directory names have 3 to 32 characters: letters, digits, - and _"
        ), 400
    name = name.lower()
    if name in _RESERVED_NAMES:
        return jsonify(error="This directory name is reserved"), 400
    mailboxes = _own_verified_mailboxes(data.get("mailbox_ids"))
    if mailboxes is None:
        return jsonify(error="mailbox_ids must list verified mailboxes"), 400
    if user.directory_quota <= 0:
        return jsonify(
            error=f"You cannot have more than {config.MAX_NB_DIRECTORY} directories"
        ), 400
    if Directory.get_by(name=name):
        return jsonify(error=f"{name} is already used"), 400

    try:
        directory = Directory.create(name=name, user_id=user.id)
    except DirectoryInTrashError:
        return jsonify(error=f"{name} has been used before and cannot be reused"), 400
    _set_mailboxes(directory, mailboxes)
    emit_user_audit_log(
        user=user,
        action=UserAuditLogAction.CreateDirectory,
        message=f"New directory {directory.id} ({directory.name})",
    )
    Session.commit()
    return jsonify(directory_to_dict(directory)), 201


@api_bp.route("/directories/<int:directory_id>", methods=["PATCH"])
@require_api_auth
def update_directory(directory_id: int):
    """
    Input (optional): disabled (bool: no more aliases created on the fly),
        mailbox_ids (verified mailboxes)
    """
    directory = _own_directory(directory_id)
    if not directory:
        return jsonify(error="Forbidden"), 403
    data = json_body()

    if "disabled" in data:
        if not isinstance(data["disabled"], bool):
            return jsonify(error="Invalid disabled"), 400
        directory.disabled = data["disabled"]

    if "mailbox_ids" in data:
        mailboxes = _own_verified_mailboxes(data["mailbox_ids"])
        if mailboxes is None:
            return jsonify(error="mailbox_ids must list verified mailboxes"), 400
        _set_mailboxes(directory, mailboxes)

    emit_user_audit_log(
        user=g.user,
        action=UserAuditLogAction.UpdateDirectory,
        message=f"Updated directory {directory.id} ({directory.name})",
    )
    Session.commit()
    return jsonify(directory_to_dict(directory)), 200


@api_bp.route("/directories/<int:directory_id>", methods=["DELETE"])
@require_api_auth
def delete_directory(directory_id: int):
    """Delete the directory and its aliases. The name cannot be used again."""
    directory = _own_directory(directory_id)
    if not directory:
        return jsonify(error="Forbidden"), 403
    emit_user_audit_log(
        user=g.user,
        action=UserAuditLogAction.DeleteDirectory,
        message=f"Delete directory {directory.id} ({directory.name})",
    )
    Directory.delete(directory.id)
    return jsonify(deleted=True), 200

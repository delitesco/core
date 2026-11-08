from smtplib import SMTPRecipientsRefused

from flask import g
from flask import jsonify

from app import mailbox_utils
from app.api.base import (
    api_bp,
    is_optional_str,
    json_body,
    parse_int,
    require_api_auth,
)
from app.pgp_utils import PGPException, load_public_key_and_check
from app.user_audit_log_utils import UserAuditLogAction, emit_user_audit_log
from app.db import Session
from app.extensions import limiter
from app.models import Mailbox
from app.utils import sanitize_email


def mailbox_to_dict(mailbox: Mailbox):
    return {
        "id": mailbox.id,
        "email": mailbox.email,
        "verified": mailbox.verified,
        "default": mailbox.user.default_mailbox_id == mailbox.id,
        "creation_timestamp": mailbox.created_at.timestamp,
        "nb_alias": mailbox.nb_alias(),
        # pending address change, waiting for POST /api/mailboxes/<id>/verify
        "new_email": mailbox.new_email,
        "pgp_fingerprint": mailbox.pgp_finger_print,
        "disable_pgp": mailbox.disable_pgp,
        "generic_subject": mailbox.generic_subject,
    }


@api_bp.route("/mailboxes", methods=["POST"])
@limiter.limit("20/hour")
@require_api_auth
def create_mailbox():
    """
    Create a new mailbox. User needs to verify the mailbox via an activation email.
    Input:
        email: in body
    Output:
        the new mailbox dict
    """
    user = g.user
    email = json_body().get("email")
    if not email or not isinstance(email, str):
        return jsonify(error="Invalid email"), 400

    mailbox_email = sanitize_email(email)

    try:
        new_mailbox = mailbox_utils.create_mailbox(user, mailbox_email).mailbox
    except mailbox_utils.TooManyVerificationEmails as e:
        return jsonify(error=e.msg), 429
    except mailbox_utils.MailboxError as e:
        return jsonify(error=e.msg), 400

    return (
        jsonify(mailbox_to_dict(new_mailbox)),
        201,
    )


@api_bp.route("/mailboxes/<int:mailbox_id>/verify", methods=["POST"])
@limiter.limit("10/minute")
@require_api_auth
def verify_mailbox(mailbox_id):
    """
    Confirm a new mailbox, or a pending mailbox email change, with the code from the
    verification email (the code is also in the link to MAILBOX_VERIFY_URL).
    Codes expire after 15 minutes and after 3 wrong attempts.
    Input:
        code: in body
    Output:
        200 and the mailbox dict, 400 if the code is wrong or expired
    """
    user = g.user
    data = json_body()
    code = data.get("code")
    if not isinstance(code, str) or not code:
        return jsonify(error="Invalid code"), 400

    try:
        mailbox = mailbox_utils.verify_mailbox_code(user, mailbox_id, code)
    except mailbox_utils.MailboxError as e:
        Session.commit()
        return jsonify(error=e.msg), 400

    Session.commit()
    return jsonify(mailbox_to_dict(mailbox)), 200


@api_bp.route("/mailboxes/<int:mailbox_id>", methods=["DELETE"])
@limiter.limit("100/hour")
@require_api_auth
def delete_mailbox(mailbox_id):
    """
    Delete mailbox
    Input:
        mailbox_id: in url
        (optional) transfer_aliases_to: in body. Id of the new mailbox for the aliases.
                                        If omitted or the value is set to -1,
                                        the aliases of the mailbox will be deleted too.
    Output:
        200 if deleted successfully

    """
    user = g.user
    data = json_body()
    transfer_mailbox_id = None
    if data.get("transfer_aliases_to") is not None:
        transfer_mailbox_id = parse_int(data.get("transfer_aliases_to"))
        if transfer_mailbox_id is None:
            return jsonify(error="Invalid transfer_aliases_to"), 400
        if transfer_mailbox_id <= 0:
            transfer_mailbox_id = None

    try:
        mailbox_utils.delete_mailbox(user, mailbox_id, transfer_mailbox_id)
    except mailbox_utils.MailboxError as e:
        return jsonify(error=e.msg), 400

    return jsonify(deleted=True), 200


@api_bp.route("/mailboxes/<int:mailbox_id>", methods=["PUT"])
@require_api_auth
@limiter.limit("100/hour")
def update_mailbox(mailbox_id):
    """
    Update mailbox
    Input:
        mailbox_id: in url
        (optional) default: in body. Set a mailbox as the default mailbox.
        (optional) email: in body. Change a mailbox email.
        (optional) cancel_email_change: in body. Cancel mailbox email change.
    Output:
        200 if updated successfully

    """
    user = g.user
    mailbox = Mailbox.get(mailbox_id)

    if not mailbox or mailbox.user_id != user.id:
        return jsonify(error="Forbidden"), 403

    data = json_body()
    changed = False
    if "default" in data:
        is_default = data.get("default")
        if not isinstance(is_default, bool):
            return jsonify(error="Invalid default"), 400
        if is_default:
            if not mailbox.verified:
                return (
                    jsonify(
                        error="Unverified mailbox cannot be used as default mailbox"
                    ),
                    400,
                )
            user.default_mailbox_id = mailbox.id
            changed = True

    if "email" in data:
        if not isinstance(data.get("email"), str):
            return jsonify(error="Invalid email"), 400
        new_email = sanitize_email(data.get("email"))
        try:
            mailbox_utils.request_mailbox_email_change(user, mailbox, new_email)
        except mailbox_utils.TooManyVerificationEmails as e:
            return jsonify(error=e.msg), 429
        except mailbox_utils.MailboxError as e:
            return jsonify(error=e.msg), 400
        except SMTPRecipientsRefused:
            return jsonify(error=f"Incorrect mailbox, please recheck {new_email}"), 400
        else:
            mailbox.new_email = new_email
            changed = True

    if "pgp_public_key" in data:
        public_key = data.get("pgp_public_key")
        if public_key is None or public_key == "":
            mailbox.pgp_public_key = None
            mailbox.pgp_finger_print = None
            mailbox.disable_pgp = False
            audit_message = f"Remove PGP key from mailbox {mailbox.id}"
        else:
            if not isinstance(public_key, str) or len(public_key) > 100_000:
                return jsonify(error="Invalid pgp_public_key"), 400
            try:
                fingerprint = load_public_key_and_check(public_key)
            except PGPException:
                return jsonify(error="Cannot add the public key, please verify it"), 400
            mailbox.pgp_public_key = public_key
            mailbox.pgp_finger_print = fingerprint
            audit_message = f"Add PGP key {fingerprint} to mailbox {mailbox.id}"
        emit_user_audit_log(
            user=user, action=UserAuditLogAction.UpdateMailbox, message=audit_message
        )
        changed = True

    if "disable_pgp" in data:
        if not isinstance(data.get("disable_pgp"), bool):
            return jsonify(error="Invalid disable_pgp"), 400
        mailbox.disable_pgp = data.get("disable_pgp")
        changed = True

    if "generic_subject" in data:
        generic_subject = data.get("generic_subject") or None
        if not is_optional_str(generic_subject) or (
            generic_subject and len(generic_subject) > 78
        ):
            return jsonify(error="Invalid generic_subject"), 400
        mailbox.generic_subject = generic_subject
        changed = True

    if "cancel_email_change" in data:
        cancel_email_change = data.get("cancel_email_change")
        if not isinstance(cancel_email_change, bool):
            return jsonify(error="Invalid cancel_email_change"), 400
        if cancel_email_change:
            mailbox_utils.cancel_email_change(mailbox.id, user)
            changed = True

    if changed:
        Session.commit()

    return jsonify(updated=True), 200


@api_bp.route("/mailboxes", methods=["GET"])
@require_api_auth
def get_mailboxes():
    """
    Get verified mailboxes
    Output:
        - mailboxes: list of mailbox dict
    """
    user = g.user

    return (
        jsonify(mailboxes=[mailbox_to_dict(mb) for mb in user.mailboxes()]),
        200,
    )


@api_bp.route("/v2/mailboxes", methods=["GET"])
@require_api_auth
def get_mailboxes_v2():
    """
    Get all mailboxes - including unverified mailboxes
    Output:
        - mailboxes: list of mailbox dict
    """
    user = g.user
    mailboxes = []

    for mailbox in Mailbox.filter_by(user_id=user.id):
        mailboxes.append(mailbox)

    return (
        jsonify(mailboxes=[mailbox_to_dict(mb) for mb in mailboxes]),
        200,
    )

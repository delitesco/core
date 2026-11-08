from flask import jsonify, g
from sqlalchemy_utils.types.arrow import arrow

from app.api.base import api_bp, require_api_sudo
from app.constants import JobType
from app.log import LOG
from app.models import Job
from app.user_audit_log_utils import emit_user_audit_log, UserAuditLogAction


@api_bp.route("/user", methods=["DELETE"])
@require_api_sudo
def delete_user():
    """
    Delete the user. Requires sudo mode.

    """
    # Schedule delete account job
    emit_user_audit_log(
        user=g.user,
        action=UserAuditLogAction.UserMarkedForDeletion,
        message=f"Marked user {g.user.id} ({g.user.email}) for deletion from API",
    )
    LOG.w("schedule delete account job for %s", g.user)
    Job.create(
        name=JobType.DELETE_ACCOUNT.value,
        payload={"user_id": g.user.id},
        run_at=arrow.now(),
        commit=True,
    )
    return jsonify(ok=True)

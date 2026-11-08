from dataclasses import dataclass

from app.db import Session
from app.models import Alias, EmailLog, User


@dataclass
class Stats:
    nb_alias: int
    nb_forward: int
    nb_reply: int
    nb_block: int


def get_stats(user: User) -> Stats:
    nb_alias = Alias.filter_by(user_id=user.id, delete_on=None).count()  # noqa : E711
    nb_forward = (
        Session.query(EmailLog)
        .filter_by(user_id=user.id, is_reply=False, blocked=False, bounced=False)
        .count()
    )
    nb_reply = (
        Session.query(EmailLog)
        .filter_by(user_id=user.id, is_reply=True, blocked=False, bounced=False)
        .count()
    )
    nb_block = (
        Session.query(EmailLog)
        .filter_by(user_id=user.id, is_reply=False, blocked=True, bounced=False)
        .count()
    )

    return Stats(
        nb_alias=nb_alias, nb_forward=nb_forward, nb_reply=nb_reply, nb_block=nb_block
    )

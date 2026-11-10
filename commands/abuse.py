"""Look into abuse reports and act on them, from the server:

    docker compose exec sl-app python -m commands.abuse find <alias or reverse alias>
    docker compose exec sl-app python -m commands.abuse show <user>
    docker compose exec sl-app python -m commands.abuse top
    docker compose exec sl-app python -m commands.abuse block-replies <user> --reason "..."
    docker compose exec sl-app python -m commands.abuse unblock-replies <user> --reason "..."
    docker compose exec sl-app python -m commands.abuse disable-alias <alias> --reason "..."

<user> is the core user id, which the app keeps as accounts.core_user_id. Every action is
written to the abuser or alias audit log with its reason.
"""

import argparse
import sys

import arrow

from app.abuse_report import REPLY_COLUMNS, activity_of, describe_account, reply_senders
from app.alias_utils import change_alias_status
from app.models import AbuserAuditLog, Alias, Contact, Mailbox, User
from app.reply_limits import block_replies, unblock_replies
from server import create_light_app


def user_or_exit(user_id: int) -> User:
    user = User.get(user_id)
    if not user:
        sys.exit(f"No user {user_id}")
    return user


def find(address: str) -> None:
    address = address.strip().lower()
    alias = Alias.get_by(email=address)
    if alias:
        print(f"Alias of user {alias.user_id}, enabled: {alias.enabled}")
        return
    contact = Contact.get_by(reply_email=address)
    if contact:
        print(
            f"Reverse alias of user {contact.user_id}, alias {contact.alias.email}, "
            f"contact {contact.website_email}"
        )
        return
    sys.exit(f"{address} is neither an alias nor a reverse alias")


def show(user_id: int) -> None:
    user = user_or_exit(user_id)
    now = arrow.now()
    aliases = Alias.filter_by(user_id=user.id)
    print(f"User {user.id}, created {user.created_at.format('YYYY-MM-DD')}")
    print(f"State: {user.account_state.name}, {describe_account(user)}")
    if user.replies_blocked_at:
        print(
            f"Replies blocked since {user.replies_blocked_at.format('YYYY-MM-DD HH:mm')}"
        )
    print(
        f"Aliases: {aliases.count()}, disabled {aliases.filter_by(enabled=False).count()}; "
        f"mailboxes: {Mailbox.filter_by(user_id=user.id).count()}"
    )
    print(f"Last 24 hours: {activity_of(user, now.shift(days=-1))}")
    print(f"Last 7 days: {activity_of(user, now.shift(days=-7))}")

    log = AbuserAuditLog.filter_by(user_id=user.id).order_by(AbuserAuditLog.created_at)
    for entry in log:
        print(
            f"  {entry.created_at.format('YYYY-MM-DD HH:mm')} {entry.action}: {entry.message}"
        )


def top(hours: int) -> None:
    since = arrow.now().shift(hours=-hours)
    print(f"Most replies in {hours} hours")
    print(REPLY_COLUMNS)
    print("\n".join(reply_senders(since, worst_first=False, limit=20)))
    print(f"\nMost replies refused as spam or bounced in {hours} hours")
    print(REPLY_COLUMNS)
    print("\n".join(reply_senders(since, worst_first=True, limit=20)))


def disable_alias(address: str, reason: str) -> None:
    alias = Alias.get_by(email=address.strip().lower())
    if not alias:
        sys.exit(f"No alias {address}")
    change_alias_status(alias, enabled=False, message=f"admin: {reason}", commit=True)
    print(f"Disabled {alias.email} of user {alias.user_id}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m commands.abuse")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("find").add_argument("address")
    commands.add_parser("show").add_argument("user_id", type=int)
    commands.add_parser("top").add_argument("--hours", type=int, default=24)
    for name in ("block-replies", "unblock-replies"):
        command = commands.add_parser(name)
        command.add_argument("user_id", type=int)
        command.add_argument("--reason", required=True)
    command = commands.add_parser("disable-alias")
    command.add_argument("address")
    command.add_argument("--reason", required=True)

    args = parser.parse_args()
    with create_light_app().app_context():
        if args.command == "find":
            find(args.address)
        elif args.command == "show":
            show(args.user_id)
        elif args.command == "top":
            top(args.hours)
        elif args.command == "block-replies":
            block_replies(user_or_exit(args.user_id), f"admin: {args.reason}")
            print(f"Blocked replies of user {args.user_id}")
        elif args.command == "unblock-replies":
            unblock_replies(user_or_exit(args.user_id), f"admin: {args.reason}")
            print(f"Unblocked replies of user {args.user_id}")
        elif args.command == "disable-alias":
            disable_alias(args.address, args.reason)


if __name__ == "__main__":
    main()

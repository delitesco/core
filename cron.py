import argparse
import asyncio
import urllib.parse
from typing import List

import arrow
import requests
from sqlalchemy import func, or_, and_
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import joinedload
from sqlalchemy.sql import Insert, text

from app import s3, config
from app.alias_utils import nb_email_log_for_mailbox
from app.db import Session
from app.email_utils import (
    send_email,
    render,
    email_can_be_used_as_mailbox,
    get_email_domain_part,
)
from app.email_validation import is_valid_email, normalize_reply_email
from app.log import LOG
from app.mail_sender import load_unsent_mails_from_fs_and_resend
from app.models import (
    User,
    Alias,
    EmailLog,
    CustomDomain,
    RefusedEmail,
    Mailbox,
    Contact,
    TransactionalEmail,
    Bounce,
    SLDomain,
    DeletedAlias,
    DomainDeletedAlias,
    Hibp,
    HibpNotifiedAlias,
)
from app.pgp_utils import load_public_key_and_check, PGPException
from app.user_audit_log_utils import emit_user_audit_log, UserAuditLogAction
from app.utils import sanitize_email
from server import create_light_app
from tasks.check_custom_domains import check_all_custom_domains
from tasks.clean_alias_audit_log import cleanup_alias_audit_log
from tasks.clean_user_audit_log import cleanup_user_audit_log
from tasks.cleanup_alias import cleanup_alias
from tasks.cleanup_old_imports import cleanup_old_imports
from tasks.cleanup_old_jobs import cleanup_old_jobs
from tasks.cleanup_old_notifications import cleanup_old_notifications

DELETE_GRACE_DAYS = 30


def delete_logs():
    """delete everything that are considered logs"""
    delete_refused_emails()

    for t_email in TransactionalEmail.filter(
        TransactionalEmail.created_at < arrow.now().shift(days=-7)
    ):
        TransactionalEmail.delete(t_email.id)

    for b in Bounce.filter(Bounce.created_at < arrow.now().shift(days=-7)):
        Bounce.delete(b.id)

    Session.commit()

    LOG.d("Deleting EmailLog older than 2 weeks")

    total_deleted = 0
    batch_size = 500
    Session.execute("set session statement_timeout=30000").rowcount
    queries_done = 0
    cutoff_time = arrow.now().shift(days=-14)
    rows_to_delete = EmailLog.filter(EmailLog.created_at < cutoff_time).count()
    expected_queries = int(rows_to_delete / batch_size)
    sql = text(
        "DELETE FROM email_log WHERE id IN (SELECT id FROM email_log WHERE created_at < :cutoff_time order by created_at limit :batch_size)"
    )
    str_cutoff_time = cutoff_time.isoformat()
    while total_deleted < rows_to_delete:
        deleted_count = Session.execute(
            sql, {"cutoff_time": str_cutoff_time, "batch_size": batch_size}
        ).rowcount
        Session.commit()
        total_deleted += deleted_count
        queries_done += 1
        LOG.i(
            f"[{queries_done}/{expected_queries}] Deleted {total_deleted} EmailLog entries"
        )
        if deleted_count < batch_size:
            break

    LOG.i("Deleted %s email logs", total_deleted)


def delete_refused_emails():
    for refused_email in (
        RefusedEmail.filter_by(deleted=False).order_by(RefusedEmail.id).all()
    ):
        if arrow.now().shift(days=1) > refused_email.delete_at >= arrow.now():
            LOG.d("Delete refused email %s", refused_email)
            if refused_email.path:
                s3.delete(refused_email.path)

            s3.delete(refused_email.full_report_path)

            # do not set path and full_report_path to null
            # so we can check later that the files are indeed deleted
            refused_email.delete_at = arrow.now()
            refused_email.deleted = True
            Session.commit()

    LOG.d("Finish delete_refused_emails")


def migrate_domain_trash():
    """Move aliases from global trash to domain trash if applicable"""

    # ignore duplicate when insert
    # copied from https://github.com/sqlalchemy/sqlalchemy/issues/5374
    @compiles(Insert, "postgresql")
    def postgresql_on_conflict_do_nothing(insert, compiler, **kw):
        statement = compiler.visit_insert(insert, **kw)
        # IF we have a "RETURNING" clause, we must insert before it
        returning_position = statement.find("RETURNING")
        if returning_position >= 0:
            return (
                statement[:returning_position]
                + "ON CONFLICT DO NOTHING "
                + statement[returning_position:]
            )
        else:
            return statement + " ON CONFLICT DO NOTHING"

    sl_domains = [sl.domain for sl in SLDomain.all()]
    count = 0
    domain_deleted_aliases = []
    deleted_alias_ids = []
    for deleted_alias in DeletedAlias.yield_per_query():
        if count % 1000 == 0:
            LOG.d("process %s", count)

        count += 1

        alias_domain = get_email_domain_part(deleted_alias.email)
        if alias_domain not in sl_domains:
            custom_domain = CustomDomain.get_by(domain=alias_domain)
            if custom_domain:
                LOG.w("move %s to domain %s trash", deleted_alias, custom_domain)
                domain_deleted_aliases.append(
                    DomainDeletedAlias(
                        user_id=custom_domain.user_id,
                        email=deleted_alias.email,
                        domain_id=custom_domain.id,
                        created_at=deleted_alias.created_at,
                    )
                )
                deleted_alias_ids.append(deleted_alias.id)

    LOG.d("create %s DomainDeletedAlias", len(domain_deleted_aliases))
    Session.bulk_save_objects(domain_deleted_aliases)

    LOG.d("delete %s DeletedAlias", len(deleted_alias_ids))
    DeletedAlias.filter(DeletedAlias.id.in_(deleted_alias_ids)).delete(
        synchronize_session=False
    )

    Session.commit()


def set_custom_domain_for_alias():
    """Go through all aliases and make sure custom_domain is correctly set"""
    sl_domains = [sl_domain.domain for sl_domain in SLDomain.all()]
    for alias in Alias.yield_per_query().filter(Alias.custom_domain_id.is_(None)):
        if (
            not any(alias.email.endswith(f"@{sl_domain}") for sl_domain in sl_domains)
            and not alias.custom_domain_id
        ):
            alias_domain = get_email_domain_part(alias.email)
            custom_domain = CustomDomain.get_by(domain=alias_domain)
            if custom_domain:
                LOG.e("set %s for %s", custom_domain, alias)
                alias.custom_domain_id = custom_domain.id
            else:  # phantom domain
                LOG.d("phantom domain %s %s %s", alias.user, alias, alias.enabled)

    Session.commit()


def sanitize_alias_address_name():
    count = 0
    # using Alias.all() will take all the memory
    for alias in Alias.yield_per_query():
        if count % 1000 == 0:
            LOG.d("process %s", count)

        count += 1
        if sanitize_email(alias.email) != alias.email:
            LOG.e("Alias %s email not sanitized", alias)

        if alias.name and "\n" in alias.name:
            alias.name = alias.name.replace("\n", "")
            Session.commit()
            LOG.e("Alias %s name contains linebreak %s", alias, alias.name)


def sanity_check():
    LOG.d("sanitize user email")
    for user in User.filter_by(activated=True).all():
        if sanitize_email(user.email) != user.email:
            LOG.e("%s does not have sanitized email", user)

    LOG.d("sanitize alias address & name")
    sanitize_alias_address_name()

    LOG.d("sanity contact address")
    contact_email_sanity_date = arrow.get("2021-01-12")
    for contact in Contact.yield_per_query():
        if sanitize_email(contact.reply_email) != contact.reply_email:
            LOG.e("Contact %s reply-email not sanitized", contact)

        if (
            sanitize_email(contact.website_email, not_lower=True)
            != contact.website_email
            and contact.created_at > contact_email_sanity_date
        ):
            LOG.e("Contact %s website-email not sanitized", contact)

        if not contact.invalid_email and not is_valid_email(contact.website_email):
            LOG.e("%s invalid email", contact)
            contact.invalid_email = True
    Session.commit()

    LOG.d("sanitize mailbox address")
    for mailbox in Mailbox.yield_per_query():
        if sanitize_email(mailbox.email) != mailbox.email:
            LOG.e("Mailbox %s address not sanitized", mailbox)

    LOG.d("normalize reverse alias")
    for contact in Contact.yield_per_query():
        if normalize_reply_email(contact.reply_email) != contact.reply_email:
            LOG.e(
                "Contact %s reply email is not normalized %s",
                contact,
                contact.reply_email,
            )

    LOG.d("clean domain name")
    for domain in CustomDomain.yield_per_query():
        if domain.name and "\n" in domain.name:
            LOG.e("Domain %s name contain linebreak %s", domain, domain.name)

    LOG.d("migrate domain trash if needed")
    migrate_domain_trash()

    LOG.d("fix custom domain for alias")
    set_custom_domain_for_alias()

    LOG.d("check mailbox valid domain")
    check_mailbox_valid_domain()

    LOG.d("check mailbox valid PGP keys")
    check_mailbox_valid_pgp_keys()

    LOG.d(
        """check if there's an email that starts with "\u200f" (right-to-left mark (RLM))"""
    )
    for contact in (
        Contact.yield_per_query()
        .filter(Contact.website_email.startswith("\u200f"))
        .all()
    ):
        contact.website_email = contact.website_email.replace("\u200f", "")
        LOG.e("remove right-to-left mark (RLM) from %s", contact)
    Session.commit()

    LOG.d("Finish sanity check")


def check_mailbox_valid_domain():
    """detect if there's mailbox that's using an invalid domain"""
    mailbox_ids = (
        Session.query(Mailbox.id)
        .filter(Mailbox.verified.is_(True), Mailbox.disabled.is_(False))
        .all()
    )
    mailbox_ids = [e[0] for e in mailbox_ids]
    # iterate over id instead of mailbox directly
    # as a mailbox can be deleted in the meantime
    for mailbox_id in mailbox_ids:
        mailbox = Mailbox.get(mailbox_id)
        # a mailbox has been deleted
        if not mailbox:
            continue

        if email_can_be_used_as_mailbox(mailbox.email):
            LOG.d("Mailbox %s valid", mailbox)
            mailbox.nb_failed_checks = 0
        else:
            mailbox.nb_failed_checks += 1
            nb_email_log = nb_email_log_for_mailbox(mailbox)

            LOG.w(
                "issue with mailbox %s domain. #alias %s, nb email log %s",
                mailbox,
                mailbox.nb_alias(),
                nb_email_log,
            )

            # send a warning
            if mailbox.nb_failed_checks == 5:
                if mailbox.user.email != mailbox.email:
                    send_email(
                        mailbox.user.email,
                        f"Mailbox {mailbox.email} is disabled",
                        render(
                            "transactional/disable-mailbox-warning.txt.jinja2",
                            user=mailbox.user,
                            mailbox=mailbox,
                        ),
                        render(
                            "transactional/disable-mailbox-warning.html",
                            user=mailbox.user,
                            mailbox=mailbox,
                        ),
                        retries=3,
                    )

            # alert if too much fail and nb_email_log > 100
            if mailbox.nb_failed_checks > 10 and nb_email_log > 100:
                mailbox.disabled = True

                if mailbox.user.email != mailbox.email:
                    send_email(
                        mailbox.user.email,
                        f"Mailbox {mailbox.email} is disabled",
                        render(
                            "transactional/disable-mailbox.txt.jinja2", mailbox=mailbox
                        ),
                        render("transactional/disable-mailbox.html", mailbox=mailbox),
                        retries=3,
                    )

        Session.commit()


def check_mailbox_valid_pgp_keys():
    mailbox_ids = (
        Session.query(Mailbox.id)
        .filter(
            Mailbox.verified.is_(True),
            Mailbox.pgp_public_key.isnot(None),
            Mailbox.disable_pgp.is_(False),
        )
        .all()
    )
    mailbox_ids = [e[0] for e in mailbox_ids]
    # iterate over id instead of mailbox directly
    # as a mailbox can be deleted in the meantime
    for mailbox_id in mailbox_ids:
        mailbox = Mailbox.get(mailbox_id)
        # a mailbox has been deleted
        if not mailbox:
            LOG.d(f"Mailbox {mailbox_id} not found")
            continue

        LOG.d(f"Checking PGP key for {mailbox}")

        try:
            load_public_key_and_check(mailbox.pgp_public_key)
        except PGPException:
            LOG.i(f"{mailbox} PGP key invalid")
            send_email(
                mailbox.user.email,
                f"Mailbox {mailbox.email}'s PGP Key is invalid",
                render(
                    "transactional/invalid-mailbox-pgp-key.txt.jinja2",
                    user=mailbox.user,
                    mailbox=mailbox,
                ),
                retries=3,
            )


async def _hibp_check(api_key: str, queue: asyncio.Queue):
    """
    Uses a single API key to check the queue as fast as possible.

    This function to be ran simultaneously (multiple _hibp_check functions with different keys on the same queue) to make maximum use of multiple API keys.
    """
    default_rate_sleep = (60.0 / config.HIBP_RPM) + 0.1
    rate_sleep = default_rate_sleep
    rate_hit_counter = 0
    while True:
        try:
            alias_id = queue.get_nowait()
        except asyncio.QueueEmpty:
            return

        alias = Alias.get(alias_id)
        if not alias:
            continue
        user = alias.user
        if user.disabled or not user.is_premium():
            # Mark it as hibp done to skip it as if it had been checked
            alias.hibp_last_check = arrow.utcnow()
            Session.commit()
            continue

        LOG.d("Checking HIBP for %s", alias)

        request_headers = {
            "user-agent": "SimpleLogin",
            "hibp-api-key": api_key,
        }
        r = requests.get(
            f"https://haveibeenpwned.com/api/v3/breachedaccount/{urllib.parse.quote(alias.email)}",
            headers=request_headers,
        )
        if r.status_code == 200:
            # Breaches found
            alias.hibp_breaches = [
                Hibp.get_by(name=entry["Name"]) for entry in r.json()
            ]
            if len(alias.hibp_breaches) > 0:
                LOG.w("%s appears in HIBP breaches %s", alias, alias.hibp_breaches)
            if rate_hit_counter > 0:
                rate_hit_counter -= 1
        elif r.status_code == 404:
            # No breaches found
            alias.hibp_breaches = []
        elif r.status_code == 429:
            # rate limited
            LOG.w("HIBP rate limited, check alias %s in the next run", alias)
            rate_hit_counter += 1
            rate_sleep = default_rate_sleep + (0.2 * rate_hit_counter)
            if rate_hit_counter > 10:
                LOG.w(f"HIBP rate limited too many times stopping with alias {alias}")
                return
            # Just sleep for a while
            asyncio.sleep(5)
        elif r.status_code > 500:
            LOG.w("HIBP server 5** error %s", r.status_code)
            return
        else:
            LOG.error(
                "An error occurred while checking alias %s: %s - %s",
                alias,
                r.status_code,
                r.text,
            )
            return

        alias.hibp_last_check = arrow.utcnow()
        Session.add(alias)
        Session.commit()

        LOG.d("Updated breach info for %s", alias)
        await asyncio.sleep(rate_sleep)


def get_alias_to_check_hibp(
    oldest_hibp_allowed: arrow.Arrow,
    user_ids_to_skip: list[int],
    min_alias_id: int,
    max_alias_id: int,
):
    alias_query = (
        Session.query(Alias)
        .join(User, User.id == Alias.user_id)
        .filter(
            or_(
                Alias.hibp_last_check.is_(None),
                Alias.hibp_last_check < oldest_hibp_allowed,
            ),
            Alias.user_id.notin_(user_ids_to_skip),
            Alias.enabled,
            Alias.delete_on == None,  # noqa: E711
            Alias.id >= min_alias_id,
            Alias.id < max_alias_id,
            User.disabled == False,  # noqa: E712
            User.enable_data_breach_check,
        )
    )
    for alias in (
        alias_query.order_by(Alias.id.asc()).enable_eagerloads(False).yield_per(500)
    ):
        yield alias


async def check_hibp():
    """
    Check all aliases on the HIBP (Have I Been Pwned) API
    """
    LOG.d("Checking HIBP API for aliases in breaches")

    if len(config.HIBP_API_KEYS) == 0:
        LOG.e("No HIBP API keys")
        return

    LOG.d("Updating list of known breaches")
    r = requests.get("https://haveibeenpwned.com/api/v3/breaches")
    for entry in r.json():
        hibp_entry = Hibp.get_or_create(name=entry["Name"])
        hibp_entry.date = arrow.get(entry["BreachDate"])
        hibp_entry.description = entry["Description"]

    Session.commit()
    LOG.d("Updated list of known breaches")

    LOG.d("Getting the list of users to skip")
    query = "select u.id, count(a.id) from users u, alias a where a.user_id=u.id group by u.id having count(a.id) > :max_alias"
    rows = Session.execute(query, {"max_alias": config.HIBP_MAX_ALIAS_CHECK})
    user_ids = [row[0] for row in rows]
    LOG.d("Got %d users to skip" % len(user_ids))

    LOG.d("Checking aliases")
    queue = asyncio.Queue()
    min_alias_id = 0
    max_alias_id = Session.query(func.max(Alias.id)).scalar()
    step = 10000
    now = arrow.now()
    oldest_hibp_allowed = now.shift(days=-config.HIBP_SCAN_INTERVAL_DAYS)
    alias_checked = 0
    for alias_batch_id in range(min_alias_id, max_alias_id, step):
        for alias in get_alias_to_check_hibp(
            oldest_hibp_allowed, user_ids, alias_batch_id, alias_batch_id + step
        ):
            await queue.put(alias.id)

        alias_checked += queue.qsize()
        LOG.d(
            f"Need to check about {queue.qsize()} aliases in this loop {alias_batch_id}/{max_alias_id}"
        )

        # Start one checking process per API key
        # Each checking process will take one alias from the queue, get the info
        # and then sleep for 1.5 seconds (due to HIBP API request limits)
        checkers = []
        for i in range(len(config.HIBP_API_KEYS)):
            checker = asyncio.create_task(
                _hibp_check(
                    config.HIBP_API_KEYS[i],
                    queue,
                )
            )
            checkers.append(checker)

        # Wait until all checking processes are done
        for checker in checkers:
            await checker

    LOG.d(f"Done checking {alias_checked} HIBP API for aliases in breaches")


def notify_hibp():
    """
    Send aggregated email reports for HIBP breaches
    """
    # to get a list of users that have at least a breached alias
    alias_query = (
        Session.query(Alias)
        .options(joinedload(Alias.hibp_breaches))
        .filter(Alias.hibp_breaches.any())
        .filter(Alias.id.notin_(Session.query(HibpNotifiedAlias.alias_id)))
        .distinct(Alias.user_id)
        .all()
    )

    user_ids = [alias.user_id for alias in alias_query]

    for user in User.filter(User.id.in_(user_ids)):
        breached_aliases = (
            Session.query(Alias)
            .options(joinedload(Alias.hibp_breaches))
            .filter(Alias.hibp_breaches.any(), Alias.user_id == user.id)
            .all()
        )

        LOG.d(
            "Send new breaches found email to %s for %s breaches aliases",
            user,
            len(breached_aliases),
        )

        send_email(
            user.email,
            "You were in a data breach",
            render(
                "transactional/hibp-new-breaches.txt.jinja2",
                user=user,
                breached_aliases=breached_aliases,
            ),
            render(
                "transactional/hibp-new-breaches.html",
                user=user,
                breached_aliases=breached_aliases,
            ),
            retries=3,
        )

        # add the breached aliases to HibpNotifiedAlias to avoid sending another email
        for alias in breached_aliases:
            HibpNotifiedAlias.create(user_id=user.id, alias_id=alias.id)
        Session.commit()


def clear_users_scheduled_to_be_deleted(dry_run=False):
    users: List[User] = User.filter(
        and_(
            User.delete_on.isnot(None),
            User.delete_on <= arrow.now().shift(days=-DELETE_GRACE_DAYS),
        )
    ).all()
    for user in users:
        LOG.i(
            f"Scheduled deletion of user {user} with scheduled delete on {user.delete_on}"
        )
        if dry_run:
            continue
        emit_user_audit_log(
            user=user,
            action=UserAuditLogAction.DeleteUser,
            message=f"Delete user {user.id} ({user.email})",
        )
        User.delete(user.id)
        Session.commit()


def delete_old_data():
    oldest_valid = arrow.now().shift(days=-config.KEEP_OLD_DATA_DAYS)
    cleanup_old_imports(oldest_valid)
    cleanup_old_jobs(oldest_valid)
    cleanup_old_notifications(oldest_valid)


def clear_alias_audit_log():
    oldest_valid = arrow.now().shift(days=-config.AUDIT_LOG_MAX_DAYS)
    cleanup_alias_audit_log(oldest_valid)


def clear_user_audit_log():
    oldest_valid = arrow.now().shift(days=-config.AUDIT_LOG_MAX_DAYS)
    cleanup_user_audit_log(oldest_valid)


def clear_aliases_pending_to_be_deleted():
    oldest_valid = arrow.now().shift(days=-config.ALIAS_TRASH_DAYS)
    cleanup_alias(oldest_valid)


if __name__ == "__main__":
    LOG.d("Start running cronjob")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-j",
        "--job",
        help="Choose a cron job to run",
        type=str,
    )
    args = parser.parse_args()
    # wrap in an app context to benefit from app setup like database cleanup, sentry integration, etc
    with create_light_app().app_context():
        if args.job == "delete_logs":
            LOG.d("Deleted Logs")
            delete_logs()
        elif args.job == "delete_old_data":
            LOG.d("Delete old data")
            delete_old_data()
        elif args.job == "sanity_check":
            LOG.d("Check data consistency")
            sanity_check()
        elif args.job == "check_custom_domain":
            LOG.d("Check custom domain")
            check_all_custom_domains()
        elif args.job == "check_hibp":
            LOG.d("Check HIBP")
            asyncio.run(check_hibp())
        elif args.job == "notify_hibp":
            LOG.d("Notify users about HIBP breaches")
            notify_hibp()
        elif args.job == "send_undelivered_mails":
            LOG.d("Sending undelivered emails")
            load_unsent_mails_from_fs_and_resend()
        elif args.job == "delete_scheduled_users":
            LOG.d("Deleting users scheduled to be deleted")
            clear_users_scheduled_to_be_deleted()
        elif args.job == "clear_alias_audit_log":
            LOG.d("Clearing alias audit log")
            clear_alias_audit_log()
        elif args.job == "clear_user_audit_log":
            LOG.d("Clearing user audit log")
            clear_user_audit_log()
        elif args.job == "clear_alias_delete_on":
            LOG.d("Clearing aliases pending to be deleted")
            clear_aliases_pending_to_be_deleted()

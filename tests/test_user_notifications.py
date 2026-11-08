"""What is addressed to an account (its placeholder email) becomes a notification."""

from flask import url_for

from app import config
from app.email_utils import send_email, send_email_with_rate_control
from app.mail_sender import mail_sender
from app.models import ApiKey, Notification, User


def _provisioned_user() -> User:
    from app.utils import random_string

    return User.create(
        email=f"{random_string(20)}@{config.PROVISIONED_USER_EMAIL_DOMAIN}",
        activated=True,
        flush=True,
    )


@mail_sender.store_emails_test_decorator
def test_email_to_account_becomes_notification(flask_client):
    user = _provisioned_user()
    send_email(user.email, "Domain deleted", "Line one\nLine <two>")

    assert len(mail_sender.get_stored_emails()) == 0
    notification = Notification.filter_by(user_id=user.id).one()
    assert notification.title == "Domain deleted"
    assert notification.message == "Line one<br>\nLine &lt;two&gt;"
    assert not notification.read


@mail_sender.store_emails_test_decorator
def test_rate_controlled_alert_becomes_notification_once(flask_client):
    user = _provisioned_user()
    for _ in range(3):
        send_email_with_rate_control(
            user, "test_alert", user.email, "Alert", "body", max_nb_alert=1
        )
    assert len(mail_sender.get_stored_emails()) == 0
    assert Notification.filter_by(user_id=user.id).count() == 1


@mail_sender.store_emails_test_decorator
def test_email_to_real_mailbox_is_still_an_email(flask_client):
    _provisioned_user()
    send_email("someone@mailbox.lan", "Hello", "body")
    assert len(mail_sender.get_stored_emails()) == 1


@mail_sender.store_emails_test_decorator
def test_notifications_are_listed_by_the_api(flask_client):
    user = _provisioned_user()
    other = _provisioned_user()
    api_key = ApiKey.create(user.id, name="test", commit=True)
    send_email(user.email, "For user", "body")
    send_email(other.email, "For other", "body")

    r = flask_client.get(
        url_for("api.get_notifications", page=0),
        headers={"Authentication": api_key.code},
    )
    assert r.status_code == 200
    titles = [n["title"] for n in r.json["notifications"]]
    assert titles == ["For user"]
    assert isinstance(r.json["notifications"][0]["created_at_timestamp"], int)

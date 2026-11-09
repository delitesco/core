import arrow

from app.email_utils import render
from app.models import Notification


def test_subject_in_alert_is_escaped():
    html = render(
        "transactional/spf-fail.html",
        user=None,
        ip="198.51.100.7",
        to_email="contact@example.com",
        subject='<a href="https://evil.example">click</a>',
        time=arrow.now(),
    )
    assert '<a href="https://evil.example">' not in html
    assert "&lt;a href=&#34;https://evil.example&#34;&gt;click&lt;/a&gt;" in html


def test_template_markup_is_kept():
    html = render(
        "transactional/verify-mailbox.html",
        user=None,
        mailbox_email="<i>me</i>@example.com",
        link=None,
        code="123456",
    )
    assert "<b>123456</b>" in html
    assert "<b>&lt;i&gt;me&lt;/i&gt;@example.com</b>" in html


def test_text_templates_are_not_escaped():
    text = render(
        "transactional/spf-fail.txt",
        user=None,
        alias="a@b.c",
        ip="198.51.100.7",
        to_email="contact@example.com",
        subject="a < b & c",
        time=arrow.now(),
    )
    assert "a < b & c" in text


def test_notification_values_are_escaped():
    class Alias:
        email = "alias@example.com"

    html = Notification.render(
        "notification/message-quarantine.html",
        from_header='"<script>x</script>" <a@b.c>',
        alias=Alias(),
    )
    assert "<script>" not in html
    assert "&lt;script&gt;" in html

from email import message_from_bytes
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from app.tracker_blocker import (
    clean_url,
    is_tracking_pixel,
    strip_trackers,
    strip_trackers_from_message,
)


@pytest.mark.parametrize(
    "tag",
    [
        '<img src="https://t.example/o.gif" width="1" height="1">',
        "<img src=https://t.example/o.gif width=0 height=0 />",
        '<img src="https://t.example/o.gif" width="1px" height="1px" alt="">',
        '<img src="https://t.example/o.gif" style="width:1px;height:1px">',
        '<img src="https://t.example/o.gif" style="display: none">',
        '<img src="https://t.example/o.gif" style="opacity:0">',
        '<img src="https://x.list-manage.com/track/open.php?u=1" width="600">',
    ],
)
def test_tracking_pixels(tag):
    assert is_tracking_pixel(tag)


@pytest.mark.parametrize(
    "tag",
    [
        '<img src="https://cdn.example/logo.png" width="120" height="40">',
        '<img src="https://cdn.example/logo.png" width="1" height="40">',
        '<img src="https://cdn.example/logo.png" style="opacity:0.5;width:1px">',
        '<img src="https://cdn.example/hero.jpg">',
    ],
)
def test_normal_images(tag):
    assert not is_tracking_pixel(tag)


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "https://shop.example/p?id=7&utm_source=news&utm_medium=email",
            "https://shop.example/p?id=7",
        ),
        ("https://shop.example/p?utm_source=news", "https://shop.example/p"),
        (
            "https://shop.example/p?utm_source=news&id=7#top",
            "https://shop.example/p?id=7#top",
        ),
        # HTML-escaped separators are kept as they are
        (
            "https://shop.example/p?a=1&amp;fbclid=XyZ&amp;b=2",
            "https://shop.example/p?a=1&amp;b=2",
        ),
        # signed parameters are not decoded or re-encoded
        (
            "https://list.example/unsub?sig=a%2Bb%3D%3D&t=1&gclid=1",
            "https://list.example/unsub?sig=a%2Bb%3D%3D&t=1",
        ),
        ("https://shop.example/p?id=7", "https://shop.example/p?id=7"),
        ("mailto:a@b.c?subject=utm_source", "mailto:a@b.c?subject=utm_source"),
    ],
)
def test_clean_url(url, expected):
    assert clean_url(url) == expected


def test_strip_trackers_keeps_the_rest_of_the_html():
    html = (
        '<p>Hi</p><img src="https://t.example/o.gif" width="1" height="1">'
        '<a class="btn" href="https://shop.example/?id=1&utm_campaign=x">Buy</a>'
        '<img src="https://cdn.example/logo.png" width="120" height="40">'
    )
    cleaned, stats = strip_trackers(html)
    assert cleaned == (
        "<p>Hi</p>"
        '<a class="btn" href="https://shop.example/?id=1">Buy</a>'
        '<img src="https://cdn.example/logo.png" width="120" height="40">'
    )
    assert (stats.pixels, stats.links) == (1, 1)


def test_strip_trackers_from_message_only_touches_html():
    msg = MIMEMultipart("alternative")
    plain = "Buy: https://shop.example/?utm_source=x"
    msg.attach(MIMEText(plain, "plain", "utf-8"))
    msg.attach(
        MIMEText(
            'Привет <img src="https://t.example/o.gif" width="1" height="1">'
            '<a href="https://shop.example/?utm_source=x">Buy</a>',
            "html",
            "utf-8",
        )
    )
    msg = message_from_bytes(msg.as_bytes())

    stats = strip_trackers_from_message(msg)

    assert stats.total == 2
    plain_part, html_part = msg.get_payload()
    assert plain_part.get_payload(decode=True).decode() == plain
    html = html_part.get_payload(decode=True).decode("utf-8")
    assert html == 'Привет <a href="https://shop.example/">Buy</a>'
    assert len(html_part.get_all("Content-Transfer-Encoding")) == 1


def test_attached_html_is_not_changed():
    msg = MIMEMultipart("mixed")
    msg.attach(MIMEText("see attachment", "plain"))
    attachment = MIMEText('<img src="x" width="1" height="1">', "html")
    attachment.add_header("Content-Disposition", "attachment", filename="page.html")
    msg.attach(attachment)
    assert strip_trackers_from_message(msg).total == 0

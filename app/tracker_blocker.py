"""Remove email trackers from the HTML parts of forwarded emails (user setting
block_trackers, off by default).

- tracking pixels: <img> of 0 or 1 pixel (attributes or style), hidden images, and
  images from known open-tracking addresses;
- tracking parameters in links (utm_*, fbclid, gclid...).

The HTML is not parsed and re-serialized: only the matched <img> tags and href
values change, everything else stays byte for byte. Query strings are cut at "&"
without decoding, so signed links (unsubscribe, confirmations) keep their other
parameters exactly. This can still change how an email looks (a layout spacer
image is removed like a pixel), hence the opt-in.

Removing a pixel does not hide the recipient's IP: other remote images still load
in the mail client. Images are never fetched by this service.
"""

import re
from dataclasses import dataclass
from email.message import Message
from typing import Tuple
from urllib.parse import urlsplit

from app.log import LOG

TRACKING_PARAM = re.compile(
    r"^(utm_[a-z0-9_]+|fbclid|gclid|dclid|gbraid|wbraid|msclkid|yclid|igshid"
    r"|mc_eid|mc_cid|_hsenc|_hsmi|mkt_tok|vero_id|oly_enc_id|oly_anon_id)$",
    re.I,
)

# open-tracking addresses (substring of the image URL); extend from real emails
TRACKING_IMAGE_URLS = (
    "list-manage.com/track/open",
    "mandrillapp.com/track/open",
    "sendgrid.net/wf/open",
    "mailtrack.io/trace",
    "sidekickopen",
    "track.hubspot.com/__ptq.gif",
    "facebook.com/tr?",
    "google-analytics.com/collect",
)

# A tag ends at the first ">" and may not contain "<": a scan for a tag then never goes
# past the next "<", so every character is looked at a bounded number of times and a
# crafted email ("<a <a <a ...") cannot make this quadratic.
MAX_TAG_LENGTH = 4000
_IMG_TAG = re.compile(r"<img\b[^<>]{0,%d}>" % MAX_TAG_LENGTH, re.I)
_A_TAG = re.compile(r"<a\b[^<>]{0,%d}>" % MAX_TAG_LENGTH, re.I)
_ATTR = re.compile(
    r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*("[^"]*"|'[^']*'|[^\s"'>]+)"""
)
# applied to a single <a> tag only
_HREF = re.compile(r"""(\shref\s*=\s*)("[^"]*"|'[^']*'|[^\s"'>]+)""", re.I)
_HIDDEN_STYLE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|opacity\s*:\s*0(?![.\d])", re.I
)
_STYLE_SIZE = re.compile(r"(?:^|[;\s])(width|height)\s*:\s*([0-9.]+)\s*(px)?\s*(?=;|$)", re.I)
_TINY_VALUES = {"0", "1", "0px", "1px"}
_QUERY_SEPARATOR = re.compile(r"(&amp;|&#38;|&)", re.I)


@dataclass
class TrackerStats:
    pixels: int = 0
    links: int = 0

    @property
    def total(self) -> int:
        return self.pixels + self.links


def _attributes(tag: str) -> dict:
    attrs = {}
    for name, value in _ATTR.findall(tag):
        if value[:1] in ("'", '"'):
            value = value[1:-1]
        attrs.setdefault(name.lower(), value.strip())
    return attrs


def is_tracking_pixel(tag: str) -> bool:
    attrs = _attributes(tag)
    width, height = attrs.get("width", ""), attrs.get("height", "")
    if width.lower() in _TINY_VALUES and height.lower() in _TINY_VALUES:
        return True
    style = attrs.get("style", "")
    if _HIDDEN_STYLE.search(style):
        return True
    sizes = {}
    for name, value, _ in _STYLE_SIZE.findall(style):
        sizes[name.lower()] = value
    if len(sizes) == 2 and all(v in ("0", "1") for v in sizes.values()):
        return True
    src = attrs.get("src", "").lower()
    return any(url in src for url in TRACKING_IMAGE_URLS)


def clean_url(url: str) -> str:
    """Drop tracking parameters; the rest of the URL is kept exactly."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme.lower() not in ("http", "https") or not parts.query:
        return url
    query_start = url.index("?")
    query_end = url.index("#", query_start) if "#" in url[query_start:] else len(url)
    query = url[query_start + 1 : query_end]

    tokens = _QUERY_SEPARATOR.split(query)
    params, separators = tokens[0::2], tokens[1::2]
    kept, kept_separators = [], []
    for index, param in enumerate(params):
        if TRACKING_PARAM.match(param.split("=", 1)[0]):
            continue
        if kept:
            kept_separators.append(separators[index - 1])
        kept.append(param)
    if len(kept) == len(params):
        return url

    new_query = kept[0] if kept else ""
    for separator, param in zip(kept_separators, kept[1:]):
        new_query += separator + param
    prefix = url[:query_start]
    suffix = url[query_end:]
    return f"{prefix}?{new_query}{suffix}" if new_query else prefix + suffix


def strip_trackers(html: str) -> Tuple[str, TrackerStats]:
    stats = TrackerStats()

    def remove_pixel(match):
        if is_tracking_pixel(match.group(0)):
            stats.pixels += 1
            return ""
        return match.group(0)

    def clean_href(match):
        value = match.group(2)
        quote = value[0] if value[:1] in ("'", '"') else ""
        url = value[1:-1] if quote else value
        cleaned = clean_url(url)
        if cleaned == url:
            return match.group(0)
        stats.links += 1
        return f"{match.group(1)}{quote}{cleaned}{quote}"

    def clean_link(match):
        return _HREF.sub(clean_href, match.group(0), count=1)

    html = _IMG_TAG.sub(remove_pixel, html)
    html = _A_TAG.sub(clean_link, html)
    return html, stats


_CONTAINERS = ("multipart/alternative", "multipart/related", "multipart/mixed")


def strip_trackers_from_message(msg: Message) -> TrackerStats:
    """Clean the text/html parts of msg in place. Plain text, attachments, attached
    emails and signed parts (multipart/signed) are not changed."""
    stats = TrackerStats()
    _strip_part(msg, stats)
    return stats


def _strip_part(part: Message, stats: TrackerStats):
    content_type = part.get_content_type()
    if content_type in _CONTAINERS:
        for sub_part in part.get_payload() or []:
            _strip_part(sub_part, stats)
        return
    if content_type != "text/html" or part.get_content_disposition() == "attachment":
        return

    charset = part.get_content_charset() or "utf-8"
    try:
        raw = part.get_payload(decode=True)
        html = raw.decode(charset)
    except (LookupError, UnicodeDecodeError, AttributeError):
        LOG.d("Tracker blocking: cannot decode a text/html part (%s)", charset)
        return

    cleaned, part_stats = strip_trackers(html)
    if part_stats.total == 0:
        return
    try:
        cleaned.encode(charset)
    except (LookupError, UnicodeEncodeError):
        return
    del part["Content-Transfer-Encoding"]
    part.set_payload(cleaned, charset=charset)
    stats.pixels += part_stats.pixels
    stats.links += part_stats.links

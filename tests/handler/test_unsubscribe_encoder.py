import pytest

from app import config
from app.handler.unsubscribe_encoder import (
    UnsubscribeData,
    UnsubscribeAction,
    UnsubscribeEncoder,
    UnsubscribeOriginalData,
)

legacy_subject_test_data = [
    ("3=", UnsubscribeData(UnsubscribeAction.DisableAlias, 3)),
    ("438_", UnsubscribeData(UnsubscribeAction.DisableContact, 438)),
    ("4325*", UnsubscribeData(UnsubscribeAction.UnsubscribeNewsletter, 4325)),
]


@pytest.mark.parametrize("expected_subject, expected_deco", legacy_subject_test_data)
def test_unsigned_subjects_are_refused(expected_subject, expected_deco):
    # anyone could forge them: only signed subjects are accepted
    assert UnsubscribeEncoder.decode_subject(expected_subject) is None
    assert UnsubscribeEncoder.decode_subject(expected_subject.rstrip("=_*")) is None


def test_tampered_signed_subject_is_refused():
    subject = UnsubscribeEncoder.encode_subject(UnsubscribeAction.DisableAlias, 3)
    assert UnsubscribeEncoder.decode_subject(subject).data == 3
    forged = UnsubscribeEncoder.encode_subject(UnsubscribeAction.DisableAlias, 4)
    tampered = forged.rsplit(".", 1)[0] + "." + subject.rsplit(".", 1)[1]
    assert UnsubscribeEncoder.decode_subject(tampered) is None


legacy_mail_or_link_test_data = [
    (
        None,
        False,
        UnsubscribeData(UnsubscribeAction.DisableAlias, 3),
    ),
    (
        "mailto:me@nowhere.net?subject=un.WzIsIDld.ONeJMiTW6CosJg4PMR1MPcDs-6GWoTOQFMfA2A",
        True,
        UnsubscribeData(UnsubscribeAction.DisableAlias, 9),
    ),
    (
        None,
        False,
        UnsubscribeData(UnsubscribeAction.DisableContact, 8),
    ),
    (
        "mailto:me@nowhere.net?subject=un.WzMsIDhd.eo_Ynk0eNyPtsHXMpTqw7HMFgYmm1Up_wWUc3g",
        True,
        UnsubscribeData(UnsubscribeAction.DisableContact, 8),
    ),
    (
        "mailto:me@nowhere.net?subject=un.WzEsIDgzXQ.NZAWqfpCmLEszwc5nWuQwDSLJ3TXO3rcOe_73Q",
        True,
        UnsubscribeData(UnsubscribeAction.UnsubscribeNewsletter, 83),
    ),
    (
        None,
        False,
        UnsubscribeData(
            UnsubscribeAction.OriginalUnsubscribeMailto,
            UnsubscribeOriginalData(1, "a@b.c", "subject"),
        ),
    ),
    (
        "mailto:me@nowhere.net?subject=un.WzQsIFswLCAxLCAiYUBiLmMiLCAic3ViamVjdCJdXQ.aU3T5XNzJIG4LDm6-pqJk4vxxJxpgVYzc9MEFQ",
        True,
        UnsubscribeData(
            UnsubscribeAction.OriginalUnsubscribeMailto,
            UnsubscribeOriginalData(1, "a@b.c", "subject"),
        ),
    ),
]


@pytest.mark.parametrize(
    "expected_link, via_mail, unsub_data", legacy_mail_or_link_test_data
)
def test_encode_legacy_link(monkeypatch, expected_link, via_mail, unsub_data):
    # the expected links were signed with FLASK_SECRET=secret
    monkeypatch.setattr(config, "UNSUBSCRIBE_SECRET", "secretunsub")
    if via_mail:
        config.UNSUBSCRIBER = "me@nowhere.net"
    else:
        config.UNSUBSCRIBER = None
    link_info = UnsubscribeEncoder.encode(unsub_data.action, unsub_data.data)
    if expected_link is None:
        # no web unsubscribe: without UNSUBSCRIBER there is no link at all
        assert link_info is None
        return
    assert via_mail == link_info.via_email
    assert expected_link == link_info.link


encode_decode_test_data = [
    UnsubscribeData(UnsubscribeAction.DisableContact, 3),
    UnsubscribeData(UnsubscribeAction.DisableContact, 10),
    UnsubscribeData(UnsubscribeAction.DisableAlias, 101),
    UnsubscribeData(
        UnsubscribeAction.OriginalUnsubscribeMailto,
        UnsubscribeOriginalData(323, "a@b.com", "some subject goes here"),
    ),
]


@pytest.mark.parametrize("unsub_data", encode_decode_test_data)
def test_encode_decode_unsub(unsub_data):
    encoded = UnsubscribeEncoder.encode_subject(unsub_data.action, unsub_data.data)
    decoded = UnsubscribeEncoder.decode_subject(encoded)
    assert unsub_data.action == decoded.action
    assert unsub_data.data == decoded.data

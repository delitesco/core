import base64
import enum
import hashlib
import json
from dataclasses import dataclass
from typing import Optional, Union

import itsdangerous

from app import config
from app.log import LOG

UNSUB_PREFIX = "un"


class UnsubscribeAction(enum.Enum):
    UnsubscribeNewsletter = 1
    DisableAlias = 2
    DisableContact = 3
    OriginalUnsubscribeMailto = 4


@dataclass
class UnsubscribeOriginalData:
    alias_id: int
    recipient: str
    subject: str


@dataclass
class UnsubscribeData:
    action: UnsubscribeAction
    data: Union[UnsubscribeOriginalData, int]


@dataclass
class UnsubscribeLink:
    link: str
    via_email: bool


class UnsubscribeEncoder:
    @staticmethod
    def encode(
        action: UnsubscribeAction,
        data: Union[int, UnsubscribeOriginalData],
    ) -> Optional[UnsubscribeLink]:
        """Signed mailto: link to the UNSUBSCRIBER address.

        There is no web page to unsubscribe from: without UNSUBSCRIBER there is no link.
        The mail handler only acts on these emails when they come from a mailbox (or an
        authorized address) of the alias owner.
        """
        if not config.UNSUBSCRIBER:
            return None
        return UnsubscribeLink(UnsubscribeEncoder.encode_mailto(action, data), True)

    @classmethod
    def encode_subject(
        cls, action: UnsubscribeAction, data: Union[int, UnsubscribeOriginalData]
    ) -> str:
        if action != UnsubscribeAction.OriginalUnsubscribeMailto and not isinstance(
            data, int
        ):
            raise ValueError(f"Data has to be an int for an action of type {action}")
        if action == UnsubscribeAction.OriginalUnsubscribeMailto:
            if type(data) is not UnsubscribeOriginalData:
                raise ValueError(
                    f"Data has to be an UnsubscribeOriginalData for an action of type {action}"
                )
            # Initial 0 is the version number. If we need to add support for extra use-cases we can bump up this number
            data = (0, data.alias_id, data.recipient, data.subject)
        payload = (action.value, data)
        serialized_data = (
            base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8"))
            .rstrip(b"=")
            .decode("utf-8")
        )
        signed_data = cls._get_signer().sign(serialized_data).decode("utf-8")
        encoded_request = f"{UNSUB_PREFIX}.{signed_data}"
        if len(encoded_request) > 512:
            LOG.w("Encoded request is longer than 512 chars")
        return encoded_request

    @staticmethod
    def encode_mailto(
        action: UnsubscribeAction, data: Union[int, UnsubscribeOriginalData]
    ) -> str:
        subject = UnsubscribeEncoder.encode_subject(action, data)
        return f"mailto:{config.UNSUBSCRIBER}?subject={subject}"

    @staticmethod
    def _get_signer() -> itsdangerous.Signer:
        return itsdangerous.Signer(
            config.UNSUBSCRIBE_SECRET, digest_method=hashlib.sha3_224
        )

    @classmethod
    def decode_subject(cls, data: str) -> Optional[UnsubscribeData]:
        # Only signed subjects: the old unsigned formats ("123=", "45_") let anyone who
        # knows a mailbox address disable its aliases by guessing ids.
        if data.find(UNSUB_PREFIX) == -1:
            return None

        signer = cls._get_signer()
        try:
            verified_data = signer.unsign(data[len(UNSUB_PREFIX) + 1 :])
        except itsdangerous.BadSignature:
            return None
        try:
            padded_data = verified_data + (b"=" * (-len(verified_data) % 4))
            payload = json.loads(base64.urlsafe_b64decode(padded_data))
        except ValueError:
            return None
        action = UnsubscribeAction(payload[0])
        action_data = payload[1]
        if action == UnsubscribeAction.OriginalUnsubscribeMailto:
            # Skip version number in action_data[0] for now it's always 0
            action_data = UnsubscribeOriginalData(
                action_data[1], action_data[2], action_data[3]
            )
        return UnsubscribeData(action, action_data)


def unsubscribe_action_link(
    action: UnsubscribeAction, data: Union[int, UnsubscribeOriginalData]
) -> Optional[str]:
    """mailto: link that performs `action` when sent from the alias owner's mailbox."""
    link = UnsubscribeEncoder.encode(action, data)
    return link.link if link else None

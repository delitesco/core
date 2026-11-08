import enum

HEADER_ALLOW_API_COOKIES = "X-Sl-Allowcookies"
DMARC_RECORD = "v=DMARC1; p=quarantine; pct=100; adkim=s; aspf=s"
HKDF_INFO_TEMPLATE = "enc_key.ab.sl.proton.me:%s"
AEAD_AAD_DATA = "data.ab.sl.proton.me"


class JobType(enum.Enum):
    DELETE_ACCOUNT = "delete-account"
    DELETE_MAILBOX = "delete-mailbox"
    DELETE_DOMAIN = "delete-domain"
    SEND_USER_REPORT = "send-user-report"
    ABUSER_MARK = "abuser-mark"

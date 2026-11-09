Some email services like Gmail, Proton Mail, etc don't have a strict SPF record (`-all`) to support the "classic" email forwarding
that is usually used for group mailing list. In this scenario, an email is sent to a group is forwarded as-is,
breaking therefore the SPF.

A malicious hacker could use this security fail to impersonate your alias via the reverse-alias. This rarely happens
as the reverse-alias is generated randomly and is unique for each sender.

To prevent this kind of attack, the mail handler enforces the SPF policy even if your mailbox uses a "soft" policy.
`ENFORCE_SPF` is on unless set to `false`, but it needs Postfix to record the client IP.

The Docker Compose setup in `deploy/` does all of this already (`deploy/postfix/client_ip_header.pcre`).
For another Postfix:

1) Install `postfix-pcre`

```bash
apt install -y postfix-pcre
```

2) Add `/etc/postfix/client_ip_header.pcre` with the following content

```
/^([0-9]{1,3}(\.[0-9]{1,3}){3})$/      PREPEND X-SimpleLogin-Client-IP: $1
/^([0-9a-fA-F]*:[0-9a-fA-F:.]+)$/       PREPEND X-SimpleLogin-Client-IP: $1
```

The header is prepended, so it is always the first `X-SimpleLogin-Client-IP` header of the message, and the mail
handler only reads the first one: a copy sent by the client is never used.

3) Add the following lines to your Postfix config file at `/etc/postfix/main.cf`

```
smtpd_client_restrictions =
    check_client_access pcre:/etc/postfix/client_ip_header.pcre,
    permit
```

4) Restart Postfix and the mail handler.

Without the header the check is skipped and a warning is logged for each reply, so add it before
relying on `ENFORCE_SPF`. Set `ENFORCE_SPF=false` only if Postfix can't see the real client address.

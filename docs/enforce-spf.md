Some email services like Gmail, Proton Mail, etc don't have a strict SPF record (`-all`) to support the "classic" email forwarding
that is usually used for group mailing list. In this scenario, an email is sent to a group is forwarded as-is,
breaking therefore the SPF.

A malicious hacker could use this security fail to impersonate your alias via the reverse-alias. This rarely happens
as the reverse-alias is generated randomly and is unique for each sender.

However if you want to prevent this kind of attack, you can enforce the SPF policy even if your mailbox uses a "soft" policy.

The Docker Compose setup in `deploy/` does all of this already (`deploy/postfix/client_ip_header.pcre`,
`ENFORCE_SPF=true` in `.env.example`). For another Postfix:

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

4) Enable `ENFORCE_SPF` in your config file

```
ENFORCE_SPF=true
```

5) Restart Postfix and the mail handler.

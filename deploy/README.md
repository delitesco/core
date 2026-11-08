# Running delitesco-core with Docker Compose

The layout follows the community SimpleLogin Docker Compose setup (same `.env` format).

| service         | what it does                                                          |
|-----------------|-----------------------------------------------------------------------|
| `sl-db`         | Postgres (volume `sl-db-data`)                                        |
| `sl-redis`      | rate limits and locks (required)                                      |
| `sl-init`       | runs once on each `up`: migrations, alias domains, DKIM key           |
| `sl-app`        | HTTP API, served by Traefik on `https://$SUBDOMAIN.$DOMAIN`           |
| `sl-email`      | mail handler, receives mail from Postfix                              |
| `sl-job-runner` | background jobs (account / mailbox / domain deletion)                 |
| `sl-cron`       | periodic cleanup jobs from `../crontab.yml`                           |
| `postfix`       | SMTP on port 25: receives mail for the alias and custom domains, sends forwarded mail |
| `traefik`       | HTTPS with Let's Encrypt (ports 80 and 443)                           |
| `certs-dumper`  | gives Traefik's certificate to Postfix for STARTTLS                   |

## 1. DNS

The API and the mail server can use different domains. Example: the API on `app.mydomain.com`
(`DOMAIN=mydomain.com`, `SUBDOMAIN=app`) and aliases on `myaliases.com` (`EMAIL_DOMAIN=myaliases.com`), the mail
server being `mail.myaliases.com` (`MAIL_HOSTNAME`, default `mail.$EMAIL_DOMAIN`):

- `A app.mydomain.com` → the server's IPv4
- `A mail.myaliases.com` → the server's IPv4 (no AAAA: Postfix sends over IPv4 only)
- PTR (reverse DNS) of the server's IP → `mail.myaliases.com`, set at the hosting provider
- `MX myaliases.com` → `10 mail.myaliases.com.`
- `TXT myaliases.com` → `v=spf1 mx -all`
- `TXT dkim._domainkey.myaliases.com`, and the same value on `dkim02._domainkey` and `dkim03._domainkey`
  (custom domains CNAME all three) → printed by `sl-init`
- `TXT _dmarc.myaliases.com` → `v=DMARC1; p=quarantine; adkim=s; aspf=s`

On every start `sl-init` prints these records and a `WARNING` line for each one that is missing or wrong:
`docker compose logs sl-init`. Custom domains get their records from `GET /api/custom_domains/<id>/dns`.

## 2. Configure

```bash
git clone https://github.com/delitesco/core.git
cd core/deploy
cp .env.example .env
# fill in .env; secrets: openssl rand -hex 32
```

Coming from a SimpleLogin `.env`: keep `DOMAIN`, `SUBDOMAIN`, `POSTGRES_*`, `FLASK_SECRET`, `LE_*`, DNS provider
tokens and `SPAMHAUS_DQS_KEY`. `LOCAL_FILE_UPLOAD`, `DISABLE_REGISTRATION`, `DISABLE_ONBOARDING`, `SL_VERSION`
and `SL_IMAGE` are not used; set the new `PROVISIONING_API_KEY`, `MAILBOX_VERIFY_URL`, `UNSUBSCRIBER` and
`API_ALLOWED_IPS`.

## 3. Existing DKIM key (optional)

To keep a DKIM key already published in DNS, copy it into the data volume before the first start
(volume names are prefixed with the directory name, `deploy`):

```bash
docker volume create deploy_sl-data
docker run --rm -v deploy_sl-data:/sl -v /path/to/keys:/src alpine \
  sh -c "cp /src/dkim.key /sl/dkim.key && cp /src/dkim.pub.key /sl/dkim.pub.key"
```

Otherwise `sl-init` generates one.

## 4. Start

Stop a Postfix installed on the host first (`sudo systemctl disable --now postfix`), it would hold port 25.

```bash
docker compose up -d --build
docker compose logs sl-init     # migrations and the DKIM DNS record
docker compose ps               # sl-init exited (0), the others are running
```

## 5. Check

```bash
curl https://app.mydomain.com/health
curl -X POST https://app.mydomain.com/api/provision/users -H "X-Provisioning-Key: $PROVISIONING_API_KEY"
docker compose logs -f postfix sl-email
```

Send an email from an outside mailbox to any address of the domain: Postfix should log
`relay=sl-email[...]:20381` and the mail handler answers (550 for an unknown address).

## Operations

- Firewall: open 25, 80 and 443. Port 25 outbound must not be blocked by the host. Restrict the API to the
  frontend backend with `API_ALLOWED_IPS`.
- Backups: volumes `deploy_sl-db-data` and `deploy_sl-data` (DKIM key, PGP keyring, stored refused emails).
- Update: `git pull && docker compose up -d --build` (`sl-init` applies new migrations first).
- Postfix switches from its self-signed certificate to the Let's Encrypt one within 5 minutes of it being
  issued, and reloads on renewal.
- Mail for paused accounts waits in the Postfix queue (the mail handler answers 421) and is delivered when the
  account is active again, or returned to the sender after 5 days.

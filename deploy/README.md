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
| `docker-socket-proxy` | read-only Docker API for Traefik, instead of the Docker socket  |
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

Service emails (mailbox confirmation, alerts) come from `NOREPLY` (default `noreply@$EMAIL_DOMAIN`). To send
them from a subdomain of your main domain, e.g. `NOREPLY=noreply@notify.mydomain.com`, publish the same DKIM key
there (`TXT dkim._domainkey.notify.mydomain.com`, printed by `sl-init`). Nothing else changes for that domain:
its MX and SPF can stay with another mail provider, as bounces and SPF use the alias domain.

On every start `sl-init` prints these records and a `WARNING` line for each one that is missing or wrong:
`docker compose logs sl-init`. Custom domains get their records from `GET /api/custom_domains/<id>/dns`.

## 2. Configure

```bash
git clone https://github.com/delitesco/core.git
cd core/deploy
cp .env.example .env
chmod 600 .env
# fill in .env; secrets: openssl rand -hex 32
# LE_CHALLENGE=dns only: cp traefik.env.example traefik.env, put the DNS provider token there
```

`sl-init` refuses to start while a value is still the example one, and the app refuses a `FLASK_SECRET` or
`PROVISIONING_API_KEY` shorter than 32 characters. `API_ALLOWED_IPS` is required: the address of the frontend
backend. `/api/provision/*` only answers `PROVISIONING_ALLOWED_IPS` (default: `10.79.0.0/24`, the `delitesco-api`
Docker network: a frontend on the same host joins it and calls `http://sl-app:7777` directly); allowing
any address (`0.0.0.0/0`) also needs `PROVISIONING_EMERGENCY_OPEN=true`, and every provisioning request then
logs an error — for emergencies only.

Coming from a SimpleLogin `.env`: keep `DOMAIN`, `SUBDOMAIN`, `POSTGRES_*`, `FLASK_SECRET`, `LE_*`, DNS provider
tokens and `SPAMHAUS_DQS_KEY`. `LOCAL_FILE_UPLOAD`, `DISABLE_REGISTRATION`, `DISABLE_ONBOARDING`, `SL_VERSION`
and `SL_IMAGE` are not used; set the new `PROVISIONING_API_KEY`, `MAILBOX_VERIFY_URL`, `UNSUBSCRIBER` and
`API_ALLOWED_IPS`, and move the DNS provider tokens to `traefik.env`.

## 3. Existing DKIM key (optional)

To keep a DKIM key already published in DNS, copy it into the data volume before the first start
(volume names are prefixed with the directory name, `deploy`):

```bash
docker volume create deploy_sl-data
docker run --rm -v deploy_sl-data:/sl -v /path/to/keys:/src alpine \
  sh -c "cp /src/dkim.key /sl/dkim.key && cp /src/dkim.pub.key /sl/dkim.pub.key"
```

Otherwise `sl-init` generates one (RSA 2048).

### Replacing a DKIM key (for example an old 1024-bit one)

Custom domains point all three selectors (`dkim`, `dkim02`, `dkim03`) to the alias domain, so a new key can be
published on a free selector first and switched to without breaking signatures:

```bash
docker compose run --rm --user 0 --entrypoint sh sl-init -c \
  "openssl genrsa -traditional -out /sl/dkim-new.key 2048 && \
   openssl rsa -in /sl/dkim-new.key -pubout -out /sl/dkim-new.pub.key && \
   chown 10001:10001 /sl/dkim-new.* && chmod 600 /sl/dkim-new.key && cat /sl/dkim-new.pub.key"
```

1. Publish `TXT dkim02._domainkey.<EMAIL_DOMAIN>` → `v=DKIM1; k=rsa; p=<the key above, without the BEGIN/END
   lines, on one line>`, and wait until it resolves (`dig TXT dkim02._domainkey.<EMAIL_DOMAIN>`).
2. Move the new key in place and sign with it:
   ```bash
   docker compose run --rm --user 0 --entrypoint sh sl-init -c \
     "mv /sl/dkim-new.key /sl/dkim.key && mv /sl/dkim-new.pub.key /sl/dkim.pub.key"
   ```
   then set `DKIM_SELECTOR=dkim02` in `.env` and run `docker compose up -d`.
3. A few days later (mail already sent has been checked), publish the new key on `dkim._domainkey` too.

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
# from the frontend backend (other addresses get 404):
curl -X POST https://app.mydomain.com/api/provision/users -H "X-Provisioning-Key: $PROVISIONING_API_KEY"
docker compose logs -f postfix sl-email
```

Send an email from an outside mailbox to any address of the domain: Postfix should log
`relay=sl-email[...]:20381` and the mail handler answers (550 for an unknown address).

## Operations

- Firewall: open 25, 80 and 443. Port 25 outbound must not be blocked by the host. Restrict the API to the
  frontend backend with `API_ALLOWED_IPS`.
- Client addresses: Traefik's allow list, the provisioning allow list and the SPF check of replies
  (`ENFORCE_SPF`) all need the real client IP. With Docker's default iptables port publishing it is kept;
  check that `docker compose logs postfix` shows outside IPs in `connect from`, not `10.77.0.1` or
  `10.78.0.1`.
- The app containers run as an unprivileged user (uid 10001); `sl-init` gives it the data volume.
- Postfix relays outgoing mail only for the app containers, which reach it on the separate `mail-out` network
  (`10.78.0.0/24`); Traefik and the socket proxy are not on that network.
- A frontend on the same host joins only `delitesco-api` (`10.79.0.0/24`), where it meets `sl-app` and Traefik.
  The `delitesco` network holds the database, Redis (password `REDIS_PASSWORD`) and the mail handler, which a
  frontend never needs.
- The app containers drop all Linux capabilities; `sl-app` is limited to 1 GB and 2 CPUs and `sl-email` to 2 GB and
  2 CPUs (`mem_limit`, `cpus` in `docker-compose.yml`), so neither can take the whole server.
- Abuse: replies are capped per account and day (`MAX_REPLIES_PER_DAY`, `MAX_REPLY_RECIPIENTS_PER_DAY`, lower
  for accounts younger than `NEW_ACCOUNT_DAYS`), and an account's replies are blocked after
  `REPLY_BLOCK_AFTER_SPAM` spam or `REPLY_BLOCK_AFTER_BOUNCES` bounced replies in a day; forwarding goes on.
  `ABUSE_REPORT_EMAIL` gets an alert for each block and a daily report at 06:00 UTC. To look into a complaint
  and act on it: `docker compose exec sl-app python -m commands.abuse --help`.
- Backups: volumes `deploy_sl-db-data` and `deploy_sl-data` (DKIM key, PGP keyring, stored refused emails).
- Update: `git pull && docker compose up -d --build` (`sl-init` applies new migrations first).
- Postfix switches from its self-signed certificate to the Let's Encrypt one within 5 minutes of it being
  issued, and reloads on renewal.
- Mail for paused accounts waits in the Postfix queue (the mail handler answers 421) and is delivered when the
  account is active again, or returned to the sender after 5 days.

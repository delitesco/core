#!/bin/sh
# Render the Postfix config from the environment, then run Postfix in the foreground.
set -eu

: "${DOMAIN:?DOMAIN is required}"
: "${APP_HOSTNAME:?APP_HOSTNAME is required}"
: "${POSTGRES_USER:?}" "${POSTGRES_PASSWORD:?}" "${POSTGRES_DB:?}"
export POSTGRES_HOST="${POSTGRES_HOST:-sl-db}"
export EMAIL_HANDLER="${EMAIL_HANDLER:-sl-email:20381}"
export DOCKER_SUBNET="${DOCKER_SUBNET:-10.78.0.0/24}"
export DOCKER_GATEWAY="${DOCKER_GATEWAY:-10.78.0.1}"

# Spamhaus: the Data Query Service needs a (free) key; the public zones refuse queries
# made through public DNS resolvers.
if [ -n "${SPAMHAUS_DQS_KEY:-}" ]; then
    export RBL_CHECKS="reject_rbl_client ${SPAMHAUS_DQS_KEY}.zen.dq.spamhaus.net=127.0.0.[2..255],
    reject_rhsbl_sender ${SPAMHAUS_DQS_KEY}.dbl.dq.spamhaus.net=127.0.1.[2..99],"
else
    export RBL_CHECKS="reject_rbl_client zen.spamhaus.org=127.0.0.[2..255],"
fi

# TLS: the certificate from Traefik (via certs-dumper) when it exists, otherwise a
# self-signed one so that STARTTLS works until the real one is issued.
WANTED_CERT="${TLS_CERT:-/etc/postfix/tls/fullchain.pem}"
WANTED_KEY="${TLS_KEY:-/etc/postfix/tls/privkey.pem}"
SELF_CERT=/etc/postfix/selfsigned/cert.pem
SELF_KEY=/etc/postfix/selfsigned/key.pem
if [ ! -f "$SELF_CERT" ]; then
    mkdir -p /etc/postfix/selfsigned
    openssl req -x509 -nodes -days 3650 -newkey rsa:2048 -subj "/CN=${APP_HOSTNAME}" \
        -keyout "$SELF_KEY" -out "$SELF_CERT" >/dev/null 2>&1
fi
if [ -f "$WANTED_CERT" ] && [ -f "$WANTED_KEY" ]; then
    export TLS_CERT="$WANTED_CERT" TLS_KEY="$WANTED_KEY"
else
    export TLS_CERT="$SELF_CERT" TLS_KEY="$SELF_KEY"
fi

for name in main.cf pgsql-relay-domains.cf pgsql-transport-maps.cf; do
    # only our variables: $myhostname etc. are Postfix's own
    envsubst '${DOMAIN} ${APP_HOSTNAME} ${POSTGRES_HOST} ${POSTGRES_USER} ${POSTGRES_PASSWORD} ${POSTGRES_DB} ${EMAIL_HANDLER} ${DOCKER_SUBNET} ${DOCKER_GATEWAY} ${TLS_CERT} ${TLS_KEY} ${RBL_CHECKS}' \
        < "/etc/postfix/templates/${name}.template" > "/etc/postfix/${name}"
done
chmod 640 /etc/postfix/pgsql-*.cf
chown root:postfix /etc/postfix/pgsql-*.cf

mkdir -p /var/spool/postfix/etc
cp /etc/resolv.conf /etc/services /var/spool/postfix/etc/ 2>/dev/null || true
newaliases

# switch to the real certificate once it appears, and reload on renewal
(
    last=""
    while sleep 300; do
        if [ -f "$WANTED_CERT" ] && [ -f "$WANTED_KEY" ]; then
            current=$(cat "$WANTED_CERT" "$WANTED_KEY" | sha256sum)
            if [ "$current" != "$last" ]; then
                postconf -e "smtpd_tls_cert_file=$WANTED_CERT" "smtpd_tls_key_file=$WANTED_KEY"
                postfix reload >/dev/null 2>&1 || true
                last="$current"
            fi
        fi
    done
) &

exec postfix start-fg

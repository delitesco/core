#!/bin/sh
# One-shot setup run before the other services: data directories, DKIM key,
# database migrations and alias domains.
set -eu

# Refuse to start with values copied unchanged from .env.example. FLASK_SECRET and
# PROVISIONING_API_KEY are checked by the app itself (length, placeholders).
for name in POSTGRES_USER POSTGRES_PASSWORD POSTFIX_DB_PASSWORD REDIS_PASSWORD DOMAIN FLASK_SECRET PROVISIONING_API_KEY; do
    eval "value=\${$name:-}"
    case "$value" in
        paste-*|put_*|"")
            echo "ERROR: $name in .env is empty or still the example value" >&2
            exit 1
            ;;
    esac
done
case ",${API_ALLOWED_IPS:-}," in
    *,0.0.0.0/0,*|*,::/0,*)
        echo "WARNING: API_ALLOWED_IPS lets the whole internet reach the API." >&2
        echo "WARNING: restrict it to the frontend backend; provisioning stays closed unless" >&2
        echo "WARNING: PROVISIONING_EMERGENCY_OPEN=true is set (emergency only)." >&2
        ;;
esac

mkdir -p /sl/pgp /sl/upload
chmod 700 /sl/pgp /sl/upload

if [ ! -f /sl/dkim.key ]; then
    echo "Generating a DKIM key in the data volume"
    openssl genrsa -traditional -out /sl/dkim.key 2048 2>/dev/null \
        || openssl genrsa -out /sl/dkim.key 2048
    openssl rsa -in /sl/dkim.key -pubout -out /sl/dkim.pub.key 2>/dev/null
fi
chmod 600 /sl/dkim.key

# the other services run as the unprivileged user of the image
chown -R "${APP_UID:-10001}:${APP_GID:-10001}" /sl

alembic upgrade head
python init_app.py
PYTHONPATH=/code python /code/deploy/postfix_db_role.py

# Print the DNS records mail needs and check the published ones.
PYTHONPATH=/code python /code/deploy/check_dns.py || true

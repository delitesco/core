#!/bin/sh
# One-shot setup run before the other services: data directories, DKIM key,
# database migrations and alias domains.
set -eu

mkdir -p /sl/pgp /sl/upload
chmod 700 /sl/pgp

if [ ! -f /sl/dkim.key ]; then
    echo "Generating a DKIM key in the data volume"
    openssl genrsa -traditional -out /sl/dkim.key 1024 2>/dev/null \
        || openssl genrsa -out /sl/dkim.key 1024
    openssl rsa -in /sl/dkim.key -pubout -out /sl/dkim.pub.key 2>/dev/null
fi

alembic upgrade head
python init_app.py

# Print the DNS records mail needs and check the published ones.
PYTHONPATH=/code python /code/deploy/check_dns.py || true

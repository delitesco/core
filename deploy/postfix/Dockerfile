FROM debian:bookworm-slim

# "No configuration": the package must not derive a host name from the build machine
# (a bad resolv.conf "search" line makes its post-install fail); entrypoint.sh writes
# the whole config at start.
RUN echo "postfix postfix/main_mailer_type select No configuration" | debconf-set-selections \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       postfix postfix-pgsql ca-certificates openssl gettext-base \
    && rm -rf /var/lib/apt/lists/* \
    && test -f /etc/postfix/master.cf || cp /usr/share/postfix/master.cf.dist /etc/postfix/master.cf

COPY main.cf.template pgsql-relay-domains.cf.template pgsql-transport-maps.cf.template /etc/postfix/templates/
COPY entrypoint.sh /entrypoint.sh
RUN chmod 644 /etc/postfix/templates/*
RUN chmod +x /entrypoint.sh

EXPOSE 25
ENTRYPOINT ["/entrypoint.sh"]

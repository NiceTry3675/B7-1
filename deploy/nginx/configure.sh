#!/bin/sh
set -eu

: "${SITE_ADDRESS:=localhost}"
: "${PROXY_TIMEOUT_SECONDS:=180}"
export SITE_ADDRESS PROXY_TIMEOUT_SECONDS

# Only a hostname is accepted: no scheme, port, path, or nginx directives.
if ! printf '%s\n' "$SITE_ADDRESS" | grep -Eq '^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$'; then
    echo 'SITE_ADDRESS must be a hostname without a scheme, port, or path.' >&2
    exit 1
fi
case "$SITE_ADDRESS" in
    *..*) echo 'SITE_ADDRESS must not contain empty hostname labels.' >&2; exit 1 ;;
esac
case "$PROXY_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) echo 'PROXY_TIMEOUT_SECONDS must be an integer >= 180.' >&2; exit 1 ;;
esac
if [ "$PROXY_TIMEOUT_SECONDS" -lt 180 ]; then
    echo 'PROXY_TIMEOUT_SECONDS must be >= 180.' >&2
    exit 1
fi

certificate_dir="/etc/letsencrypt/live/$SITE_ADDRESS"
if [ -s "$certificate_dir/fullchain.pem" ] && [ -s "$certificate_dir/privkey.pem" ]; then
    template=https
elif [ -e "$certificate_dir/fullchain.pem" ] || [ -e "$certificate_dir/privkey.pem" ]; then
    echo 'Both fullchain.pem and privkey.pem are required for HTTPS.' >&2
    exit 1
else
    template=bootstrap
    echo 'TLS certificate not found; only HTTP ACME validation and health checks are available.' >&2
fi

# Preserve nginx variables such as $host, $uri, and $http_upgrade.
envsubst '${SITE_ADDRESS} ${PROXY_TIMEOUT_SECONDS}' \
    < "/opt/nginx/templates/$template.conf.template" > /etc/nginx/conf.d/default.conf
nginx -t

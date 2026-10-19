#!/bin/sh
set -eu

: "${PROXY_TIMEOUT_SECONDS:=180}"
export PROXY_TIMEOUT_SECONDS

case "$PROXY_TIMEOUT_SECONDS" in
    ''|*[!0-9]*) echo 'PROXY_TIMEOUT_SECONDS must be an integer >= 180.' >&2; exit 1 ;;
esac
if [ "$PROXY_TIMEOUT_SECONDS" -lt 180 ]; then
    echo 'PROXY_TIMEOUT_SECONDS must be >= 180.' >&2
    exit 1
fi

# Preserve nginx variables such as $host, $uri, and $http_upgrade.
envsubst '${PROXY_TIMEOUT_SECONDS}' \
    < /opt/nginx/templates/http.conf.template > /etc/nginx/conf.d/default.conf
nginx -t

#!/bin/sh
set -eu
: "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
private=$(mktemp -d /run/agentchaos-XXXXXX)
cp /etc/agentchaos/redis.conf "$private/redis.conf"
printf '\nrequirepass %s\n' "$REDIS_PASSWORD" >> "$private/redis.conf"
chown -R redis:redis "$private"
chmod 600 "$private/redis.conf"
chown redis:redis /data
chmod 700 /data
exec /usr/bin/setpriv --reuid redis --regid redis --clear-groups --no-new-privs /usr/local/bin/redis-server "$private/redis.conf"

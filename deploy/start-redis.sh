#!/bin/sh
set -eu
: "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
cp /etc/agentchaos/redis.conf /tmp/agentchaos-redis.conf
printf '\nrequirepass %s\n' "$REDIS_PASSWORD" >> /tmp/agentchaos-redis.conf
chown redis:redis /tmp/agentchaos-redis.conf
chmod 600 /tmp/agentchaos-redis.conf
exec /usr/local/bin/docker-entrypoint.sh redis-server /tmp/agentchaos-redis.conf

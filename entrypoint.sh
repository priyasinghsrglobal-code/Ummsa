#!/bin/sh
set -eu
mkdir -p /data
chown bot:bot /data
exec su -s /bin/sh bot -c 'exec python /app/bot.py'

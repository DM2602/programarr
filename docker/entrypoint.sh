#!/bin/sh
# Run Programarr as PUID/PGID so poster files belong to the same user as Plex and the *arr apps.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
umask "${UMASK:-002}"
mkdir -p /data
if [ "$(id -u)" = "0" ]; then
  if ! getent group "$PGID" >/dev/null 2>&1; then
    groupadd -o -g "$PGID" programarr
  fi
  if ! getent passwd "$PUID" >/dev/null 2>&1; then
    useradd -o -u "$PUID" -g "$PGID" -d /data -s /usr/sbin/nologin programarr
  fi
  chown "$PUID:$PGID" /data
  find /data -maxdepth 2 \( ! -user "$PUID" -o ! -group "$PGID" \) -exec chown "$PUID:$PGID" {} + 2>/dev/null || true
  exec gosu "$PUID:$PGID" "$@"
fi
exec "$@"

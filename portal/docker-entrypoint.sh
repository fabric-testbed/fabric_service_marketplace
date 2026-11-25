#!/bin/bash
# The data folder is mounted from the host, where Docker creates it as root.
# Make it writable by the "portal" user, then run the command as that user.
set -e
if [ "$(id -u)" = "0" ]; then
    chown -R portal:portal /usr/src/app/instance 2>/dev/null || true
    exec gosu portal "$@"
fi
exec "$@"

#!/bin/sh
# Start munged with a key generated on first use, then run the given command.
set -eu

if [ ! -f /etc/munge/munge.key ]; then
    /usr/sbin/mungekey --create --keyfile /etc/munge/munge.key
    chown munge:munge /etc/munge/munge.key
    chmod 400 /etc/munge/munge.key
fi
install -d -o munge -g munge -m 0755 /run/munge /var/log/munge /var/lib/munge
su munge -s /bin/sh -c "/usr/sbin/munged --force"

exec "$@"

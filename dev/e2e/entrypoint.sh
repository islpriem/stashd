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

# The ssh transfer channel needs a server on the receiving side. Both daemons run in this
# one container, so it talks to itself over ssh, exactly as two hosts would.
install -d -m 0755 /run/sshd
[ -f /etc/ssh/ssh_host_ed25519_key ] || ssh-keygen -A >/dev/null
/usr/sbin/sshd

exec "$@"

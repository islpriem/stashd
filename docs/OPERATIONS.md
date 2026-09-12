# Operations

What running STASH on a cluster involves. Read [Concepts](CONCEPTS.md) and
[Configuration](CONFIGURATION.md) first.

## Topology

Run one controller, and one storage daemon per storage system on a host that mounts it.
A second controller is unsupported and goes undetected. Start the controller first: a
storage daemon needs it on its first start, and afterwards starts from its cached cluster
config, reported as degraded by `/readyz`, until the controller is back.

`server.host` defaults to `127.0.0.1`; anything reached from another host needs a real
address, and TLS through `server.tls_cert` and `server.tls_key`. Every host that runs the
CLI or the controller needs `munged` with the cluster's key.

## Running work as the user

With `identity: sudo` (the default), a storage daemon runs every command as the requesting
user through `sudo -n -u <user> --`. Allow exactly those commands, without a password or
TTY, and never as root:

```sudoers
Cmnd_Alias STASH = /usr/bin/mkdir, /usr/bin/chmod, /usr/bin/rm, /usr/bin/test, \
                   /usr/bin/du, /usr/bin/find, /usr/bin/rsync
stashd ALL=(ALL, !root) NOPASSWD: STASH
Defaults!STASH !requiretty
```

Adjust the paths to your distribution. `identity: current` runs everything as the
daemon's own user and is meant for development. Running as root and dropping privileges
per operation is not implemented.

## Fileset directories

A fileset is `<fileset_prefix>/<user>/<name>`, created by the user with mode
`fileset_mode` (default `0700`). Users must be able to create `<fileset_prefix>/<user>`
themselves: make the prefix sticky and world-writable (`1777`), or create the per-user
directories in advance. STASH never adopts a directory that belongs to someone else.

## Transfers between sites

When a transfer's storages belong to different daemons, rsync runs over ssh from the
source daemon's host to the target daemon's `host`, as the requesting user and with
`BatchMode=yes`. Every user who transfers between sites needs key-based ssh between those
hosts that works without a prompt.

## Quotas on POSIX storage

The `posix` driver cannot enforce a directory quota, so a reservation is bookkeeping, not
a limit: a job can write past it. Usage reconciliation measures every fileset each
`usage_reconcile_interval` (default 15 minutes), flags overruns and blocks the owner's
next allocation. `stash quota` tells users so. Raise the interval where walking the tree
is expensive.

## Secrets

- The peer token (`peer_token_file`, mode `0600`), shared by the controller and every
  daemon. To rotate it, write the new token everywhere and restart every process.
- The MUNGE key, the same on every host that runs the CLI or the controller.
- The database password in the controller's `database.url`.

## Maintenance

- `stash admin drain STORAGE` stops new filesets and dispatches on a storage; running
  transfers finish and no data moves. `stash admin undrain STORAGE` reverses it. Drain
  state lives in the database and needs no config change.
- Stopping a storage daemon lets its transfers finish for `timeouts.drain`, then cancels
  and reports the rest.
- Finished transfers older than `retention.transfers` are rolled into daily statistics
  and deleted, so report totals stay the same; audit events older than `retention.audit`
  are deleted. Pruning runs hourly on the controller.

## Monitoring

Both roles serve `/healthz` and `/readyz`. The controller serves Prometheus metrics at
`/metrics`, computed from the database on each scrape. The endpoint needs no credential;
filter it at a proxy if that matters. Worth alerting on:

- `stash_daemon_up == 0`: a daemon not seen within `timeouts.daemon_unreachable`
- `stash_queue_depth`: transfers waiting or running per storage
- `stash_storage_used_bytes` far below `stash_storage_allocated_bytes`: users reserve
  more than they use

## Availability and backups

The controller is a single point of failure. While it is down, nothing is submitted or
scheduled; transfers already on a daemon keep running and report back once it returns.
On start, the controller reconciles in-flight transfers with the daemons before it
schedules anything new. Everything STASH knows is in PostgreSQL, so back it up. Cached
filesets can be rebuilt from their source; output filesets cannot until they are flushed.

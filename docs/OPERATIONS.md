# Operating STASH

What an operator has to decide, install and watch.

## Roles and processes

One **controller** (`role: controller`) owns PostgreSQL, the queue and the authoritative
cluster config. One **storage daemon** (`role: storage`) per storage system runs on a host
where that storage is mounted and does every filesystem operation. Running two controllers
is unsupported: the cluster config cannot detect it, and nothing else will either.

## Acting as the user (undecided)

A daemon never impersonates anyone through MUNGE: MUNGE proves *who asked*. Doing the work
*as* that user is a host-local privilege mechanism, chosen per daemon with `identity:`.

| `identity:` | What happens                                                | Use                    |
| ----------- | ----------------------------------------------------------- | ---------------------- |
| `sudo`      | `sudo -n -u <user> -- <command>`; never prompts.            | The interim decision.  |
| `current`   | The daemon's own user does the work; no privilege change.   | Development.           |

`sudo` needs a sudoers rule allowing the daemon's account to run the commands the driver
uses (`mkdir`, `chmod`, `rm`) as any STASH user, without a password and without a TTY:

```sudoers
Cmnd_Alias STASH = /bin/mkdir, /bin/chmod, /bin/rm
stashd ALL=(ALL) NOPASSWD: STASH
Defaults!STASH !requiretty
```

The alternative — a daemon that runs as root and drops privileges per operation —
is not implemented. The decision needs a security sign-off before production use.

## POSIX cannot enforce a fileset quota

The `posix` driver declares `native_quota: false`, and `set_fileset_quota` does nothing
there. An allocation is therefore a *reservation in STASH*, not a limit the filesystem
enforces: a job that writes past it succeeds. Overruns are found afterwards by usage
reconciliation, which flags the fileset `over_allocation` and blocks further allocations by
that user. Where the storage can enforce a quota, a driver that declares the capability
applies it automatically. Tell your users this; `stash quota` says it too.

## Fileset directories

A fileset is `<fileset_prefix>/<user>/<name>`, created by the user themselves through the
`Identity`, mode `0700` by default and configurable per storage with `fileset_mode`.
The per-user directory is created on the way with the daemon's umask, so the
`fileset_prefix` must let a user create their own directory.

STASH never adopts a directory that already exists and belongs to somebody else: creation
fails instead.

## Peer tokens

`/internal/v1` accepts one credential: a bearer token from `peer_token_file`, mode `0600`.
A daemon with no token configured refuses every internal request. MUNGE credentials do not
open the internal API. Rotate by writing a new token on every peer and restarting.

## Starting order

A storage daemon fetches the cluster config from the controller at startup and caches it
under `cache_dir`. On a host that has never run one, the controller must be up first;
after that the daemon starts from its cache and reports itself degraded until it reaches
the controller again. Each daemon announces itself on startup and on every refresh, so
`stash storages` shows when it was last seen and what revision it is on.

`worker_pool_size` bounds how many transfers a daemon runs at once. It may not be smaller
than `concurrency.per_storage`, or the daemon would be handed more work than it can run;
it refuses to start in that case.

## SSH between daemon hosts

A transfer whose two storages sit on different daemons runs over rsync's ssh channel. The
daemon holding the source connects to the target daemon's `host` **as the requesting
user**, so every user who transfers across sites needs key-based ssh from the
source host to the target host, and `ssh -o BatchMode=yes` must succeed without a prompt.
A daemon without `host:` in the cluster config can only take part in local transfers.

The alternative — connecting as a service account and using rsync's `--rsync-path` to
switch to the user on the far side — is not implemented.

## Draining a storage

`POST /api/v1/storages/{id}/drain` (admin) stops new work involving a storage: the
scheduler dispatches nothing that touches it and fileset creation on it is refused with
`STORAGE_DRAINED`. Transfers already running finish. `undrain` reverses it. Drain state
lives in the database, not the cluster config, so it survives a config rollout and takes
effect without one. `stash storages` shows which storages are drained.

Draining is what you do before maintenance. It does not evacuate anything: filesets stay
where they are, and their owners keep their reservations.

## Usage reconciliation

Each cache storage is measured on its own `usage_reconcile_interval` (default 15 minutes):
the controller asks the owning daemon what every live fileset holds, corrects
`used_bytes`, and flags anything past its allocation. A daemon that cannot answer is
logged and retried on the next pass. Raise the interval on storages where walking the
tree is expensive; lower it where overruns need to be caught quickly.

## Retention

Terminal transfers older than `retention.transfers` are rolled into a daily bucket per
user, storage, route and kind, then deleted; audit events older than `retention.audit`
are deleted. The usage report reads both the live rows and the buckets, so totals do not
move when pruning runs. What a bucket cannot keep is the distribution: p95 queue wait
covers only transfers that are still in the table.

Pruning runs hourly on the controller. Nothing else deletes rows.

## Metrics

`/metrics` on the controller serves the Prometheus text format, without a credential, and
is computed from the database at each scrape. Watch `stash_daemon_up` (a daemon not seen
within `timeouts.daemon_unreachable`), `stash_queue_depth`, and
`stash_storage_used_bytes` against `stash_storage_allocated_bytes` — a gap that keeps
growing means users reserve more than they use.

## The controller is a single point of failure

There is one controller. While it is down: no submissions, no reads, no scheduling, and
daemons run on their cached config and report themselves degraded. Transfers already
handed to a daemon keep running and their events are retried; nothing is lost, but
nothing new starts. Restarting the controller reconciles in-flight transfers against the
daemons before it schedules anything new.

## Backups

Everything STASH knows is in PostgreSQL. A lost database means lost accounting for data
that still exists on disk; back it up. Cached filesets are replicas and need no backup;
**output filesets are not**, until they are flushed.

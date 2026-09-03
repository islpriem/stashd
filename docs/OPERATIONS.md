# Operating STASH

What an operator has to decide, install and watch. Grows with the code; today it
covers creating and releasing filesets.

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

## Backups

Everything STASH knows is in PostgreSQL. A lost database means lost accounting for data
that still exists on disk; back it up. Cached filesets are replicas and need no backup;
**output filesets are not**, until they are flushed.

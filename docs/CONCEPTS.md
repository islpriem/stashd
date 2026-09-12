# Concepts

## Storages and locations

A **storage** is a filesystem STASH manages, at a **location** (a site). Its roles decide
what STASH does with it:

| Role | Holds | STASH |
| ---- | ----- | ----- |
| `source` | Authoritative data | Reads from it, and writes flushed results back to it |
| `cache` | Filesets | Creates, fills and deletes filesets on it |

A storage can have both roles. Exactly one storage daemon serves each storage.

## Filesets

A **fileset** is a named directory on a cache storage, owned by one user, with a reserved
size. It lives at `<fileset_prefix>/<user>/<name>`.

- A **cached** fileset is filled from a path on a source storage and can be rebuilt from
  it at any time.
- An **output** fileset starts empty for a job to write into. Until it is flushed it holds
  the only copy of its data, so the server releases it only when the request says
  `discard`.

A reference names either: `STORAGE:/path` is a path, `STORAGE:name` a fileset. The
leading slash decides.

## Transfers

| Kind | From | To | Effect |
| ---- | ---- | -- | ------ |
| `warm` | Source path | Cached fileset | Fills or refreshes the fileset |
| `flush` | Fileset | Source path | Writes it back, then releases it unless `keep` is set |
| `release` | Fileset | | Deletes it and frees its reservation |

A transfer moves through `SUBMITTED`, `ASSIGNED` and `RUNNING` and ends `SUCCEEDED`,
`FAILED` or `CANCELLED`. A failure in a class listed in `transfer.retries.retry_on`, such
as `network`, goes back into the queue after a backoff; permission, quota and validation
failures are never retried. Cancelling keeps whatever has arrived, and the fileset keeps
its reservation.

## Allocation

A fileset counts against its owner's limits by its **reserved** size, from creation until
release, whatever it holds. A new allocation is refused, checked in this order, when:

1. the storage is drained;
2. the user has a fileset that uses more than it reserved;
3. the user already owns `max_filesets_per_user` filesets;
4. it exceeds the user's limit on that storage;
5. it exceeds the user's limit across all caches;
6. it exceeds what is left of the storage's `capacity` times its `fill_limit`.

Admins can set both user limits per user. Otherwise the limit across caches is
`user_total_cache_allocation`, and the limit on a storage is its
`default_user_allocation_limit`; a storage without one admits nothing.

A warm reserves the measured source size times `scheduling.allocation_headroom`, or more
when asked. POSIX storage cannot enforce a directory quota, so a job can write past its
reservation; usage reconciliation notices, and rule 2 blocks the owner until the fileset
is back within its reservation.

## Scheduling

Every `scheduling.interval`, the controller walks the queue in fair-share order: points
grow with the volume a user transfers and halve every `fairshare.half_life`, and fewer
points go first. Concurrency is capped globally, per user, per storage and per route, and
a transfer takes a slot on both of its storages. A transfer that does not fit is skipped,
never a barrier for the ones behind it. A route's bandwidth is split among its running
transfers.

Between storages on the same daemon, rsync runs locally. Between daemons it runs over
ssh, from the source daemon's host to the target daemon's `host`, as the requesting user.

## Security

- Users are identified by MUNGE credentials. Admins are listed in `auth.admin_uids` and
  `auth.admin_gids`.
- A storage daemon runs every filesystem command and every rsync as the requesting user,
  so STASH reads only what that user can.
- Each daemon resolves and checks paths against the storages it serves; nothing the
  controller sends is taken on trust.
- The internal API between controller and daemons takes only the peer token; a MUNGE
  credential does not open it.

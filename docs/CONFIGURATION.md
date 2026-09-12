# Configuration

stashd reads two YAML files:

- The **bootstrap config**, one per process: who it is, where it listens, how it reaches
  the controller. Pass it with `stashd --config PATH` (default `/etc/stash/stashd.yaml`).
  Relative paths in it resolve against its own directory.
- The **cluster config**, authored on the controller: sites, daemons, storages, limits and
  policy. Storage daemons fetch it from the controller and cache it under `cache_dir`, so
  they can start while the controller is down.

Each file is validated as a whole, every problem is reported at once, and a rejected
config makes stashd exit with status 2. Working examples are in [`dev/`](../dev):
`controller.yaml`, `daemon-hot1.yaml`, `daemon-loc2hot.yaml` and `cluster.yaml.in`.

## Bootstrap config

| Key | Role | Meaning |
| --- | ---- | ------- |
| `self.daemon_id`, `self.role` | Both | Identity; `role` is `controller` or `storage` |
| `self.storages` | Storage | Storage ids this daemon serves |
| `server.host`, `server.port` | Both | Listen address, default `127.0.0.1:8443` |
| `server.tls_cert`, `server.tls_key` | Both | Serve HTTPS; set both or neither |
| `cache_dir` | Both | Local state, such as the cached cluster config |
| `logging.level`, `logging.format` | Both | Defaults `INFO` and `json`; `console` for development |
| `peer_token_file` | Both | Token the internal API accepts; the controller also sends it to daemons |
| `database.url` | Controller | `postgresql+psycopg://user:password@host/db` |
| `cluster_config` | Controller | Path of the cluster config |
| `controller.url`, `controller.token_file` | Storage | The controller, and the token to send it |
| `identity` | Storage | `sudo` (default), or `current` to run as the daemon's own user |
| `worker_pool_size` | Storage | Transfers run at once, default 4; at least `concurrency.per_storage` |
| `config_refresh_interval` | Storage | How often the cluster config is refetched, default `60s` |

## Cluster config

| Section | Contents |
| ------- | -------- |
| `revision` | Raised with every change; a changed file without a higher revision is refused |
| `locations` | Sites: `id`, `name` |
| `daemons` | `id`, `url`, and `host` for ssh (defaults to the host in `url`) |
| `storages` | `id`, `location`, `daemon`, `roles` (`source`, `cache`), `tier`, `driver` (`posix`), `root`, `fileset_prefix` (default `root`), `fileset_mode` (default `0700`); caches add `capacity`, `fill_limit` (default 0.95), `default_user_allocation_limit` and `usage_reconcile_interval` (default `15m`) |
| `limits` | `user_total_cache_allocation`, `max_filesets_per_user`, `queued_transfers_per_user`; `concurrency` (`global`, `per_storage`, `per_user`, `per_route`); `bandwidth`: `per_route_aggregate`, split among a route's transfers within `min_per_transfer` and `max_per_transfer` |
| `scheduling` | `interval`, `fairshare` (`half_life`, `points_per_gib`), `allocation_headroom` (a factor, at least 1) |
| `transfer` | `engine` (`rsync`), `retries` (`count`, `backoff`, `retry_on` failure classes such as `network`), `progress_poll_interval`, `nominal_throughput` for estimates |
| `auth` | `cli: munge`, `peer: token`, `munge_socket`, `admin_uids`, `admin_gids` |
| `retention` | How long finished `transfers` and `audit` events are kept |
| `timeouts` | `daemon_unreachable` (default `10m`); `drain`, how long a stopping daemon lets transfers finish (default `5m`) |

A cache storage without `default_user_allocation_limit` admits no allocation until an
admin sets a limit for the user.

Sizes take `500G` (1000-based) or `500Gi` (1024-based), rates `200Mbit` or `120MB/s`, and
durations `30s`, `15m`, `1h30m` or `7d`.

The controller re-reads the cluster config on `SIGHUP` and keeps the running one if the
new file is refused. Storage daemons pick up a new revision within
`config_refresh_interval`.

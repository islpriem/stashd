# stashd

Server side of **STASH**, a data-staging service for multi-site HPC clusters.

Users reserve named *filesets* on fast cache storage, fill them from a source storage
before a job runs, and write results back afterwards. A central scheduler orders the
transfers by fair share and keeps them within per-user, per-storage and per-route limits.

This repository holds the daemon; the command-line client is
[stashcli](https://github.com/islpriem/stashcli). Version 0.1.0, unreleased.

## Architecture

One binary, two roles, chosen by `self.role` in the bootstrap config:

- **Controller**, one per cluster: PostgreSQL, the queue and scheduler, the cluster
  config, and the public API under `/api/v1`.
- **Storage daemon**, one per storage system, on a host that mounts it: every filesystem
  operation and every transfer, run as the requesting user.

```
stash --(HTTP, MUNGE)--> controller --(HTTP, peer token)--> storage daemons
                             |                                     |
                         PostgreSQL                    rsync, local or over ssh
```

Users authenticate with MUNGE. A daemon resolves and checks every path it is sent
against the storage it serves.

## Quickstart

### Try it in Docker

A sandbox container runs a controller, two storage daemons, MUNGE, rsync and sshd, and
gives you a shell with the CLI. Clone both repositories side by side:

```bash
git clone git@github.com:islpriem/stashd.git
git clone git@github.com:islpriem/stashcli.git
cd stashd
docker compose --profile e2e up -d        # builds the image on first use
docker compose exec e2e /workspace/stashd/dev/e2e/sandbox.sh up
docker compose exec -it e2e /workspace/stashd/dev/e2e/sandbox.sh shell
```

The shell belongs to the test user `mmustermann`, whose data waits at
`HOT1:/mmustermann/small` (10 MiB) and `HOT1:/mmustermann/medium` (100 MiB):

```bash
stash storages
stash warm HOT1:/mmustermann/medium LOC2HOT:mydir --wait
stash path LOC2HOT:mydir
stash --yes cool LOC2HOT:mydir
```

`sandbox.sh admin ARGS` runs one command as an admin and `sandbox.sh logs NAME` follows a
daemon's log. `down` stops the daemons; `reset` also empties the database and the caches.
Code changes take effect after `sandbox.sh down && sandbox.sh up`.

### Run it locally

```bash
source dev/env.sh              # keeps uv's cache, Python and temp files in the checkout
uv sync
dev/render-configs.sh          # dev/cluster.yaml, storage roots and a peer token
docker compose up -d postgres
uv run alembic -x config=dev/controller.yaml upgrade head
uv run stashd --config dev/controller.yaml       # controller on :8000
uv run stashd --config dev/daemon-hot1.yaml      # storage daemon for HOT1 on :8001
uv run stashd --config dev/daemon-loc2hot.yaml   # storage daemon for LOC2HOT on :8002
curl localhost:8000/healthz
```

The CLI needs MUNGE to authenticate against this; without it, use the sandbox.

## Development

```bash
uv run pytest                  # the integration tests need the PostgreSQL above
uv run ruff check . && uv run ruff format --check .
uv run mypy
scripts/coverage-gates.sh      # the suite with coverage gates, as CI runs it
uv run scripts/gen-openapi.py  # after an API change; commit the result
```

The end-to-end checks in `dev/e2e/` run in the same container, for example
`docker compose exec e2e /workspace/stashd/dev/e2e/full-lifecycle.sh`. They stop a
running sandbox.

## Documentation

- [Concepts](docs/CONCEPTS.md): filesets, storages, transfers, allocation, scheduling
- [Configuration](docs/CONFIGURATION.md): bootstrap and cluster config
- [Operations](docs/OPERATIONS.md): deploying and running a cluster
- [API contract](contracts/openapi.json): the public API as OpenAPI
- [Changelog](CHANGELOG.md)

## Layout

```
src/stashd/
  config/     bootstrap and cluster config: models, distribution, reload
  domain/     pure rules: references, allocation, fair share, state machines, routes
  services/   use cases; they own the database queries
  api/        public and internal HTTP API, health and metrics
  schemas/    wire models
  models/     database tables
  db/         database engine and sessions
  scheduler/  what runs next, dispatched to the daemons
  auth/       MUNGE and peer-token authentication
  clients/    HTTP between controller and daemons
  drivers/    filesystem operations per storage type (posix)
  engines/    moving data (rsync)
  identity/   running commands as the requesting user
  tasks/      transfers a daemon runs outside its request handlers
  obs/        logging and metrics
migrations/   database schema (Alembic)
dev/          local and end-to-end setup
```

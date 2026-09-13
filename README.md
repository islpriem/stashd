# stashd

**STASH** (Storage Transfer & Allocation Scheduling Handler) stages data for HPC jobs
across sites. Users reserve named *filesets* on fast cache storage, fill them from a
source storage before a job runs, and write the results back afterwards.

This repository holds the server: a controller, and one storage daemon per storage
system. The command-line client is [stashcli](https://github.com/islpriem/stashcli).

> [!WARNING]
> STASH is a proof of concept and not suitable for production use yet.

## Features

- Named filesets on cache storage: *cached* ones filled from a source, *output* ones for
  job results
- Allocation limits per user, per storage and across all caches, checked before any data
  moves
- Fair-share scheduling with concurrency limits per user, storage and route, and a
  bandwidth split per route
- Transfers with rsync, locally or over ssh between sites, run as the requesting user
- MUNGE authentication; admin controls for limits, draining and usage reports
- Prometheus metrics, an audit trail and retention of transfer history

## Quickstart

### Try it in Docker

A sandbox container runs a controller, two storage daemons, MUNGE, rsync and sshd, and
gives you a shell with the CLI. Clone both repositories side by side:

```bash
git clone https://github.com/islpriem/stashd.git
git clone https://github.com/islpriem/stashcli.git
cd stashd
docker compose --profile e2e up -d        # builds the image on first use
docker compose exec e2e /workspace/stashd/dev/e2e/sandbox.sh up
docker compose exec -it e2e /workspace/stashd/dev/e2e/sandbox.sh shell
```

The shell belongs to the test user `mmustermann`, with test data at
`HOT1:/mmustermann/small` (10 MiB) and `HOT1:/mmustermann/medium` (100 MiB):

```bash
stash storages
stash warm HOT1:/mmustermann/medium LOC2HOT:mydir --wait
stash path LOC2HOT:mydir
stash --yes cool LOC2HOT:mydir
```

`sandbox.sh down` stops the daemons; `sandbox.sh reset` also empties the database and the
caches.

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
```

The CLI needs MUNGE to authenticate against this; without it, use the sandbox.

## Documentation

- [Concepts](docs/CONCEPTS.md): filesets, storages, transfers, allocation, scheduling
- [Configuration](docs/CONFIGURATION.md): bootstrap and cluster config
- [Operations](docs/OPERATIONS.md): deploying and running a cluster
- [API contract](contracts/openapi.json) and [changelog](CHANGELOG.md)

## Development

```bash
uv run pytest                  # the integration tests need the PostgreSQL above
uv run ruff check . && uv run ruff format --check . && uv run mypy
scripts/coverage-gates.sh      # the suite with coverage gates, as CI runs it
```

End-to-end checks live in `dev/e2e/` and run in the sandbox container.

## License

MIT, see [LICENSE](LICENSE).

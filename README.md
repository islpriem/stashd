# stashd

The STASH daemon. One binary, two roles selected by `self.role` in the bootstrap config:

- **controller** — exactly one instance: PostgreSQL, the queue, the scheduler, the
  authoritative cluster config, and the public API under `/api/v1`.
- **storage daemon** — one per storage system, on a host where that storage is mounted:
  all filesystem access and transfer execution.

Both roles serve `/healthz` and `/readyz`.

## Development

```bash
source dev/env.sh          # workspace-local uv cache, interpreter and TMPDIR
uv sync
dev/render-configs.sh      # renders dev/cluster.yaml for this checkout
docker compose up -d postgres   # the only service either role needs
uv run alembic -x config=dev/controller.yaml upgrade head
uv run stashd --config dev/controller.yaml     # controller on :8000
uv run stashd --config dev/daemon-hot1.yaml    # storage daemon HOT1 on :8001
```

`uv run pytest tests/unit` needs nothing running; the integration suite needs the
compose stack. A wire change ends with `scripts/gen-openapi.py`, whose output is
committed and compared by a test.

The CLI cannot authenticate against this: it needs a MUNGE credential, and the host
may have no libmunge. For anything involving `stash`, use the sandbox below.

## Trying it by hand

A stack that stays up, in the container the e2e checks use: a controller, two storage
daemons, real MUNGE, real rsync, real ssh over the loopback. Nothing is installed on the
host and nothing is written outside the checkout.

```bash
docker compose --profile e2e up -d
docker compose exec e2e /workspace/stashd/dev/e2e/sandbox.sh up
docker compose exec -it e2e /workspace/stashd/dev/e2e/sandbox.sh shell
```

The last one drops into a shell as `mmustermann` with `STASH_SERVER` set and `stash` on
the path. Test data is waiting on the source storage: `HOT1:/mmustermann/small` (10 MiB)
and `HOT1:/mmustermann/medium` (100 MiB).

```
sandbox.sh up          start everything, seed the storages, print what to try
sandbox.sh shell       an interactive shell as the test user
sandbox.sh run ARGS    one stash command as the test user
sandbox.sh admin ARGS  one stash command as root, which the config calls an admin
sandbox.sh status      which of the three are up
sandbox.sh logs NAME   follow controller | hot1 | loc2hot
sandbox.sh down        stop the daemons, keep the data
sandbox.sh reset       empty the database and the caches
```

Code changes are picked up by restarting: `sandbox.sh down && sandbox.sh up`. The
workspace is bind-mounted, so there is nothing to rebuild.

## Checks

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run mypy
scripts/coverage-gates.sh  # 85 % overall, 95 % in domain/
```

## Configuration

Two layers. The **bootstrap config** is local to a process and
holds only what it needs to identify itself and reach the controller; relative paths in it
resolve against the directory of the config file. The **cluster config** is authored on
the controller, holds topology and policy, and is rejected as a whole if any part of it is
invalid — every problem is reported at once.

Sizes and rates are integer bytes and bytes per second after loading. A bare decimal
prefix is SI (`500G` = 500 · 1000³), an `i` prefix is IEC (`500Gi` = 500 · 1024³);
`Mbit`/`Gbit` are SI bits per second divided by eight.

## Exit codes

| Code | Meaning                          |
| ---- | -------------------------------- |
| 0    | Served until shutdown            |
| 2    | Configuration rejected           |

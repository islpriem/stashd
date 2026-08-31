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
uv run stashd --config dev/controller.yaml     # controller on :8000
uv run stashd --config dev/daemon-hot1.yaml    # storage daemon HOT1 on :8001
```

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

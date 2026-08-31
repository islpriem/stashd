# Changelog

## 0.1.0 — unreleased

### Added

- Bootstrap and cluster config models with wholesale validation and a content hash.
- `stashd --config` entry point serving both roles, with `/healthz` and `/readyz`.
- Structured JSON logging.
- Development configs under `dev/`, rendered for the checkout by `dev/render-configs.sh`.
- Pure domain layer: reference syntax and fileset naming, admission control, fileset and
  transfer state machines, decaying fair share, queue ordering, the dispatch walk with all
  concurrency limits and the bandwidth split, routes, channel selection and ETA.

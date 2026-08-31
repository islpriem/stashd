# Changelog

## 0.1.0 — unreleased

### Added

- Bootstrap and cluster config models with wholesale validation and a content hash.
- `stashd --config` entry point serving both roles, with `/healthz` and `/readyz`.
- Structured JSON logging.
- Development configs under `dev/`, rendered for the checkout by `dev/render-configs.sh`.

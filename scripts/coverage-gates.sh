#!/bin/sh
# The full test suite plus the coverage gates: 85 % overall, 95 % in the pure domain.
set -eu
uv run pytest --cov --cov-report=term-missing "$@"
uv run coverage report --include='*/stashd/domain/*' --fail-under=95

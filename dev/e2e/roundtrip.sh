#!/bin/sh
# An authenticated round trip with real MUNGE credentials, inside the e2e container:
# migrate, start the controller, ask it who we are.
#
#   docker compose --profile e2e up -d e2e
#   docker compose exec e2e /workspace/stashd/dev/e2e/roundtrip.sh
set -eu

STASHD=/opt/venvs/stashd
STASHCLI=/opt/venvs/stashcli
SERVER=http://localhost:8000

cd /workspace/stashd
UV_PROJECT_ENVIRONMENT=$STASHD uv sync --frozen -q
UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml upgrade head
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/controller-e2e.yaml &
controller=$!
trap 'kill $controller 2>/dev/null || true' EXIT

cd /workspace/stashcli
UV_PROJECT_ENVIRONMENT=$STASHCLI uv sync --frozen -q
curl -sS --retry 30 --retry-delay 1 --retry-connrefused "$SERVER/healthz" > /dev/null

for command in "whoami" "locations" "storages"; do
    echo "--- stash $command ---"
    $STASHCLI/bin/stash --server "$SERVER" $command
done
echo "--- stash whoami --json ---"
$STASHCLI/bin/stash --server "$SERVER" --json whoami

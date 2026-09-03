#!/bin/sh
# Create an output fileset and release it, with real MUNGE credentials and the real posix
# driver: controller, storage daemon and client all inside the e2e container.
#
#   docker compose --profile e2e up -d e2e
#   docker compose exec e2e /workspace/stashd/dev/e2e/lifecycle.sh
set -eu

STASHD=/opt/venvs/stashd
CONTROLLER=http://127.0.0.1:8000
USER_NAME=${STASH_USER:-mmustermann}

cd /workspace/stashd
UV_PROJECT_ENVIRONMENT=$STASHD uv sync --frozen -q
STASH_DEV_STORAGE=/srv/stash STASH_DEV_CLUSTER=/workspace/stashd/dev/cluster-e2e.yaml \
    dev/render-configs.sh
id "$USER_NAME" >/dev/null 2>&1 || useradd -m "$USER_NAME"
rm -rf "/srv/stash/loc2hot/$USER_NAME"
chmod 0755 /srv/stash/loc2hot
chown "$USER_NAME" /srv/stash/loc2hot

UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml upgrade head
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-e2e.yaml &
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/controller-e2e.yaml &
trap 'kill %1 %2 2>/dev/null || true' EXIT
curl -sS --retry 30 --retry-delay 1 --retry-connrefused "$CONTROLLER/healthz" > /dev/null
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8002/healthz > /dev/null

as_user() {
    su "$USER_NAME" -s /bin/sh -c "$1"
}

echo "--- create LOC2HOT:results ---"
as_user "curl -sS -X POST '$CONTROLLER/api/v1/filesets' \
    -H \"Authorization: Munge \$(munge -n)\" -H 'Content-Type: application/json' \
    -d '{\"storage\": \"LOC2HOT\", \"name\": \"results\", \"size_bytes\": 1073741824}'"
echo
echo "--- on disk ---"
ls -ld "/srv/stash/loc2hot/$USER_NAME/results"
echo "--- release ---"
as_user "curl -sS -X POST '$CONTROLLER/api/v1/transfers' \
    -H \"Authorization: Munge \$(munge -n)\" -H 'Content-Type: application/json' \
    -d '{\"kind\": \"release\", \"target\": {\"storage\": \"LOC2HOT\", \"fileset\": \"results\"}}'"
echo
echo "--- on disk after release ---"
ls -ld "/srv/stash/loc2hot/$USER_NAME/results" 2>&1 || echo "gone"

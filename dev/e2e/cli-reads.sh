#!/bin/sh
# The read commands against a real controller: create a fileset, then list, show and quota.
#
#   docker compose exec e2e /workspace/stashd/dev/e2e/cli-reads.sh
set -eu

# A previous run may still hold the ports; take those down first.
stop_daemons() {
    me=$$
    for entry in /proc/[0-9]*; do
        pid=$(basename "$entry")
        [ "$pid" = "$me" ] && continue
        command=$(tr "\0" " " < "$entry/cmdline" 2>/dev/null || true)
        case "$command" in */bin/stashd*) kill -9 "$pid" 2>/dev/null || true ;; esac
    done
}
stop_daemons

STASHD=/opt/venvs/stashd
STASHCLI=/opt/venvs/stashcli
CONTROLLER=http://127.0.0.1:8000
USER_NAME=${STASH_USER:-mmustermann}

cd /workspace/stashd
UV_PROJECT_ENVIRONMENT=$STASHD uv sync --frozen -q
STASH_DEV_STORAGE=/srv/stash STASH_DEV_CLUSTER=/workspace/stashd/dev/cluster-e2e.yaml \
    dev/render-configs.sh > /dev/null
id "$USER_NAME" >/dev/null 2>&1 || useradd -m "$USER_NAME"
rm -rf "/srv/stash/loc2hot/$USER_NAME"
chmod 0755 /srv/stash/loc2hot
chown "$USER_NAME" /srv/stash/loc2hot

UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml upgrade head >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-e2e.yaml >/dev/null 2>&1 &
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/controller-e2e.yaml >/dev/null 2>&1 &
trap 'kill %1 %2 2>/dev/null || true' EXIT
curl -sS --retry 30 --retry-delay 1 --retry-connrefused "$CONTROLLER/healthz" > /dev/null
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8002/healthz > /dev/null

cd /workspace/stashcli
UV_PROJECT_ENVIRONMENT=$STASHCLI uv sync --frozen -q

su "$USER_NAME" -s /bin/sh -c "curl -sS -X POST '$CONTROLLER/api/v1/filesets' \
    -H \"Authorization: Munge \$(munge -n)\" -H 'Content-Type: application/json' \
    -d '{\"storage\": \"LOC2HOT\", \"name\": \"results\", \"size_bytes\": 1073741824}'" > /dev/null

for command in "list" "fileset show LOC2HOT:results" "quota"; do
    echo "--- stash $command ---"
    su "$USER_NAME" -s /bin/sh -c \
        "STASH_SERVER=$CONTROLLER COLUMNS=80 $STASHCLI/bin/stash $command"
done
echo "--- stash quota --json ---"
su "$USER_NAME" -s /bin/sh -c "STASH_SERVER=$CONTROLLER $STASHCLI/bin/stash --json quota"

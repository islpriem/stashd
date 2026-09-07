#!/bin/sh
# Following a real transfer: --wait, status, queue, cancel.
#
#   docker compose exec e2e /workspace/stashd/dev/e2e/cli-follow.sh
set -eu

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
    dev/render-configs.sh >/dev/null
id "$USER_NAME" >/dev/null 2>&1 || useradd -m "$USER_NAME"
rm -rf "/srv/stash/hot1/$USER_NAME" "/srv/stash/loc2hot/$USER_NAME"
mkdir -p "/srv/stash/hot1/$USER_NAME/mydirectory"
head -c 40000000 /dev/urandom > "/srv/stash/hot1/$USER_NAME/mydirectory/big"
chown -R "$USER_NAME" "/srv/stash/hot1/$USER_NAME"
chmod 0755 /srv/stash/loc2hot && chown "$USER_NAME" /srv/stash/loc2hot

UV_PROJECT_ENVIRONMENT=$STASHD uv run python -c "
import sqlalchemy as sa
e = sa.create_engine('postgresql+psycopg://stash:stash@postgres:5432/stash')
with e.begin() as c:
    c.execute(sa.text('TRUNCATE transfers, filesets, audit_events, daemons RESTART IDENTITY CASCADE'))
"
UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml upgrade head >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/controller-e2e.yaml >/tmp/controller.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused "$CONTROLLER/healthz" >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-e2e.yaml >/tmp/hot1.log 2>&1 &
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-loc2hot-e2e.yaml >/tmp/loc2hot.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8001/healthz >/dev/null
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8002/healthz >/dev/null
trap 'kill %1 %2 %3 2>/dev/null || true' EXIT

cd /workspace/stashcli
UV_PROJECT_ENVIRONMENT=$STASHCLI uv sync --frozen -q
run() {
    echo "--- stash $1 ---"
    su "$USER_NAME" -s /bin/sh -c "STASH_SERVER=$CONTROLLER COLUMNS=80 $STASHCLI/bin/stash $1" \
        || echo "exit $?"
}

# Off a terminal: a line per poll, and the fileset is filled when --wait returns.
run "warm HOT1:/$USER_NAME/mydirectory LOC2HOT:mydir --wait --timeout 300"
run "status"
run "status 1"
run "queue --route 'HOT1->LOC2HOT'"
run "--json status 1"

# A second warm, cancelled while it is queued or running.
run "warm HOT1:/$USER_NAME/mydirectory LOC2HOT:again"
run "cancel 2"
run "status 2"
run "cancel 2 99999"

echo "--- what arrived ---"
ls -l "/srv/stash/loc2hot/$USER_NAME/mydir"

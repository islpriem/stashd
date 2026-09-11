#!/bin/sh
# The whole story on a real stack: warm, write, reconcile, flush, release, report.
#
#   docker compose exec e2e /workspace/stashd/dev/e2e/full-lifecycle.sh
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
mkdir -p "/srv/stash/hot1/$USER_NAME/mydirectory" "/srv/stash/hot1/$USER_NAME/out"
head -c 4000000 /dev/urandom > "/srv/stash/hot1/$USER_NAME/mydirectory/big"
chown -R "$USER_NAME" "/srv/stash/hot1/$USER_NAME"
chmod 0755 /srv/stash/loc2hot && chown "$USER_NAME" /srv/stash/loc2hot

UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml upgrade head >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run python -c "
import sqlalchemy as sa
e = sa.create_engine('postgresql+psycopg://stash:stash@postgres:5432/stash')
with e.begin() as c:
    c.execute(sa.text('TRUNCATE transfers, transfer_stats, filesets, audit_events, daemons, user_limits, storage_states RESTART IDENTITY CASCADE'))
"
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/controller-e2e.yaml >/tmp/controller.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused "$CONTROLLER/healthz" >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-e2e.yaml >/tmp/hot1.log 2>&1 &
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-loc2hot-e2e.yaml >/tmp/loc2hot.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8001/healthz >/dev/null
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8002/healthz >/dev/null
trap 'kill %1 %2 %3 2>/dev/null || true' EXIT

cd /workspace/stashcli
UV_PROJECT_ENVIRONMENT=$STASHCLI uv sync --frozen -q
stash() {
    su "$USER_NAME" -s /bin/sh -c "STASH_SERVER=$CONTROLLER COLUMNS=90 $STASHCLI/bin/stash $1" \
        || echo "exit $?"
}
as_user() { su "$USER_NAME" -s /bin/sh -c "$1"; }
post() {
    as_user "curl -sS -X POST '$CONTROLLER/api/v1$1' -H \"Authorization: Munge \$(munge -n)\" \
        -H 'Content-Type: application/json' -d '$2'"
    echo
}
admin() {
    curl -sS -X "$1" "$CONTROLLER/api/v1$2" -H "Authorization: Munge $(munge -n)" \
        ${3:+-H 'Content-Type: application/json'} ${3:+-d "$3"}
    echo
}

echo "=== 1. warm a cached fileset, and wait for it ==="
stash "warm HOT1:/$USER_NAME/mydirectory LOC2HOT:mydir --wait --timeout 300"

echo "=== 2. an output fileset, written into by the job ==="
stash "fileset create LOC2HOT:results --size 1Gi"
as_user "head -c 3000000 /dev/urandom > /srv/stash/loc2hot/$USER_NAME/results/answer"
stash "fileset show LOC2HOT:results"

echo "=== 3. usage reconciliation notices what is on disk ==="
sleep 70
stash "quota"

echo "=== 4. flush it out with the CLI, which releases it ==="
stash "path LOC2HOT:results"
stash "--yes cool LOC2HOT:results --to HOT1:/$USER_NAME/out --wait"
echo "--- what reached HOT1 ---"
ls -l "/srv/stash/hot1/$USER_NAME/out/"
echo "--- the fileset is gone from the cache ---"
ls -ld "/srv/stash/loc2hot/$USER_NAME/results" 2>&1 || echo "released"
stash "fileset list --state RELEASED"

echo "=== 5. release the cached fileset: no discard needed ==="
stash "--yes release LOC2HOT:mydir"
ls -ld "/srv/stash/loc2hot/$USER_NAME/mydir" 2>&1 || echo "released"

echo "=== 6. an output fileset may not be lost by accident ==="
stash "fileset create LOC2HOT:precious --size 1Gi"
stash "--yes release LOC2HOT:precious"
echo "--- and without a terminal, --yes is required ---"
stash "release LOC2HOT:precious"

echo "=== 7. admin, through the CLI this time ==="
admin_cli() { COLUMNS=90 STASH_SERVER=$CONTROLLER $STASHCLI/bin/stash "$@" || echo "exit $?"; }
admin_cli admin limit set "$USER_NAME" LOC2HOT 1Gi
stash "quota"
admin_cli admin drain LOC2HOT
stash "fileset create LOC2HOT:refused --size 1Gi"
admin_cli admin undrain LOC2HOT
admin_cli admin report usage --group-by route
admin_cli admin report allocation
admin_cli admin limit unset "$USER_NAME" LOC2HOT

echo "=== 8. metrics ==="
curl -sS "$CONTROLLER/metrics" | grep -E "^stash_(transfers_total|transfer_bytes_total|queue_depth|storage_used_bytes|filesets_total|daemon_up)" | head -20

#!/bin/sh
# A warm across two daemons: the scheduler picks the ssh channel and rsync crosses it.
#
#   docker compose --profile e2e up -d --build e2e
#   docker compose exec e2e /workspace/stashd/dev/e2e/warm-ssh.sh
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
CONTROLLER=http://127.0.0.1:8000
USER_NAME=${STASH_USER:-mmustermann}

cd /workspace/stashd
UV_PROJECT_ENVIRONMENT=$STASHD uv sync --frozen -q
STASH_DEV_STORAGE=/srv/stash STASH_DEV_CLUSTER=/workspace/stashd/dev/cluster-e2e.yaml \
    dev/render-configs.sh >/dev/null

id "$USER_NAME" >/dev/null 2>&1 || useradd -m "$USER_NAME"
# The owner is who rsync connects as, so the owner needs a key and a known host.
su "$USER_NAME" -s /bin/sh -c '
    [ -f ~/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/id_ed25519
    cat ~/.ssh/id_ed25519.pub >> ~/.ssh/authorized_keys
    chmod 600 ~/.ssh/authorized_keys
    ssh-keyscan -H localhost >> ~/.ssh/known_hosts 2>/dev/null
'
rm -rf "/srv/stash/hot1/$USER_NAME" "/srv/stash/loc2hot/$USER_NAME"
mkdir -p "/srv/stash/hot1/$USER_NAME/mydirectory/nested"
head -c 4000000 /dev/urandom > "/srv/stash/hot1/$USER_NAME/mydirectory/big"
echo "across the wire" > "/srv/stash/hot1/$USER_NAME/mydirectory/nested/small"
chown -R "$USER_NAME" "/srv/stash/hot1/$USER_NAME"
chmod 0755 /srv/stash/loc2hot && chown "$USER_NAME" /srv/stash/loc2hot

UV_PROJECT_ENVIRONMENT=$STASHD uv run python -c "
import sqlalchemy as sa
e = sa.create_engine('postgresql+psycopg://stash:stash@postgres:5432/stash')
with e.begin() as c:
    c.execute(sa.text('TRUNCATE transfers, filesets, audit_events, daemons, fairshare_accounts, storage_states RESTART IDENTITY CASCADE'))
"
UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml upgrade head >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/controller-e2e.yaml >/tmp/controller.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused "$CONTROLLER/healthz" >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-e2e.yaml >/tmp/hot1.log 2>&1 &
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-loc2hot-e2e.yaml >/tmp/loc2hot.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8001/healthz >/dev/null
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8002/healthz >/dev/null
trap 'kill %1 %2 %3 2>/dev/null || true' EXIT

as_user() { su "$USER_NAME" -s /bin/sh -c "$1"; }
echo "--- warm HOT1 -> LOC2HOT, two daemons ---"
as_user "curl -sS -X POST '$CONTROLLER/api/v1/transfers' -H \"Authorization: Munge \$(munge -n)\" \
    -H 'Content-Type: application/json' -d '{\"kind\":\"warm\",\"source\":{\"storage\":\"HOT1\",\"path\":\"/$USER_NAME/mydirectory\"},\"target\":{\"storage\":\"LOC2HOT\",\"fileset\":\"mydir\"}}'"
echo
for _ in $(seq 60); do
    state=$(as_user "curl -sS '$CONTROLLER/api/v1/transfers/1' -H \"Authorization: Munge \$(munge -n)\"" \
        | sed 's/.*"state":"\([A-Z]*\)".*/\1/')
    case "$state" in SUCCEEDED|FAILED|CANCELLED) break ;; esac
    sleep 1
done
echo "--- transfer ---"
as_user "curl -sS '$CONTROLLER/api/v1/transfers/1' -H \"Authorization: Munge \$(munge -n)\""
echo
echo "--- what crossed ---"
ls -lR "/srv/stash/loc2hot/$USER_NAME/mydir" 2>&1 || echo "nothing arrived"

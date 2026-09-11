#!/bin/sh
# A stack that stays up, for trying things by hand. Same pieces as the e2e checks:
# a controller, two storage daemons, real MUNGE, real rsync, real ssh.
#
#   docker compose --profile e2e up -d
#   docker compose exec e2e /workspace/stashd/dev/e2e/sandbox.sh up
#   docker compose exec -it e2e /workspace/stashd/dev/e2e/sandbox.sh shell
#
# Then type stash commands as mmustermann. `sandbox.sh down` stops the daemons,
# `reset` empties the database and the storages and seeds them again.
set -eu

STASHD=/opt/venvs/stashd
STASHCLI=/opt/venvs/stashcli
CONTROLLER=http://127.0.0.1:8000
USER_NAME=${STASH_USER:-mmustermann}
LOGS=/var/log/stash-sandbox
DB="postgresql+psycopg://stash:stash@postgres:5432/stash"

running() {
    for entry in /proc/[0-9]*; do
        command=$(tr "\0" " " < "$entry/cmdline" 2>/dev/null || true)
        case "$command" in
            */bin/stashd*--config*"$1"*) return 0 ;;
        esac
    done
    return 1
}

stop_all() {
    me=$$
    for entry in /proc/[0-9]*; do
        pid=$(basename "$entry")
        [ "$pid" = "$me" ] && continue
        command=$(tr "\0" " " < "$entry/cmdline" 2>/dev/null || true)
        case "$command" in */bin/stashd*) kill "$pid" 2>/dev/null || true ;; esac
    done
}

start() {
    config=$1
    log=$2
    port=$3
    running "$config" && { echo "  $log already up"; return 0; }
    ( cd /workspace/stashd \
      && UV_PROJECT_ENVIRONMENT=$STASHD setsid uv run stashd --config "$config" \
         < /dev/null > "$LOGS/$log.log" 2>&1 & )
    # The first attempts are refused while it binds; only the outcome is interesting.
    if curl -s --retry 40 --retry-delay 1 --retry-connrefused \
        "http://127.0.0.1:$port/healthz" > /dev/null 2>&1; then
        echo "  $log on :$port"
    else
        echo "  $log FAILED to start; see $LOGS/$log.log" >&2
        return 1
    fi
}

seed() {
    id "$USER_NAME" >/dev/null 2>&1 || useradd -m "$USER_NAME"
    install -d -m 0755 -o "$USER_NAME" "/srv/stash/hot1/$USER_NAME"
    for name in small medium; do
        dir="/srv/stash/hot1/$USER_NAME/$name"
        [ -d "$dir" ] && continue
        install -d -m 0755 -o "$USER_NAME" "$dir"
        case $name in
            small)  count=2;  size=5  ;;
            medium) count=4;  size=25 ;;
        esac
        i=1
        while [ "$i" -le "$count" ]; do
            dd if=/dev/urandom "of=$dir/part-$i.bin" bs=1M "count=$size" status=none
            i=$((i + 1))
        done
        chown -R "$USER_NAME" "$dir"
    done
    chmod 0755 /srv/stash/loc2hot
    chown "$USER_NAME" /srv/stash/loc2hot
}

sql() {
    cd /workspace/stashd
    UV_PROJECT_ENVIRONMENT=$STASHD uv run python - "$1" <<'PY'
import sys
import sqlalchemy as sa
engine = sa.create_engine("postgresql+psycopg://stash:stash@postgres:5432/stash")
with engine.begin() as connection:
    connection.execute(sa.text(sys.argv[1]))
PY
}

case "${1:-}" in
up)
    mkdir -p "$LOGS"
    cd /workspace/stashd
    echo "syncing environments"
    UV_PROJECT_ENVIRONMENT=$STASHD uv sync --frozen -q
    ( cd /workspace/stashcli && UV_PROJECT_ENVIRONMENT=$STASHCLI uv sync --frozen -q )

    echo "rendering configs"
    STASH_DEV_STORAGE=/srv/stash STASH_DEV_CLUSTER=/workspace/stashd/dev/cluster-e2e.yaml \
        dev/render-configs.sh > /dev/null

    echo "migrating"
    UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml \
        upgrade head > /dev/null

    echo "seeding /srv/stash"
    seed

    echo "starting"
    start dev/controller-e2e.yaml controller 8000
    start dev/daemon-e2e.yaml hot1 8001
    start dev/daemon-loc2hot-e2e.yaml loc2hot 8002

    cat <<EOF

Up. Two ways in:

  docker compose exec -it e2e /workspace/stashd/dev/e2e/sandbox.sh shell
      an interactive shell as $USER_NAME, with STASH_SERVER already set

  docker compose exec e2e /workspace/stashd/dev/e2e/sandbox.sh run whoami
      one command, same user

Test data on the source storage:
  HOT1:/$USER_NAME/small     10 MiB
  HOT1:/$USER_NAME/medium   100 MiB

Try:
  stash storages
  stash warm HOT1:/$USER_NAME/medium LOC2HOT:mydir --wait
  stash path LOC2HOT:mydir
  stash --yes cool LOC2HOT:mydir

Logs in $LOGS, or: sandbox.sh logs controller|hot1|loc2hot
EOF
    ;;

shell)
    exec su "$USER_NAME" -s /bin/sh -c "
        export STASH_SERVER=$CONTROLLER PATH=$STASHCLI/bin:\$PATH COLUMNS=\${COLUMNS:-100}
        cd ~ && exec /bin/sh -i"
    ;;

run)
    shift
    su "$USER_NAME" -s /bin/sh -c \
        "STASH_SERVER=$CONTROLLER COLUMNS=\${COLUMNS:-100} $STASHCLI/bin/stash $*"
    ;;

admin)
    shift
    STASH_SERVER=$CONTROLLER COLUMNS=${COLUMNS:-100} "$STASHCLI/bin/stash" "$@"
    ;;

status)
    for pair in "controller 8000" "hot1 8001" "loc2hot 8002"; do
        name=${pair% *}; port=${pair#* }
        if curl -fsS "http://127.0.0.1:$port/healthz" > /dev/null 2>&1; then
            echo "$name  :$port  up"
        else
            echo "$name  :$port  DOWN"
        fi
    done
    ;;

logs)
    tail -f "$LOGS/${2:-controller}.log"
    ;;

down)
    stop_all
    echo "daemons stopped (the database and /srv/stash are untouched)"
    ;;

reset)
    stop_all
    sql "TRUNCATE transfers, transfer_stats, filesets, audit_events, daemons, user_limits, storage_states, fairshare_accounts RESTART IDENTITY CASCADE"
    rm -rf "/srv/stash/loc2hot/$USER_NAME"
    echo "database emptied and caches wiped; run 'sandbox.sh up' again"
    ;;

*)
    echo "usage: $0 up|shell|run ...|admin ...|status|logs [name]|down|reset" >&2
    exit 2
    ;;
esac

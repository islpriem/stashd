#!/bin/sh
# A warm from end to end: real MUNGE, the real posix driver, real rsync.
#
#   docker compose --profile e2e up -d e2e
#   docker compose exec e2e /workspace/stashd/dev/e2e/warm.sh
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
CONTROLLER=http://127.0.0.1:8000
USER_NAME=${STASH_USER:-mmustermann}

cd /workspace/stashd
UV_PROJECT_ENVIRONMENT=$STASHD uv sync --frozen -q
STASH_DEV_STORAGE=/srv/stash STASH_DEV_CLUSTER=/workspace/stashd/dev/cluster-e2e.yaml \
    dev/render-configs.sh > /dev/null
id "$USER_NAME" >/dev/null 2>&1 || useradd -m "$USER_NAME"

# A source tree the user owns, and cache storage they may create their fileset in.
rm -rf "/srv/stash/hot1/$USER_NAME" "/srv/stash/loc2hot/$USER_NAME"
mkdir -p "/srv/stash/hot1/$USER_NAME/mydirectory/nested"
head -c 3000000 /dev/urandom > "/srv/stash/hot1/$USER_NAME/mydirectory/big"
echo "hello" > "/srv/stash/hot1/$USER_NAME/mydirectory/nested/small"
chown -R "$USER_NAME" "/srv/stash/hot1/$USER_NAME"
chmod 0755 /srv/stash/loc2hot && chown "$USER_NAME" /srv/stash/loc2hot

UV_PROJECT_ENVIRONMENT=$STASHD uv run alembic -x config=dev/controller-e2e.yaml upgrade head >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/controller-e2e.yaml >/tmp/controller.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused "$CONTROLLER/healthz" >/dev/null
UV_PROJECT_ENVIRONMENT=$STASHD uv run stashd --config dev/daemon-e2e.yaml >/tmp/daemon.log 2>&1 &
curl -sS --retry 30 --retry-delay 1 --retry-connrefused http://127.0.0.1:8001/healthz >/dev/null
trap 'kill %1 %2 2>/dev/null || true' EXIT

as_user() { su "$USER_NAME" -s /bin/sh -c "$1"; }
warm_body='{"kind":"warm","source":{"storage":"HOT1","path":"/'"$USER_NAME"'/mydirectory"},"target":{"storage":"LOC2HOT","fileset":"mydir"}}'

echo "--- dry run ---"
as_user "curl -sS -X POST '$CONTROLLER/api/v1/transfers' -H \"Authorization: Munge \$(munge -n)\" \
    -H 'Content-Type: application/json' -d '$(echo "$warm_body" | sed 's/}$/,"dry_run":true}/')'"
echo
echo "--- warm ---"
as_user "curl -sS -X POST '$CONTROLLER/api/v1/transfers' -H \"Authorization: Munge \$(munge -n)\" \
    -H 'Content-Type: application/json' -d '$warm_body'"
echo
# The transfer runs beside the request; wait for the daemon to report it finished.
for _ in $(seq 60); do
    state=$(as_user "curl -sS '$CONTROLLER/api/v1/transfers/1' -H \"Authorization: Munge \$(munge -n)\"" \
        | sed 's/.*"state":"\([A-Z]*\)".*/\1/')
    case "$state" in SUCCEEDED|FAILED|CANCELLED) break ;; esac
    sleep 1
done
echo "--- transfer ---"
as_user "curl -sS '$CONTROLLER/api/v1/transfers/1' -H \"Authorization: Munge \$(munge -n)\""
echo
echo "--- fileset ---"
as_user "curl -sS '$CONTROLLER/api/v1/filesets' -H \"Authorization: Munge \$(munge -n)\""
echo
echo "--- what is on the cache storage ---"
ls -lR "/srv/stash/loc2hot/$USER_NAME/mydir"

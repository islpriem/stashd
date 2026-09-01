#!/bin/sh
# Render a cluster config for this checkout and create what development needs:
# storage roots (under the repository unless told otherwise) and a peer token.
#
#   dev/render-configs.sh                              -> dev/cluster.yaml
#   STASH_DEV_STORAGE=/srv/stash STASH_DEV_CLUSTER=dev/cluster-e2e.yaml dev/render-configs.sh
#
# The e2e container renders onto a container-local volume: a macOS bind mount reports
# every file as root-owned, which would make ownership impossible to demonstrate.
set -eu
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
storage=${STASH_DEV_STORAGE:-$root/dev/storage}
output=${STASH_DEV_CLUSTER:-$root/dev/cluster.yaml}

mkdir -p "$storage/hot1" "$storage/loc2hot" "$root/dev/var"
sed "s|@STORAGE@|$storage|g" "$root/dev/cluster.yaml.in" > "$output"

if [ ! -f "$root/dev/peer-token" ]; then
    head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$root/dev/peer-token"
    chmod 600 "$root/dev/peer-token"
fi
echo "rendered $output with storage roots under $storage"

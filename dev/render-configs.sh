#!/bin/sh
# Render dev/cluster.yaml for this checkout and create the development storage roots.
set -eu
root=$(cd -- "$(dirname -- "$0")/.." && pwd)
mkdir -p "$root/dev/storage/hot1" "$root/dev/storage/loc2hot" "$root/dev/var"
sed "s|@ROOT@|$root|g" "$root/dev/cluster.yaml.in" > "$root/dev/cluster.yaml"
echo "rendered $root/dev/cluster.yaml"

# Workspace-local toolchain paths. Source before any uv/pytest invocation:
#   source dev/env.sh
# Keeps caches, interpreters and temporary files inside the repository; pytest's
# tmp_path follows TMPDIR, and the suites use it for storage roots.
_stash_root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")/.." && pwd)
export UV_CACHE_DIR="$_stash_root/.uv-cache"
export UV_PYTHON_INSTALL_DIR="$_stash_root/.uv-python"
# uv would otherwise symlink installed interpreters into ~/.local/bin.
export UV_PYTHON_BIN_DIR="$_stash_root/.uv-python/bin"
export UV_PYTHON_INSTALL_BIN=0
export TMPDIR="$_stash_root/.tmp"
mkdir -p "$TMPDIR"
unset _stash_root

#!/usr/bin/env bash
# Vendor management — clone or update a vendored upstream to its pinned SHA.
#
# Usage:
#   tools/vendor.sh           # set up all vendored repos at pinned SHAs
#   tools/vendor.sh verify    # exit non-zero if any vendor checkout is off-pin
#   tools/vendor.sh <name>    # set up only the named repo (e.g. "reaver")
#
# The source of truth is vendor.lock at the repo root.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOCK="$REPO_ROOT/vendor.lock"
VENDOR_DIR="$REPO_ROOT/vendor"

cmd="${1:-setup}"
filter="${2:-}"

if [[ "$cmd" != "setup" && "$cmd" != "verify" ]]; then
    # Treat first arg as filter when not a command verb.
    filter="$cmd"
    cmd="setup"
fi

mkdir -p "$VENDOR_DIR"

# Read non-comment, non-blank lines from vendor.lock.
exit_code=0
while read -r line; do
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    name=$(echo "$line" | awk '{print $1}')
    sha=$(echo  "$line" | awk '{print $2}')
    url=$(echo  "$line" | awk '{print $3}')

    if [[ -n "$filter" && "$filter" != "$name" ]]; then
        continue
    fi

    target="$VENDOR_DIR/$name"

    if [[ "$cmd" == "verify" ]]; then
        if [[ ! -d "$target/.git" ]]; then
            echo "vendor.sh: $name not checked out at $target — run tools/vendor.sh"
            exit_code=1
            continue
        fi
        actual=$(cd "$target" && git rev-parse HEAD)
        if [[ "$actual" != "$sha" ]]; then
            echo "vendor.sh: $name pin drift — expected $sha got $actual"
            exit_code=1
        else
            echo "vendor.sh: $name OK ($sha)"
        fi
        continue
    fi

    if [[ ! -d "$target/.git" ]]; then
        echo "vendor.sh: cloning $name from $url"
        git clone --quiet "$url" "$target"
    fi

    actual=$(cd "$target" && git rev-parse HEAD)
    if [[ "$actual" == "$sha" ]]; then
        echo "vendor.sh: $name already at $sha"
        continue
    fi

    echo "vendor.sh: setting $name to $sha"
    (cd "$target" && git fetch --quiet origin && git checkout --quiet "$sha")
done < "$LOCK"

exit "$exit_code"

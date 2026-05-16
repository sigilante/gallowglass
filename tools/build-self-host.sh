#!/usr/bin/env bash
#
# build-self-host.sh — compile a Gallowglass source file using the
# vendored Compiler.plan + Reaver, with NO Python in the build path.
#
# This is the Python-less side of the Phase I (1.0.0-rc3) bootstrap
# arc.  The vendored Compiler.plan at compiler/dist/Compiler.plan was
# produced once by the Python bootstrap (see compiler/dist/MANIFEST.json
# for the BLAKE3 of that initial seed); subsequent builds run the seed
# through Reaver to compile the *current* Compiler.gls (or any other
# Gallowglass source), bootstrapping a fresh Compiler.plan with no
# Python involvement.
#
# Usage:
#   tools/build-self-host.sh SOURCE.gls > output.plan
#   tools/build-self-host.sh SOURCE.gls --output output.plan
#
# Requirements:
#   * vendor/reaver/ populated (run tools/vendor.sh if absent)
#   * nix or cabal on PATH (matches the rest of the Reaver fixtures)
#
# Verification (separate, Python-required):
#   tools/selfcompile.py SOURCE.gls
# asserts the self-host's output matches the Python bootstrap's
# byte-for-byte.  Use it whenever Compiler.gls changes to confirm the
# vendored Compiler.plan is still a correct compiler.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SEED_PLAN="$REPO_ROOT/compiler/dist/Compiler.plan"
REAVER_DIR="$REPO_ROOT/vendor/reaver"
BOOT_PLAN="$REAVER_DIR/src/plan/boot.plan"

if [[ $# -lt 1 ]]; then
    echo "usage: $0 SOURCE.gls [--output PATH]" >&2
    exit 2
fi

SOURCE="$1"
shift
OUTPUT=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --output|-o) OUTPUT="$2"; shift 2;;
        *) echo "unknown arg: $1" >&2; exit 2;;
    esac
done

if [[ ! -f "$SOURCE" ]]; then
    echo "error: source file not found: $SOURCE" >&2
    exit 2
fi

if [[ ! -f "$SEED_PLAN" ]]; then
    echo "error: seed Compiler.plan not found at $SEED_PLAN" >&2
    echo "       (regenerate with the Python bootstrap or fetch the artifact)" >&2
    exit 2
fi

if [[ ! -f "$BOOT_PLAN" ]]; then
    echo "error: vendor/reaver not populated — run tools/vendor.sh" >&2
    exit 2
fi

# Copy the seed and boot.plan into a temp dir so Reaver's
# plan-assembler can resolve them as a coherent module set.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
cp "$SEED_PLAN" "$TMPDIR/compiler.plan"
cp "$BOOT_PLAN" "$TMPDIR/boot.plan"

# Reaver dispatch: prefer nix if available (matches tests/reaver/), fall
# back to cabal.  Same shape as tests/reaver/test_selfhost.py:_run_compiler.
if command -v nix >/dev/null 2>&1; then
    REAVER_CMD=(nix develop --command cabal run -v0 plan-assembler --
                "$TMPDIR" compiler Compiler_main_reaver 0)
elif command -v cabal >/dev/null 2>&1; then
    REAVER_CMD=(cabal run -v0 plan-assembler --
                "$TMPDIR" compiler Compiler_main_reaver 0)
else
    echo "error: neither nix nor cabal on PATH" >&2
    exit 2
fi

cd "$REAVER_DIR"
if [[ -n "$OUTPUT" ]]; then
    "${REAVER_CMD[@]}" < "$SOURCE" > "$OUTPUT"
    echo "wrote $(wc -c < "$OUTPUT") bytes to $OUTPUT" >&2
else
    "${REAVER_CMD[@]}" < "$SOURCE"
fi

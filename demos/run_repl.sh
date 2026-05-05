#!/usr/bin/env bash
# Compile demos/repl_calc.gls, write Plan Assembler + boot.plan into a
# tempdir, and exec plan-assembler under nix. Pass any stdin you want
# evaluated to this script; output goes to stdout.
#
# Usage:
#   echo "1+2" | demos/run_repl.sh
#   demos/run_repl.sh   # interactive — Ctrl-D to exit
#
# Requires: vendor/reaver populated (run tools/vendor.sh) and `nix` on PATH.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEMO_GLS="$REPO_ROOT/demos/repl_calc.gls"
REAVER_DIR="$REPO_ROOT/vendor/reaver"
BOOT_PLAN="$REAVER_DIR/src/plan/boot.plan"

if [[ ! -f "$BOOT_PLAN" ]]; then
    echo "error: $BOOT_PLAN not present — run tools/vendor.sh first" >&2
    exit 1
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

cd "$REPO_ROOT"
PYTHONPATH="$REPO_ROOT" python3 - "$DEMO_GLS" > "$WORKDIR/demo.plan" <<'PY'
import sys
sys.setrecursionlimit(50000)
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_pla import emit_program
src = open(sys.argv[1]).read()
prog = parse(lex(src, sys.argv[1]), sys.argv[1])
resolved, _ = resolve(prog, 'Main', {}, sys.argv[1])
compiled = compile_program(resolved, 'Main')
sys.stdout.write(emit_program(compiled))
PY

cp "$BOOT_PLAN" "$WORKDIR/boot.plan"

cd "$REAVER_DIR"
exec nix develop --command cabal run -v0 plan-assembler -- \
    "$WORKDIR" demo Main_main 0

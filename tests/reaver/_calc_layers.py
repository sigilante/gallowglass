"""
Local Reaver layer-bisect harness for the calculator REPL fixture.

The full lex/parse/eval/loop pipeline produces wrong output under
Reaver (PR #83 punt). This harness lets us isolate each pipeline
layer: take the calc fixture, append a wrapper definition that calls
`<layer> <hardcoded_args>` and `Trace`s the result, run under Reaver,
parse the traced int, compare against the expected value.

Not pytest-collected — it's an interactive debugging aid you import
from the python REPL or invoke as `__main__`. Once the calc REPL is
fixed and re-promoted to `demos/repl_calc.gls`, this harness gets
deleted (or kept as a regression scaffold).

Usage:

    from tests.reaver._calc_layers import trace
    print(trace(probe='tokenize 0x322B31 3'))      # tokenize "1+2"
    print(trace(probe='parse (tokenize 0x322B31 3)'))
    print(trace(probe='eval (parse (tokenize 0x322B31 3))'))
"""

import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse as gparse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_pla import emit_program


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
REAVER_DIR = os.path.join(REPO_ROOT, 'vendor', 'reaver')
BOOT_PLAN = os.path.join(REAVER_DIR, 'src', 'plan', 'boot.plan')
FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'calc.gls')

_SHIFT = 32   # match test_differential.py's Lsh trick


def _compile(src: str) -> dict:
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 50000))
    prog = gparse(lex(src, '<calc-probe>'), '<calc-probe>')
    resolved, _ = resolve(prog, 'Main', {}, '<calc-probe>')
    return compile_program(resolved, 'Main')


def trace(probe: str) -> str:
    """Append `let trace_target : Nat = <probe>` to the fixture, emit
    Plan Asm with a `(Trace (Lsh trace_target 32) 0)` driver, run under
    Reaver, return raw stdout+stderr. Caller parses out the value.
    """
    fixture_src = open(FIXTURE).read()
    # Strip `main` since we're substituting our own driver.
    src_lines = []
    skip_main = False
    for line in fixture_src.splitlines():
        if line.startswith('let main '):
            skip_main = True
            continue
        if skip_main and line and not line[0].isspace():
            skip_main = False
        if not skip_main:
            src_lines.append(line)
    src = '\n'.join(src_lines) + f'\n\nlet trace_target : Nat = {probe}\n'
    compiled = _compile(src)
    plan_text = emit_program(
        compiled,
        trailer=f'(Trace (Lsh Main_trace_target {_SHIFT}) 0)\n',
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, 'demo.plan'), 'w') as f:
            f.write(plan_text)
        shutil.copy(BOOT_PLAN, os.path.join(tmpdir, 'boot.plan'))
        cmd = ['nix', 'develop', '--command', 'cabal', 'run', '-v0',
               'plan-assembler', '--', tmpdir, 'demo']
        result = subprocess.run(cmd, cwd=REAVER_DIR, capture_output=True, timeout=60)
    return (result.stdout + result.stderr).decode('utf-8', errors='replace')


def parse_traced_value(reaver_output: str) -> int | None:
    """Pull the `<value>\\n0\\n` tail from Reaver output; right-shift by 32."""
    import re
    m = re.search(r'(\d+)\s*\n0\s*\n*\Z', reaver_output)
    if m is None:
        return None
    return int(m.group(1)) >> _SHIFT


if __name__ == '__main__':
    probes = sys.argv[1:] or [
        'BPLAN.add 1 2',                                        # sanity: 3
        'tokenize 49 1',                                        # tokenize "1" → expect non-zero list head
        'tokenize 3287857 3',                                   # tokenize "1+2" (0x322B31)
    ]
    for p in probes:
        out = trace(p)
        v = parse_traced_value(out)
        if v is None:
            tail = out[-400:].replace('\n', '|')
            print(f'PROBE {p!r}: <no value> tail={tail!r}')
        else:
            print(f'PROBE {p!r}: {v}')

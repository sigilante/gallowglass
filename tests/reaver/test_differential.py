#!/usr/bin/env python3
"""
Differential tests: same Gallowglass program, two runtimes, equal output.

For each fixture, this:
  1. Compiles via the bootstrap.
  2. Evaluates via the BPLAN harness (`bevaluate`) — the Python reference.
  3. Emits Plan Assembler text via `bootstrap.emit_pla` and runs it under
     Reaver (`vendor/reaver`, plan-assembler) with a `Trace main 0` driver.
  4. Parses Reaver's traced result and asserts equality with the harness.

This catches harness/Reaver divergence on real programs, not just the
narrow smoke fixtures in `test_smoke.py`. It is the strongest correctness
guarantee we have short of Phase G's full self-host validation.

Conventions:
  - Every fixture's `main` value is a Nat strictly greater than 255 so
    Reaver's `showVal` renders it as a decimal literal rather than a
    quoted byte/string form. (Phase F discovered Reaver pretty-prints
    byte-range nats as escape sequences; see PR #53.)
  - Fixtures are kept compact — the goal is *coverage* of language
    features, not depth in any one area.
  - Tests skip if Nix/Reaver are unavailable (same as `test_smoke.py`).

Run:
    make test-reaver
  or:
    python3 -m pytest tests/reaver/test_differential.py -v
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.build import build_with_prelude
from bootstrap.emit_pla import emit_program
from dev.harness.bplan import bevaluate, register_jets, register_prelude_jets
from dev.harness.plan import is_nat


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
REAVER_DIR = os.path.join(REPO_ROOT, 'vendor', 'reaver')
BOOT_PLAN = os.path.join(REAVER_DIR, 'src', 'plan', 'boot.plan')


# ---------------------------------------------------------------------------
# Skip gating — same shape as test_smoke.py
# ---------------------------------------------------------------------------

def _reaver_available() -> tuple[bool, str]:
    if not os.path.isdir(REAVER_DIR):
        return False, f'{REAVER_DIR} not present — run tools/vendor.sh'
    if not os.path.isfile(BOOT_PLAN):
        return False, 'vendor/reaver/src/plan/boot.plan not present'
    if shutil.which('nix') is None and shutil.which('cabal') is None:
        return False, 'neither nix nor cabal on PATH'
    return True, ''


_AVAIL, _SKIP_REASON = _reaver_available()
requires_reaver = unittest.skipUnless(_AVAIL, _SKIP_REASON or 'reaver unavailable')


# ---------------------------------------------------------------------------
# Compilation + evaluation helpers
# ---------------------------------------------------------------------------

def _compile_demo(src: str, module: str = 'Demo', *, with_prelude: bool = False) -> dict:
    """Compile to a `dict[fq_name → PlanVal]`.

    `with_prelude=True` builds against the Core prelude (use `Core.X` etc.).
    """
    if with_prelude:
        return build_with_prelude(module, src)
    prog = parse(lex(src, '<diff>'), '<diff>')
    resolved, _ = resolve(prog, module, {}, '<diff>')
    return compile_program(resolved, module)


def _harness_eval(compiled: dict, fq_name: str) -> int:
    """Evaluate a compiled FQ value via the BPLAN harness; return as int.

    Bumps Python's recursion limit and registers both the `Compiler.*` and
    `Core.*` jet tables. Jets short-circuit the recursive PLAN-level
    arithmetic and list ops that would otherwise hit the recursion ceiling
    on programs with non-trivial input."""
    register_jets(compiled)         # Compiler.* (no-op for non-compiler programs)
    register_prelude_jets(compiled) # Core.Nat.*, Core.List.*, Core.Text.*
    old_limit = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old_limit, 50000))
    try:
        val = compiled[fq_name]
        result = bevaluate(val)
    finally:
        sys.setrecursionlimit(old_limit)
    if not is_nat(result):
        raise AssertionError(
            f'harness expected Nat result for {fq_name}, got {type(result).__name__}: {result!r}'
        )
    return int(result)


def _run_reaver(plan_text: str, module: str = 'demo', timeout: int = 60) -> str:
    """Write plan_text + boot.plan into a tempdir, run plan-assembler, return
    stdout+stderr decoded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, f'{module}.plan'), 'w') as f:
            f.write(plan_text)
        shutil.copy(BOOT_PLAN, os.path.join(tmpdir, 'boot.plan'))
        if shutil.which('nix') is not None:
            cmd = ['nix', 'develop', '--command', 'cabal', 'run', '-v0',
                   'plan-assembler', '--', tmpdir, module]
        else:
            cmd = ['cabal', 'run', '-v0', 'plan-assembler', '--', tmpdir, module]
        result = subprocess.run(cmd, cwd=REAVER_DIR, capture_output=True,
                                timeout=timeout)
    return (result.stdout + result.stderr).decode('utf-8', errors='replace')


# Reaver's combined output is mostly boot.plan loading noise (each binding's
# name printed as a quoted symbol, plus a couple of `print(3)` calls). The
# trace driver `(Trace main 0)` prints two integer-only lines at the end:
# the traced value, then the cont return `0`. Pull the second-to-last
# decimal line — anchoring at end-of-output is more robust than first-match.
_TAIL_DECIMAL_PAIR_RE = re.compile(r'(\d+)\s*\n0\s*\n*\Z')


def _parse_traced_int(reaver_output: str) -> int:
    """Parse the trace value from Reaver's combined output.

    The output ends with `<value>\\n0\\n` (Trace + cont return). Match
    that exact tail; raise if not found (which means Reaver mis-formatted
    the value, typically because it was in the byte range and showVal
    rendered it as a quoted string)."""
    m = _TAIL_DECIMAL_PAIR_RE.search(reaver_output)
    if m is None:
        raise AssertionError(
            f'expected `<value>\\n0\\n` tail in Reaver output (value may be '
            f'in byte range and string-formatted by showVal):\n{reaver_output!r}'
        )
    return int(m.group(1))


def _reaver_eval_main(compiled: dict, module: str, value_name: str) -> int:
    """Emit the program with a Trace driver, run under Reaver, parse the int."""
    sym = f'{module}_{value_name}'.replace('.', '_')
    trailer = f'(Trace {sym} 0)\n'
    plan_text = emit_program(compiled, trailer=trailer)
    out = _run_reaver(plan_text)
    try:
        return _parse_traced_int(out)
    except AssertionError as e:
        raise AssertionError(f'{e}\n--- emitted plan text (head) ---\n{plan_text[:600]}') from None


# ---------------------------------------------------------------------------
# The differential assertion
# ---------------------------------------------------------------------------

@requires_reaver
class TestHarnessReaverEquivalence(unittest.TestCase):
    """For each fixture, harness `bevaluate` and Reaver agree on `main : Nat`."""

    def _assert_equiv(self, src: str, *, module: str = 'Demo',
                      value: str = 'main', with_prelude: bool = False):
        compiled = _compile_demo(src, module, with_prelude=with_prelude)
        fq = f'{module}.{value}'
        self.assertIn(fq, compiled, f'{fq!r} not in compiled output')
        harness = _harness_eval(compiled, fq)
        reaver = _reaver_eval_main(compiled, module, value)
        self.assertEqual(harness, reaver,
            f'harness/Reaver divergence on {fq}: harness={harness} reaver={reaver}')
        # Also assert > 255 so we know Reaver actually decimal-printed it
        # (a Trace fallthrough on a byte-range nat would render as a quoted
        # string and our parser would have raised, but the explicit guard
        # protects against future fixture authors picking small values).
        self.assertGreater(harness, 255,
            f'fixture {fq} returned {harness}; choose a value > 255 so Reaver '
            f'showVal prints decimal not byte/string form')

    # --- arithmetic --------------------------------------------------------

    def test_basic_arithmetic(self):
        """λ-application, no recursion, no prelude."""
        src = '''
external mod Core.PLAN { inc : Nat -> Nat }

let succ : Nat -> Nat
  = λ n → PLAN.inc n

let main : Nat
  = succ (succ (succ 997))
'''
        self._assert_equiv(src)

    # --- recursion ---------------------------------------------------------

    def test_recursive_factorial(self):
        """Recursive Nat function via match dispatch."""
        src = '''
use Core.Nat

let factorial : Nat -> Nat
  = λ n → match n {
      | 0 → 1
      | k → Nat.mul n (factorial (Nat.sub n 1))
    }

let main : Nat
  = factorial 7   -- 5040
'''
        self._assert_equiv(src, with_prelude=True)

    def test_mutual_recursion(self):
        """Mutually recursive even/odd via SCC compilation."""
        src = '''
use Core.Nat

let is_even : Nat -> Nat
  = λ n → match n {
      | 0 → 1
      | k → is_odd (Nat.sub n 1)
    }

let is_odd : Nat -> Nat
  = λ n → match n {
      | 0 → 0
      | k → is_even (Nat.sub n 1)
    }

-- 1000 is even → is_even returns 1; multiply to get a value > 255.
let main : Nat
  = Nat.add 999 (is_even 1000)
'''
        self._assert_equiv(src, with_prelude=True)

    # --- ADT pattern matching ---------------------------------------------

    def test_constructor_matching_unary(self):
        """Match a unary constructor (constructor tag is a large strNat —
        regression for the body-context-quote bug from PR #48)."""
        src = '''
type Wrap = | Inner Nat

let unwrap : Wrap -> Nat
  = λ w → match w { | Inner n → n }

let main : Nat
  = unwrap (Inner 1234)
'''
        self._assert_equiv(src)

    def test_constructor_matching_multi(self):
        """Match across multiple nullary constructors."""
        src = '''
type Color =
  | Red
  | Green
  | Blue

let pick : Nat -> Color
  = λ n → match n {
      | 0 → Red
      | 1 → Green
      | _ → Blue
    }

let main : Nat
  = match (pick 1) {
      | Red   → 1000
      | Green → 2000
      | Blue  → 3000
    }
'''
        self._assert_equiv(src)

    # --- higher-order functions -------------------------------------------

    def test_higher_order_application(self):
        """Function-as-argument; closure via lambda lifting."""
        src = '''
external mod Core.PLAN { inc : Nat -> Nat }

let twice : (Nat -> Nat) -> Nat -> Nat
  = λ f n → f (f n)

let main : Nat
  = twice (λ x → PLAN.inc x) 998   -- 1000
'''
        self._assert_equiv(src)

    # --- prelude usage ----------------------------------------------------

    def test_prelude_list_foldr(self):
        """Recursive list operation through the prelude's foldr."""
        src = '''
use Core.Nat
use Core.List unqualified { List, Nil, Cons, foldr }

let main : Nat
  = foldr Nat.add 0 (Cons 100 (Cons 300 (Cons 600 Nil)))
'''
        self._assert_equiv(src, with_prelude=True)

    def test_prelude_list_length(self):
        """Length of a list — exercises Cons/Nil pattern dispatch in prelude."""
        src = '''
use Core.Nat
use Core.List unqualified { List, Nil, Cons, length }

let xs : List Nat
  = Cons 10 (Cons 20 (Cons 30 (Cons 40 (Cons 50 Nil))))

let main : Nat
  = Nat.add 1000 (length xs)   -- 1005
'''
        self._assert_equiv(src, with_prelude=True)

    # --- let-bindings + recursion in body ---------------------------------

    def test_inner_let_binding(self):
        """`let x = ... in ...` produces a body-level let that kal threads
        through; differential test exercises the `(1 v k)` form in body
        context across both runtimes."""
        src = '''
use Core.Nat

let main : Nat
  = let a = Nat.add 500 100 in
    let b = Nat.add a a in       -- 1200
    b
'''
        self._assert_equiv(src, with_prelude=True)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""
planvm seed validation tests.

Verifies that seeds produced by the Python bootstrap compiler are accepted
by the xocore-tech/PLAN VM (`x/plan` / `planvm`).

These tests require planvm to be installed and on PATH.  They are skipped
automatically when planvm is not available — use `make test-planvm` or the
Docker environment (`make test-planvm-docker`) to run them.

Run:
    planvm=/path/to/x/plan python3 tests/planvm/test_seed_planvm.py
  or:
    make test-planvm
"""

import os
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit import emit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PLANVM = os.environ.get('PLANVM', 'planvm')


def planvm_available() -> bool:
    """Return True if planvm is on PATH (or PLANVM env var points to it)."""
    try:
        r = subprocess.run(
            [PLANVM, '--help'],
            capture_output=True, timeout=5
        )
        return True          # any response means it exists
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def compile_to_seed(src: str, name: str, module: str = 'Test') -> bytes:
    """Lex → parse → resolve → codegen → emit."""
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _env = resolve(prog, module, {}, '<test>')
    compiled = compile_program(resolved, module)
    return emit(compiled, f'{module}.{name}')


def run_planvm(seed_bytes: bytes, stdin: bytes = b'', timeout: int = 5):
    """
    Write seed_bytes to a temp file, invoke planvm on it, return CompletedProcess.

    planvm is given `timeout` seconds; after that SIGTERM is sent.
    """
    with tempfile.NamedTemporaryFile(suffix='.seed', delete=False) as f:
        f.write(seed_bytes)
        seed_path = f.name
    try:
        return subprocess.run(
            [PLANVM, seed_path],
            input=stdin,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        # Timeout is expected for interactive cogs — capture what we have
        return e
    finally:
        os.unlink(seed_path)


def seed_loads(seed_bytes: bytes) -> bool:
    """
    Return True if planvm can load the seed without a format/parse crash.

    We distinguish between:
    - A format error (planvm exits immediately with a non-zero code and
      a parse/format error on stderr) → seed is invalid, returns False.
    - A runtime failure (the value is not a valid cog, or the cog exits
      with a non-zero code) → seed is valid but the program fails, returns True.
    - A timeout (the cog is waiting for IO) → seed is valid, returns True.

    Signals (returncode < 0) indicate a crash, so we return False.
    """
    result = run_planvm(seed_bytes)
    if isinstance(result, subprocess.TimeoutExpired):
        # Cog is alive and waiting — seed loaded fine
        return True
    if result.returncode < 0:
        # Killed by a signal (crash / SIGSEGV)
        return False
    stderr = result.stderr.lower()
    # Known format-error indicators from xocore planvm
    format_errors = [b'invalid seed', b'bad seed', b'seed parse', b'format error']
    if any(marker in stderr for marker in format_errors):
        return False
    # Any other exit (including "not a cog", runtime errors) means the seed
    # format was accepted
    return True


def eval_seed(seed_bytes: bytes, timeout: int = 10) -> int | None:
    """
    Run planvm on seed_bytes and return the exit code as the result Nat.

    planvm forces the seed value, casts to Nat, exits with it as the process
    exit code.  For pure Nat seeds (0-255), this gives the evaluated result.

    Returns None on timeout, signal crash, or format error.
    """
    result = run_planvm(seed_bytes, timeout=timeout)
    if isinstance(result, subprocess.TimeoutExpired):
        return None
    if result.returncode < 0:
        return None  # signal crash
    stderr = result.stderr.lower()
    format_errors = [b'invalid seed', b'bad seed', b'seed parse', b'format error']
    if any(marker in stderr for marker in format_errors):
        return None
    return result.returncode


# ---------------------------------------------------------------------------
# Skip decorator
# ---------------------------------------------------------------------------

requires_planvm = unittest.skipUnless(
    planvm_available(),
    f'planvm not found (set PLANVM=/path/to/x/plan or add to PATH)'
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSeedFormat(unittest.TestCase):
    """Verify that Python-compiled seeds are accepted by planvm."""

    @requires_planvm
    def test_nat_literal_seed_loads(self):
        """Seed containing a bare Nat (N(42)) loads without format error."""
        seed = compile_to_seed('let main = 42', 'main')
        self.assertTrue(seed_loads(seed), 'planvm rejected seed for N(42)')

    @requires_planvm
    def test_identity_law_seed_loads(self):
        """Seed containing L(1,0,N(1)) (identity law) loads without format error."""
        seed = compile_to_seed('let id_fn = λ x → x', 'id_fn')
        self.assertTrue(seed_loads(seed), 'planvm rejected seed for identity law')

    @requires_planvm
    def test_applied_lambda_seed_loads(self):
        """Seed for `id 42 = 42` loads without format error."""
        src = '''
let id_fn = λ x → x
let main = id_fn 42
'''
        seed = compile_to_seed(src, 'main')
        self.assertTrue(seed_loads(seed), 'planvm rejected seed for id_fn applied to 42')

    @requires_planvm
    def test_if_then_else_seed_loads(self):
        """Seed for an if/then/else expression loads without format error."""
        seed = compile_to_seed('let main = if True then 10 else 20', 'main')
        self.assertTrue(seed_loads(seed), 'planvm rejected seed for if/then/else')

    @requires_planvm
    def test_nullary_constructor_seed_loads(self):
        """Seed for a nullary constructor loads without format error."""
        src = '''
type Color =
  | Red
  | Green
  | Blue

let main = Green
'''
        seed = compile_to_seed(src, 'main')
        self.assertTrue(seed_loads(seed), 'planvm rejected seed for nullary constructor')

    @requires_planvm
    def test_const_law_seed_loads(self):
        """Seed for a 2-arg law loads without format error."""
        seed = compile_to_seed('let const_fn = λ x y → x', 'const_fn')
        self.assertTrue(seed_loads(seed), 'planvm rejected seed for const law')

    @requires_planvm
    def test_nat_match_seed_loads(self):
        """Seed for a nat pattern match loads without format error."""
        src = '''
let classify = λ n → match n {
  | 0 → 10
  | 1 → 20
  | _ → 30
}
'''
        seed = compile_to_seed(src, 'classify')
        self.assertTrue(seed_loads(seed), 'planvm rejected seed for nat match')


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _run_tests():
    import inspect
    tests = [(name, fn) for name, fn in globals().items()
             if name.startswith('test_') and callable(fn)]
    tests.sort()
    # Also collect methods from test classes
    all_tests = []
    for cls in [TestSeedFormat]:
        for name in sorted(dir(cls)):
            if name.startswith('test_'):
                all_tests.append((name, getattr(cls(), name)))

    if not planvm_available():
        print(f'SKIP  planvm not found (set PLANVM=/path/to/x/plan or add to PATH)')
        print(f'      Run: make test-planvm-docker  (uses Docker, works on macOS)')
        return

    passed = failed = skipped = 0
    for name, fn in all_tests:
        try:
            fn()
            print(f'  OK  {name}')
            passed += 1
        except unittest.SkipTest as e:
            print(f'SKIP  {name}: {e}')
            skipped += 1
        except Exception as exc:
            print(f'FAIL  {name}: {exc}')
            import traceback; traceback.print_exc()
            failed += 1

    print(f'\n{passed} passed, {failed} failed, {skipped} skipped')
    if failed:
        sys.exit(1)


if __name__ == '__main__':
    _run_tests()

#!/usr/bin/env python3
"""
M8.7 Driver tests (compiler/src/Compiler.gls Section 26).

Functions under test:
  nn_Compiler, main

The driver chains lex → parse_program → compile_program → emit_program
into a single `main : Bytes → Bytes` function.

Test strategy
-------------
The Python harness cannot evaluate `main` on non-trivial inputs because
`main` calls `emit_program` which calls `emit_pval` which triggers the
recursion-depth issue for multi-byte output (see test_emit.py header).

What we CAN test in the harness:
  1. Compilation: `main` and `nn_Compiler` appear in the compiled module.
  2. nn_Compiler value: matches int.from_bytes(b'Compiler', 'little').
  3. main on a minimal snippet produces non-empty Bytes — specifically,
     a snippet with one DLet definition produces output bytes with length > 0.
     We test length only (O(1) field extraction), not content (too slow).

What is validated by other means:
  - M8.8 self-hosting validation: `main` processes Compiler.gls itself
    and produces byte-identical Plan Assembler output to the Python bootstrap.
    This is the definitive functional test.
  - planvm seed loading: the seed for `main` is a valid seed (covered by
    make test-ci / make test-prelude-docker).
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads
from dev.harness.plan import A, evaluate

MODULE = 'Compiler'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                        'compiler', 'src', 'Compiler.gls')

# Module-level cache — Compiler.gls is large; compile once per process.
_COMPILED = None


def compile_module():
    global _COMPILED
    if _COMPILED is not None:
        return _COMPILED
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    _COMPILED = compile_program(resolved, MODULE)
    return _COMPILED


def make_seed(name):
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from bootstrap.emit_seed import emit
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    compiled = compile_program(resolved, MODULE)
    return emit(compiled, f'{MODULE}.{name}')


def eval_plan(val, *args):
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 50000))
    try:
        result = val
        for arg in args:
            result = A(result, arg)
        return evaluate(result)
    finally:
        sys.setrecursionlimit(old)


# ---------------------------------------------------------------------------
# Compilation tests
# ---------------------------------------------------------------------------

class TestDriverCompilation(unittest.TestCase):
    """M8.7 functions compile without errors."""

    @classmethod
    def setUpClass(cls):
        cls.c = compile_module()

    def test_nn_compiler_present(self):
        self.assertIn('Compiler.nn_Compiler', self.c)

    def test_main_present(self):
        self.assertIn('Compiler.main', self.c)


# ---------------------------------------------------------------------------
# Value tests
# ---------------------------------------------------------------------------

class TestDriverValues(unittest.TestCase):
    """nn_Compiler encodes 'Compiler' as a little-endian nat."""

    @classmethod
    def setUpClass(cls):
        cls.c = compile_module()

    def test_nn_compiler_value(self):
        expected = int.from_bytes(b'Compiler', 'little')
        result = eval_plan(self.c['Compiler.nn_Compiler'])
        self.assertEqual(result, expected,
            f'nn_Compiler: got {result}, expected {expected}')


# ---------------------------------------------------------------------------
# Smoke test: main on a minimal snippet
# ---------------------------------------------------------------------------

class TestDriverSmoke(unittest.TestCase):
    """
    main applied to a minimal Gallowglass snippet produces non-empty Bytes.

    The snippet is a single top-level definition:
        let x = 42
    Encoded as a Bytes value (MkPair length content_nat).

    We only check that bytes_length > 0 — content evaluation is skipped
    because emit_program produces multi-byte output (too many recursion frames).
    """

    @classmethod
    def setUpClass(cls):
        cls.c = compile_module()

    @unittest.skip(
        "main calls emit_program → emit_pval → multi-byte output → ~100K Python "
        "frames > 50K limit. Covered by M8.8 self-hosting validation and planvm "
        "seed loading."
    )
    def test_main_minimal_snippet(self):
        # Source: "let x = 42\n"
        src_str = b'let x = 42\n'
        src_nat = int.from_bytes(src_str, 'little')
        src_len = len(src_str)
        # Build MkPair(len, content) = Bytes in Gallowglass encoding
        # MkPair is constructor tag=0, arity=2: A(A(0, len), content)
        from dev.harness.plan import A as PA
        bytes_val = PA(PA(0, src_len), src_nat)
        result = eval_plan(self.c['Compiler.main'], bytes_val)
        # Extract bytes_length (fst of the pair)
        length = eval_plan(self.c['Compiler.bytes_length'], result)
        self.assertGreater(length, 0,
            f'main on minimal snippet should produce non-empty output, got length={length}')


# ---------------------------------------------------------------------------
# Planvm seed loading tests
# ---------------------------------------------------------------------------

class TestDriverPlanvm(unittest.TestCase):
    """M8.7 functions produce valid seeds loadable by planvm."""

    @requires_planvm
    def test_nn_compiler_seed(self):
        seed = make_seed('nn_Compiler')
        self.assertTrue(seed_loads(seed),
            'nn_Compiler seed failed planvm load')

    @requires_planvm
    def test_main_seed(self):
        seed = make_seed('main')
        self.assertTrue(seed_loads(seed),
            'main seed failed planvm load')


if __name__ == '__main__':
    unittest.main()

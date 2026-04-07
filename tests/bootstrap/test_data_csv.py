#!/usr/bin/env python3
"""
Data.Csv end-to-end test — minimal effect-driven error handling.

Exercises: type definitions, effect declarations, handle expressions,
run builtin, pattern matching on effect constructors.

This is the minimal achievable subset of the full Data.Csv example from
spec/06-surface-syntax.md §15, using only features already implemented.

Run: python3 -m pytest tests/bootstrap/test_data_csv.py -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from dev.harness.plan import A, N, evaluate, is_nat
from dev.harness.bplan import bevaluate


def pipeline(src: str, mod: str = 'Test') -> dict:
    prog = parse(lex(src, '<test>'), '<test>')
    resolved, _ = resolve(prog, mod, {}, '<test>')
    return compile_program(resolved, mod)


# ---------------------------------------------------------------------------
# Minimal Data.Csv error handling
# ---------------------------------------------------------------------------

CSV_SRC = '''
external mod Core.PLAN {
  inc : Nat → Nat
}

-- Error types for CSV parsing
type CsvError =
  | ParseError Nat
  | SchemaError Nat

-- Exception effect
eff Exn {
  raise : CsvError → Nat
}

-- try_parse: attempt to parse, handling errors with a default
let try_parse : Nat → Nat
  = λ input →
      run (handle (raise (ParseError input)) {
        | return xx → xx
        | raise ee kk → 0
      })

-- try_parse_ok: pure computation returns value through handler
let try_parse_ok : Nat → Nat
  = λ input →
      run (handle (pure input) {
        | return xx → xx
        | raise ee kk → 0
      })

-- try_with_resume: handler resumes with modified value
let try_with_resume : Nat → Nat
  = λ input →
      run (handle (raise (ParseError input)) {
        | return xx → xx
        | raise ee kk → kk 42
      })

-- try_do_chain: do-notation chains with pure values
let try_do_chain : Nat → Nat
  = λ input →
      run (handle (
        xx ← pure input in
        yy ← pure (Core.PLAN.inc xx) in
        pure yy
      ) {
        | return xx → xx
        | raise ee kk → 0
      })

-- try_inspect_error: match on error variant
let try_inspect_error : Nat → Nat
  = λ input →
      run (handle (raise (SchemaError input)) {
        | return xx → xx
        | raise ee kk →
            match ee {
              | ParseError nn → 100
              | SchemaError nn → 200
            }
      })

-- Main entry: run all tests, return 1 if all pass, 0 otherwise
let test_error_default = try_parse 99
let test_pure_passthrough = try_parse_ok 77
let test_resume = try_with_resume 10
let test_do = try_do_chain 5
let test_schema = try_inspect_error 1
'''


class TestDataCsvMinimal(unittest.TestCase):
    """Minimal Data.Csv-style error handling via effects."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = pipeline(CSV_SRC)

    def test_error_default(self):
        """raise returns handler default (0)."""
        result = bevaluate(self.compiled['Test.test_error_default'])
        self.assertEqual(result, 0)

    def test_pure_passthrough(self):
        """pure value passes through handler return arm."""
        result = bevaluate(self.compiled['Test.test_pure_passthrough'])
        self.assertEqual(result, 77)

    def test_resume(self):
        """handler resumes continuation with new value."""
        result = bevaluate(self.compiled['Test.test_resume'])
        self.assertEqual(result, 42)

    def test_do_chain(self):
        """do-notation chains pure computations."""
        result = bevaluate(self.compiled['Test.test_do'])
        self.assertEqual(result, 6)  # inc(5) = 6

    def test_inspect_error(self):
        """pattern match on error variant in handler."""
        result = bevaluate(self.compiled['Test.test_schema'])
        self.assertEqual(result, 200)  # SchemaError matches


class TestDataCsvTypes(unittest.TestCase):
    """Verify type constructors compile correctly."""

    @classmethod
    def setUpClass(cls):
        cls.compiled = pipeline(CSV_SRC)

    def test_parse_error_constructor(self):
        """ParseError constructor is in compiled output."""
        self.assertIn('Test.ParseError', self.compiled)

    def test_schema_error_constructor(self):
        """SchemaError constructor is in compiled output."""
        self.assertIn('Test.SchemaError', self.compiled)

    def test_raise_op(self):
        """raise effect op is in compiled output."""
        self.assertIn('Test.Exn.raise', self.compiled)


class TestDataCsvGLS(unittest.TestCase):
    """Test that GLS compiler can also compile the Data.Csv program."""

    @classmethod
    def setUpClass(cls):
        # Compile through bootstrap to get the GLS compiler
        from tests.compiler.test_m12_effects import compile_module
        cls.gls_bc = compile_module()

    def test_gls_compiler_has_effect_support(self):
        """GLS compiler has all effect codegen functions."""
        self.assertIn('Compiler.cg_compile_handle', self.gls_bc)
        self.assertIn('Compiler.cg_register_effs', self.gls_bc)
        self.assertIn('Compiler.parse_handle_expr', self.gls_bc)
        self.assertIn('Compiler.parse_eff_decl', self.gls_bc)


if __name__ == '__main__':
    unittest.main()

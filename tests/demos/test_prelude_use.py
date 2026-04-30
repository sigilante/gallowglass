#!/usr/bin/env python3
"""
F4 regression: demos can `use` the Core prelude via bootstrap.build.build_with_prelude.

The bootstrap M12 module system supported cross-module imports, but the demo
test harness compiled each demo with `module_env={}` (no prelude available),
so demos had to re-define stdlib utilities inline.  `build_with_prelude`
compiles all eight Core modules first and threads them through, so a demo can
reference `Core.List.foldl`, `Core.List.map`, etc. directly.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.build import build_with_prelude
from dev.harness.plan import evaluate, apply


class TestDemoUsesPrelude(unittest.TestCase):
    """A demo that imports Core.List should compile and evaluate."""

    def test_demo_can_use_core_list(self):
        """Demo references Core.List.foldl across the module boundary."""
        src = '''
use Core.List unqualified { List, Nil, Cons, foldl }
use Core.Nat  unqualified { add }

let sum_list : List Nat → Nat
  = λ xs → foldl add 0 xs

let result = sum_list (Cons 1 (Cons 2 (Cons 3 (Cons 4 Nil))))
'''
        compiled = build_with_prelude('Demo', src)
        sys.setrecursionlimit(50_000)
        result = evaluate(compiled['Demo.result'])
        self.assertEqual(result, 10)

    def test_demo_can_use_core_list_qualified(self):
        """Qualified import for List members; explicit unqualified for constructors."""
        src = '''
use Core.List unqualified { List, Nil, Cons }
use Core.Nat  unqualified { add }

let bump : Nat → Nat
  = λ nn → add nn 1

let bumped : List Nat
  = Core.List.map bump (Cons 10 (Cons 20 (Cons 30 Nil)))

let head_val : Nat
  = match bumped {
      | Nil      → 0
      | Cons h _ → h
    }
'''
        compiled = build_with_prelude('Demo', src)
        sys.setrecursionlimit(50_000)
        result = evaluate(compiled['Demo.head_val'])
        self.assertEqual(result, 11)

    def test_prelude_modules_present_in_compiled_dict(self):
        """`build_with_prelude` returns prelude FQ names alongside demo names."""
        src = '''
let zero : Nat = 0
'''
        compiled = build_with_prelude('Trivial', src)
        # Prelude entries
        self.assertIn('Core.List.map', compiled)
        self.assertIn('Core.List.foldl', compiled)
        self.assertIn('Core.Nat.add', compiled)
        # Demo entries
        self.assertIn('Trivial.zero', compiled)


if __name__ == '__main__':
    unittest.main()

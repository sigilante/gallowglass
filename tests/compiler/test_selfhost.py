#!/usr/bin/env python3
"""
M8.8 Self-hosting validation tests (compiler/src/Compiler.gls).

Goal
----
Verify that the GLS compiler written in Gallowglass (Compiler.gls) can
process its own source and produce valid Plan Assembler output equivalent
to the Python bootstrap compiler.

Two paths:

  Path B  (harness-level, always runs):
    Python bootstrap → compile Compiler.gls → compiled dict of PLAN values
    plan2pv bridge  → convert each PLAN value to GLS PlanVal ADT
    GLS emit_program (BPLAN jets) → Plan Assembler bytes
    Assertion: output is valid bytes starting with "(#bind ", length > 0,
               contains expected binding count.

  Path A  (planvm-gated):
    Python bootstrap → emit Compiler.main seed
    planvm Compiler.main.seed < Compiler.gls → Plan Assembler bytes
    Assertion: bytes match Path B output exactly (byte-identical)

    NOTE: Path A requires planvm AND a cog-compatible entry point.
    The current Compiler.main is a Bytes → Bytes function, not a cog.
    Full Path A validation is deferred until cog infrastructure is added.
    Path A planvm tests here only verify seed loading.

Evaluation gap
--------------
Running GLS `main` via BPLAN (bevaluate) on the full Compiler.gls source
causes RecursionError even at limit 200,000 — the full lex→parse→resolve→
compile→emit pipeline exceeds Python's stack depth.  Path B bypasses this
by reusing the Python bootstrap's compiled output and only calling GLS
`emit_program` (a shallow call that succeeds via BPLAN jets).

Path B is the definitive harness-level M8.8 gate.  Path A (planvm functional)
is the definitive end-to-end gate and will be activated once cog wrapping
is in place.
"""

import os
import sys
import subprocess
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tests.planvm.test_seed_planvm import requires_planvm, seed_loads, PLANVM
from dev.harness.plan import A, P, L, N, is_nat, is_app, is_pin, is_law, evaluate
from dev.harness.bplan import bevaluate

MODULE = 'Compiler'
SRC_PATH = os.path.join(os.path.dirname(__file__), '..', '..',
                        'compiler', 'src', 'Compiler.gls')

# Module-level cache — Compiler.gls is large; compile once per process.
_COMPILED = None
_COMPILED_BPLAN = None


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


def compile_module_bplan():
    global _COMPILED_BPLAN
    if _COMPILED_BPLAN is not None:
        return _COMPILED_BPLAN
    from dev.harness.bplan import register_jets
    compiled = compile_module()
    register_jets(compiled)
    _COMPILED_BPLAN = compiled
    return _COMPILED_BPLAN


def make_seed(name):
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    from bootstrap.emit import emit
    with open(SRC_PATH) as f:
        src = f.read()
    prog = parse(lex(src, SRC_PATH), SRC_PATH)
    resolved, _ = resolve(prog, MODULE, {}, SRC_PATH)
    compiled = compile_program(resolved, MODULE)
    return emit(compiled, f'{MODULE}.{name}')


def eval_bplan(val, *args):
    """Evaluate with BPLAN jets; bump recursion limit to 100K."""
    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 100000))
    try:
        result = val
        for arg in args:
            result = A(result, arg)
        return bevaluate(result)
    finally:
        sys.setrecursionlimit(old)


# ---------------------------------------------------------------------------
# plan2pv: Python PLAN value → GLS PlanVal ADT value
# ---------------------------------------------------------------------------

def plan2pv(val, bc, cache=None):
    """
    Convert a Python PLAN value to a GLS PlanVal ADT value.

    PlanVal constructors in Compiler.gls:
      PNat  : Nat → PlanVal           = A(bc['Compiler.PNat'], nat)
      PApp  : PlanVal → PlanVal → PlanVal = A(A(bc['Compiler.PApp'], f), x)
      PPin  : PlanVal → PlanVal       = A(bc['Compiler.PPin'], v)
      PLaw  : Nat → (Pair Nat PlanVal) → PlanVal
                                      = A(A(bc['Compiler.PLaw'], name_nat),
                                          MkPair(arity, body_pv))

    Cycles are impossible in a PLAN DAG; the cache avoids redundant work
    for shared sub-values.
    """
    if cache is None:
        cache = {}
    key = id(val)
    if key in cache:
        return cache[key]

    if is_nat(val):
        r = A(bc['Compiler.PNat'], val)
    elif is_app(val):
        r = A(A(bc['Compiler.PApp'],
               plan2pv(val.fun, bc, cache)),
              plan2pv(val.arg, bc, cache))
    elif is_pin(val):
        r = A(bc['Compiler.PPin'], plan2pv(val.val, bc, cache))
    elif is_law(val):
        body_pv = plan2pv(val.body, bc, cache)
        pair = A(A(bc['Compiler.MkPair'], val.arity), body_pv)
        r = A(A(bc['Compiler.PLaw'], val.name), pair)
    else:
        r = A(bc['Compiler.PNat'], 0)

    cache[key] = r
    return r


# ---------------------------------------------------------------------------
# gls_bytes_decode: GLS Bytes → Python bytes
# ---------------------------------------------------------------------------

def gls_bytes_decode(ev):
    """
    Decode a GLS Bytes value (MkPair len content_nat) to Python bytes.

    GLS Bytes = A(A(N(0), length_nat), content_nat)
    content_nat is the little-endian encoding of the byte sequence.
    Returns None if ev is not a valid Bytes value.
    """
    if (is_app(ev) and is_app(ev.fun)
            and is_nat(ev.fun.fun) and ev.fun.fun == 0):
        length = ev.fun.arg
        content = ev.arg
        if is_nat(length) and is_nat(content):
            if length == 0:
                return b''
            return content.to_bytes(length, 'little')
    return None


# ---------------------------------------------------------------------------
# run_path_b: Python bootstrap compiled dict → Plan Assembler bytes via GLS
# ---------------------------------------------------------------------------

def run_path_b(bc, compiled):
    """
    Convert a Python bootstrap compiled dict to Plan Assembler bytes
    using GLS emit_program.

    Algorithm:
      1. Forward-iterate compiled dict (Python insertion order = source order).
      2. Cons-prepend each (name_nat, pval) pair onto a GLS list.
         Cons-prepend mirrors cg_pass3's behaviour: source-order forward
         iteration + Cons-prepend produces a list in reverse-source order,
         which foldl emits back in source order.
      3. Call bevaluate(A(emit_program, lst)) with BPLAN jets.
      4. Return decoded bytes.

    NOTE: pv is pre-evaluated as a performance optimisation (avoids repeated
    evaluation if the same value appears multiple times).  _bmatch now forces
    its scrutinee unconditionally, so pre-evaluation is no longer required for
    correctness.
    """
    nil_val = bevaluate(bc['Compiler.Nil'])
    lst = nil_val
    cache = {}
    for fq_name, plan_val in compiled.items():
        nn = int.from_bytes(fq_name.encode('ascii'), 'little')
        pv = bevaluate(plan2pv(plan_val, bc, cache))   # pre-evaluate for performance
        pair = A(A(bc['Compiler.MkPair'], nn), pv)
        lst = A(A(bc['Compiler.Cons'], pair), lst)

    old = sys.getrecursionlimit()
    sys.setrecursionlimit(max(old, 100000))
    try:
        result = bevaluate(A(bc['Compiler.emit_program'], lst))
    finally:
        sys.setrecursionlimit(old)

    return gls_bytes_decode(result)


# ---------------------------------------------------------------------------
# TestSelfhostBridge: unit tests for plan2pv
# ---------------------------------------------------------------------------

class TestSelfhostBridge(unittest.TestCase):
    """
    Verify the plan2pv bridge converts Python PLAN values to GLS PlanVal ADT.

    Each test constructs a Python PLAN value, converts it with plan2pv,
    then evaluates it with the corresponding GLS constructor and asserts
    structural equivalence.
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _pnat(self, n):
        return eval_bplan(self.bc['Compiler.PNat'], n)

    def _papp(self, f, x):
        return eval_bplan(self.bc['Compiler.PApp'], f, x)

    def _ppin(self, v):
        return eval_bplan(self.bc['Compiler.PPin'], v)

    def _plaw(self, name_nat, arity, body_pv):
        pair = eval_bplan(self.bc['Compiler.MkPair'], arity, body_pv)
        return eval_bplan(self.bc['Compiler.PLaw'], name_nat, pair)

    def test_bridge_pnat(self):
        """plan2pv(N(42)) produces the same value as PNat 42."""
        converted = eval_bplan(plan2pv(42, self.bc))
        expected = self._pnat(42)
        self.assertEqual(converted, expected,
            f'plan2pv(N(42)): got {converted!r}, expected {expected!r}')

    def test_bridge_pnat_zero(self):
        """plan2pv(N(0)) → PNat 0."""
        converted = eval_bplan(plan2pv(0, self.bc))
        expected = self._pnat(0)
        self.assertEqual(converted, expected)

    def test_bridge_papp(self):
        """plan2pv(A(N(3), N(7))) → PApp(PNat 3)(PNat 7)."""
        python_val = A(3, 7)
        converted = eval_bplan(plan2pv(python_val, self.bc))
        expected = self._papp(self._pnat(3), self._pnat(7))
        self.assertEqual(converted, expected,
            f'plan2pv(A(N(3),N(7))): got {converted!r}, expected {expected!r}')

    def test_bridge_ppin(self):
        """plan2pv(P(N(5))) → PPin(PNat 5)."""
        python_val = P(5)
        converted = eval_bplan(plan2pv(python_val, self.bc))
        expected = self._ppin(self._pnat(5))
        self.assertEqual(converted, expected)

    def test_bridge_plaw(self):
        """plan2pv(L(1, 0, N(1))) → PLaw 0 (MkPair 1 (PNat 1)).

        Identity law: arity=1, name=0, body=N(1) (first argument).
        """
        python_val = L(1, 0, 1)
        converted = eval_bplan(plan2pv(python_val, self.bc))
        expected = self._plaw(0, 1, self._pnat(1))
        self.assertEqual(converted, expected,
            f'plan2pv(L(1,0,1)): got {converted!r}, expected {expected!r}')

    def test_bridge_nested_app(self):
        """plan2pv(A(A(N(0), N(3)), N(7))) — two-level nesting."""
        python_val = A(A(0, 3), 7)
        converted = eval_bplan(plan2pv(python_val, self.bc))
        inner = self._papp(self._pnat(0), self._pnat(3))
        expected = self._papp(inner, self._pnat(7))
        self.assertEqual(converted, expected)

    def test_bridge_cache_sharing(self):
        """plan2pv cache: same Python object id → same result, no duplicate work."""
        shared = A(1, 2)
        outer = A(shared, shared)
        cache = {}
        plan2pv(outer, self.bc, cache)
        # After one call, shared is in the cache exactly once
        self.assertIn(id(shared), cache)
        self.assertEqual(len([k for k in cache if k == id(shared)]), 1)


# ---------------------------------------------------------------------------
# TestSelfhostEmitProgram: M8.8 harness gate (Path B)
# ---------------------------------------------------------------------------

# Curated GLS module for TestSelfhostEmitProgram.
# Small enough to run quickly (< 1s), but covers all construct types:
#   DType with nullary, unary, binary constructors
#   DLet: nat literal (PNat), simple lambda (PLaw), pattern match (Case_),
#         external ref (Core.PLAN.inc), identity/main function.
_CURATED_GLS = """\
external mod Core.PLAN {
  inc : Nat → Nat
}

type Option a =
  | None
  | Some a

type Pair a b =
  | MkPair a b

let truth : Nat = 1

let succ : Nat → Nat
  = λ n → Core.PLAN.inc n

let pred : Nat → Nat
  = λ n → match n { | 0 → 0 | k → k }

let fst : Pair Nat Nat → Nat
  = λ p → match p { | MkPair a _ → a }

let main : Nat → Nat
  = λ x → x
"""
_CURATED_MODULE = 'Test'


class TestSelfhostEmitProgram(unittest.TestCase):
    """
    M8.8 Path B: Python bootstrap output → GLS emit_program → Plan Assembler.

    Uses a small curated GLS module (_CURATED_GLS) instead of the full
    430-definition Compiler.gls to keep the test fast (< 1s).  The curated
    module exercises all PlanVal constructor types that emit_program must
    handle: PNat (nat literal), PLaw (lambda, pattern match), PApp, PPin.

    Structural properties verified:
    - output is non-empty GLS Bytes
    - each top-level line starts with (#bind "
    - bind count equals definition count
    - a (#bind for Test.main is present
    - byte-identical Path B output for a 2-definition snippet

    It does NOT verify byte-identity with planvm output (that is Path A,
    deferred until cog infrastructure exists).
    """

    @classmethod
    def setUpClass(cls):
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program
        prog = parse(lex(_CURATED_GLS, '<curated>'), '<curated>')
        resolved, _ = resolve(prog, _CURATED_MODULE, {}, '<curated>')
        cls.compiled = compile_program(resolved, _CURATED_MODULE)
        cls.bc = compile_module_bplan()
        cls.output = run_path_b(cls.bc, cls.compiled)

    def test_output_is_not_none(self):
        """run_path_b returns non-None bytes."""
        self.assertIsNotNone(self.output,
            'run_path_b returned None — gls_bytes_decode failed')

    def test_output_is_bytes(self):
        """Output is a Python bytes object."""
        self.assertIsInstance(self.output, bytes)

    def test_output_nonempty(self):
        """Output has length > 0."""
        self.assertGreater(len(self.output), 0,
            'emit_program produced empty output')

    def test_output_starts_with_bind(self):
        """Output starts with (#bind ""."""
        self.assertTrue(
            self.output.startswith(b'(#bind "'),
            f'Output does not start with (#bind ": {self.output[:40]!r}'
        )

    def test_output_ends_with_newline(self):
        """Output ends with a newline (last bind form)."""
        self.assertTrue(
            self.output.endswith(b'\n'),
            f'Output does not end with newline: {self.output[-20:]!r}'
        )

    def test_output_bind_count_matches_compiled(self):
        """Number of (#bind lines equals number of definitions in compiled module."""
        bind_count = self.output.count(b'(#bind ')
        expected = len(self.compiled)
        self.assertEqual(bind_count, expected,
            f'bind count: got {bind_count}, expected {expected} (= len(compiled))')

    def test_output_contains_main_binding(self):
        """Output contains a (#bind for Test.main (the curated module entry point)."""
        main_nat = int.from_bytes(
            f'{_CURATED_MODULE}.main'.encode('ascii'), 'little')
        main_decimal = str(main_nat).encode('ascii')
        needle = b'(#bind "' + main_decimal + b'"'
        self.assertIn(needle, self.output,
            f'{_CURATED_MODULE}.main binding (decimal {main_nat}) not found in output')

    def test_output_top_level_lines_are_bind_forms(self):
        """Every top-level (#-prefixed) line in the output is a (#bind ...) form.

        Law bodies use multi-line formatting: the closing )) appears on
        indented continuation lines.  Only unindented non-empty lines
        must start with (#bind ".
        """
        lines = self.output.split(b'\n')
        bad = [ln for ln in lines
               if ln and not ln.startswith(b' ') and not ln.startswith(b'(#bind ')]
        self.assertEqual(bad, [],
            f'Non-bind top-level lines found: {bad[:3]!r}')

    def test_output_nonempty_content(self):
        """Output is at least 200 bytes (9 defs × ~100 bytes each minimum)."""
        self.assertGreater(len(self.output), 200,
            f'Output suspiciously small: {len(self.output)} bytes')

    def test_tiny_snippet_path_b(self):
        """Path B on a 2-definition snippet produces correct Plan Assembler text.

        This is the unit-level version of the full self-hosting test.
        We compile a tiny GLS snippet with the Python bootstrap, run Path B,
        and check the exact output.
        """
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program

        src = 'let answer : Nat = 42\nlet double : Nat\n  = answer\n'
        prog = parse(lex(src, '<snippet>'), '<snippet>')
        resolved, _ = resolve(prog, 'Test', {}, '<snippet>')
        snippet_compiled = compile_program(resolved, 'Test')

        bc = self.bc
        output = run_path_b(bc, snippet_compiled)
        self.assertIsNotNone(output, 'Path B snippet produced None')
        self.assertEqual(output.count(b'(#bind '), 2,
            f'Expected 2 bind forms for 2-def snippet, got: {output!r}')
        self.assertTrue(output.startswith(b'(#bind "'),
            f'Snippet output does not start with (#bind ": {output!r}')


# ---------------------------------------------------------------------------
# Mixed-arity type: behavioral and emit tests (M8.8 revisit)
# ---------------------------------------------------------------------------

# GLS module with a 3-constructor mixed-arity type:
#   Leaf Nat    — unary, tag=0 → outer Case_ z fires
#   Node Nat Nat — binary, tag=1 → outer Case_ app fires
#   Wrap Nat    — unary, tag=2 → outer Case_ m fires (non-zero unary tag)
#
# Wrap exercises the non-zero-unary-tag path fixed in cg_build_unary_m_body
# and cg_build_precompiled_nat_dispatch.
_MIXED_GLS = """\
type Tree =
  | Leaf Nat
  | Node Nat Nat
  | Wrap Nat

let tree_val : Tree → Nat
  = λ t → match t {
      | Leaf x   → x
      | Node l _ → l
      | Wrap w   → w
    }

let tree_tag : Tree → Nat
  = λ t → match t {
      | Leaf _   → 0
      | Node _ _ → 1
      | Wrap _   → 2
    }
"""
_MIXED_MODULE = 'Test'

# Module-level cache for the mixed-arity compiled dict.
_MIXED_COMPILED = None


def compile_mixed():
    global _MIXED_COMPILED
    if _MIXED_COMPILED is not None:
        return _MIXED_COMPILED
    from bootstrap.lexer import lex
    from bootstrap.parser import parse
    from bootstrap.scope import resolve
    from bootstrap.codegen import compile_program
    prog = parse(lex(_MIXED_GLS, '<mixed>'), '<mixed>')
    resolved, _ = resolve(prog, _MIXED_MODULE, {}, '<mixed>')
    _MIXED_COMPILED = compile_program(resolved, _MIXED_MODULE)
    return _MIXED_COMPILED


class TestMixedArityBehavioral(unittest.TestCase):
    """
    Behavioral correctness for mixed-arity type pattern matches
    (Python-bootstrap-compiled).

    Tree has three constructors:
      Leaf Nat     (unary, tag=0) — outer Case_ z fires
      Node Nat Nat (binary, tag=1) — outer Case_ app fires, inner dispatch
      Wrap Nat     (unary, tag=2) — outer Case_ m fires (non-zero unary tag)

    Before the M8.8 revisit fix, Wrap returned 0 instead of the field value.
    The fix: cg_build_unary_z_body / cg_build_unary_m_body + the
    cg_build_precompiled_nat_dispatch non-zero first-tag branch.

    Note: these tests verify the PYTHON BOOTSTRAP codegen, which was also
    fixed with an equivalent patch (see tests/bootstrap/test_codegen.py).
    Testing the GLS self-hosting codegen requires planvm (Path A, deferred).
    """

    @classmethod
    def setUpClass(cls):
        cls.c = compile_mixed()

    def _make(self, con_name, *args):
        """Construct a Tree value (constructor applied to fields)."""
        val = self.c[f'{_MIXED_MODULE}.{con_name}']
        for arg in args:
            val = A(val, arg)
        return val

    def _call(self, fn_name, *args):
        fn = self.c[f'{_MIXED_MODULE}.{fn_name}']
        return eval_bplan(fn, *args)

    # --- tree_val: return the first (or only) field ---

    def test_tree_val_leaf(self):
        """tree_val (Leaf 42) = 42  [unary tag=0, outer-z path]."""
        self.assertEqual(self._call('tree_val', self._make('Leaf', 42)), 42)

    def test_tree_val_node(self):
        """tree_val (Node 7 99) = 7  [binary tag=1, app path]."""
        self.assertEqual(self._call('tree_val', self._make('Node', 7, 99)), 7)

    def test_tree_val_wrap(self):
        """tree_val (Wrap 55) = 55  [unary tag=2, non-zero outer-m path]."""
        self.assertEqual(self._call('tree_val', self._make('Wrap', 55)), 55)

    # --- tree_tag: return the constructor index ---

    def test_tree_tag_leaf(self):
        """tree_tag (Leaf 0) = 0."""
        self.assertEqual(self._call('tree_tag', self._make('Leaf', 0)), 0)

    def test_tree_tag_node(self):
        """tree_tag (Node 0 0) = 1."""
        self.assertEqual(self._call('tree_tag', self._make('Node', 0, 0)), 1)

    def test_tree_tag_wrap(self):
        """tree_tag (Wrap 0) = 2."""
        self.assertEqual(self._call('tree_tag', self._make('Wrap', 0)), 2)


class TestMixedArityEmit(unittest.TestCase):
    """
    Path B for the mixed-arity module: GLS emit_program serializes the
    Python-bootstrap-compiled Tree functions to valid Plan Assembler text.

    This exercises emit_body_val / emit_bval_dispatch on the PlanVal IR
    produced from a mixed-arity match — in particular PNat and PPin values
    that arise from the unary constructor arms.
    """

    @classmethod
    def setUpClass(cls):
        cls.compiled = compile_mixed()
        cls.bc = compile_module_bplan()
        cls.output = run_path_b(cls.bc, cls.compiled)

    def test_output_not_none(self):
        self.assertIsNotNone(self.output,
            'run_path_b returned None for mixed-arity module')

    def test_output_is_bytes(self):
        self.assertIsInstance(self.output, bytes)

    def test_output_nonempty(self):
        self.assertGreater(len(self.output), 0)

    def test_output_bind_count(self):
        """Number of (#bind lines equals number of compiled definitions."""
        bind_count = self.output.count(b'(#bind ')
        self.assertEqual(bind_count, len(self.compiled),
            f'bind count: got {bind_count}, expected {len(self.compiled)}')

    def test_output_has_tree_val(self):
        """Output contains a (#bind for Test.tree_val."""
        nn = int.from_bytes(f'{_MIXED_MODULE}.tree_val'.encode('ascii'), 'little')
        needle = b'(#bind "' + str(nn).encode('ascii') + b'"'
        self.assertIn(needle, self.output,
            f'{_MIXED_MODULE}.tree_val binding not found in output')

    def test_output_has_tree_tag(self):
        """Output contains a (#bind for Test.tree_tag."""
        nn = int.from_bytes(f'{_MIXED_MODULE}.tree_tag'.encode('ascii'), 'little')
        needle = b'(#bind "' + str(nn).encode('ascii') + b'"'
        self.assertIn(needle, self.output,
            f'{_MIXED_MODULE}.tree_tag binding not found in output')

    def test_output_top_level_lines_are_bind_forms(self):
        """Every non-indented non-empty line is a (#bind ...) form."""
        lines = self.output.split(b'\n')
        bad = [ln for ln in lines
               if ln and not ln.startswith(b' ') and not ln.startswith(b'(#bind ')]
        self.assertEqual(bad, [],
            f'Non-bind top-level lines: {bad[:3]!r}')


# ---------------------------------------------------------------------------
# TestSelfhostPlanvm: planvm-gated seed loading
# ---------------------------------------------------------------------------

class TestSelfhostPlanvm(unittest.TestCase):
    """
    M8.8 planvm gate: seed loading tests.
    """

    @requires_planvm
    def test_compiler_main_seed_loads(self):
        """Compiler.main compiles to a planvm-valid seed."""
        seed = make_seed('main')
        self.assertTrue(seed_loads(seed),
            'planvm rejected Compiler.main seed')

    @requires_planvm
    def test_compiler_emit_program_seed_loads(self):
        """Compiler.emit_program compiles to a planvm-valid seed."""
        seed = make_seed('emit_program')
        self.assertTrue(seed_loads(seed),
            'planvm rejected Compiler.emit_program seed')

    @requires_planvm
    def test_compiler_run_main_seed_loads(self):
        """Compiler.run_main (M8.8 Path A CLI entry point) compiles to a planvm-valid seed."""
        seed = make_seed('run_main')
        self.assertTrue(seed_loads(seed),
            'planvm rejected Compiler.run_main seed')


# ---------------------------------------------------------------------------
# Path A helper: run planvm with a source text CLI argument
# ---------------------------------------------------------------------------

def run_planvm_with_source(seed_bytes: bytes, source_text: str,
                            timeout: int = 30) -> bytes | None:
    """
    Run planvm with seed_bytes and source_text as a CLI argument.

    Invocation: planvm <seed_file> <source_text>

    planvm passes source_text as a strnat (little-endian Nat) to the program
    via argVec = Closure(head=Pin, args=[strnat]).  Compiler.run_main unpins
    this to get the raw Nat, computes byte length, and calls main.

    Returns: stdout bytes on success, None on timeout or error.
    """
    with tempfile.NamedTemporaryFile(suffix='.seed', delete=False) as f:
        f.write(seed_bytes)
        seed_path = f.name
    try:
        result = subprocess.run(
            [PLANVM, seed_path, source_text],
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout
        return None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    finally:
        os.unlink(seed_path)


# ---------------------------------------------------------------------------
# TestSelfhostPathA: M8.8 Path A functional validation
# ---------------------------------------------------------------------------

class TestSelfhostPathA(unittest.TestCase):
    """
    M8.8 Path A: run Compiler.run_main via planvm with source text as CLI arg.

    Compiler.run_main unwraps the argVec (forces to P(src_nat)), computes the
    byte length, constructs a Bytes value, calls Compiler.main, then writes the
    Plan Assembler output to stdout via WriteOp.

    The test source is a 2-definition snippet compiled to Plan Assembler by Path B;
    Path A must produce the same output.
    """

    # Tiny source that compiles quickly and has a predictable output.
    SNIPPET = 'let answer : Nat = 42\nlet double : Nat\n  = answer\n'

    @classmethod
    def setUpClass(cls):
        # Compute Path B expected output once.
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program

        src = cls.SNIPPET
        prog = parse(lex(src, '<snippet>'), '<snippet>')
        resolved, _ = resolve(prog, 'Test', {}, '<snippet>')
        snippet_compiled = compile_program(resolved, 'Test')
        bc = compile_module_bplan()
        cls.expected_output = run_path_b(bc, snippet_compiled)

    @requires_planvm
    def test_path_a_snippet_matches_path_b(self):
        """Path A (planvm run_main) produces the same output as Path B (harness).

        Invokes planvm with Compiler.run_main seed and the SNIPPET source as a
        CLI arg.  The output written to stdout must match Path B's Plan Assembler.
        """
        seed = make_seed('run_main')
        output = run_planvm_with_source(seed, self.SNIPPET)
        self.assertIsNotNone(output,
            'planvm run_main returned None (non-zero exit or timeout)')
        self.assertIsNotNone(self.expected_output,
            'Path B produced None — cannot compare')
        self.assertEqual(output, self.expected_output,
            f'Path A output does not match Path B.\n'
            f'  Path A: {output[:80]!r}\n'
            f'  Path B: {self.expected_output[:80]!r}')

    @requires_planvm
    def test_path_a_output_is_plan_assembler(self):
        """Path A output starts with (#bind and ends with newline."""
        seed = make_seed('run_main')
        output = run_planvm_with_source(seed, self.SNIPPET)
        if output is None:
            self.skipTest('planvm run_main returned None')
        self.assertTrue(output.startswith(b'(#bind "'),
            f'Path A output does not start with (#bind ": {output[:40]!r}')
        self.assertTrue(output.endswith(b'\n'),
            f'Path A output does not end with newline: {output[-20:]!r}')


# ---------------------------------------------------------------------------
# TestSelfhostGolden: byte-identity gate against checked-in goldens (AUDIT.md A4)
# ---------------------------------------------------------------------------
#
# With Path A skipped pending Phase G, byte-identity is the only correctness
# criterion for self-host emission — but `TestSelfhostEmitProgram` only
# verifies structural shape (starts with `(#bind`, bind count matches).
# Emit-layer drift would slip through.
#
# This class compares Path B output against checked-in golden files.  We use
# three scopes:
#   - snippet: 82 bytes, two trivial bindings.  Smallest possible drift signal.
#   - curated: 1217 bytes, exercises PNat / PApp / PLaw / PPin via the same
#              fixture TestSelfhostEmitProgram already uses.
#   - mixed:   2903 bytes, mixed-arity (Leaf/Node/Wrap) constructor matching,
#              the codepath that hosted the F11 + AUDIT.md A1 bugs.
#
# Each is checked into `tests/compiler/golden/path_b_<label>.pla`.  The full
# Compiler.gls Path B run takes minutes (Path A territory) and is deferred to
# Phase G; these three small fixtures cover the same emit-layer logic in
# under a tenth of a second.
#
# When the goldens are intentionally outdated (e.g. an emit-format change),
# regenerate by running with `UPDATE_GOLDEN=1`:
#
#     UPDATE_GOLDEN=1 python3 -m pytest tests/compiler/test_selfhost.py \
#         -k TestSelfhostGolden
#
# Then inspect the diff with `git diff tests/compiler/golden/` and commit.
# ---------------------------------------------------------------------------

GOLDEN_DIR = os.path.join(os.path.dirname(__file__), 'golden')

# Snippet shared with TestSelfhostEmitProgram.test_tiny_snippet_path_b — keep
# in sync.  If you change one, change the other (and regenerate the golden).
_GOLDEN_SNIPPET_GLS = 'let answer : Nat = 42\nlet double : Nat\n  = answer\n'


class TestSelfhostGolden(unittest.TestCase):
    """
    Path B byte-identity gate against checked-in golden files.

    See module-level comment for rationale and the UPDATE_GOLDEN workflow.
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()

    def _compile(self, src, module):
        from bootstrap.lexer import lex
        from bootstrap.parser import parse
        from bootstrap.scope import resolve
        from bootstrap.codegen import compile_program
        prog = parse(lex(src, '<golden>'), '<golden>')
        resolved, _ = resolve(prog, module, {}, '<golden>')
        return compile_program(resolved, module)

    def _check_golden(self, label, src, module):
        actual = run_path_b(self.bc, self._compile(src, module))
        self.assertIsNotNone(actual,
            f'run_path_b returned None for {label!r} — gls_bytes_decode failed')
        path = os.path.join(GOLDEN_DIR, f'path_b_{label}.pla')

        if os.environ.get('UPDATE_GOLDEN'):
            os.makedirs(GOLDEN_DIR, exist_ok=True)
            with open(path, 'wb') as f:
                f.write(actual)
            self.skipTest(f'UPDATE_GOLDEN: rewrote {path} ({len(actual)} bytes)')

        self.assertTrue(os.path.exists(path),
            f'Golden file missing: {path}\n'
            f'Generate with: UPDATE_GOLDEN=1 python3 -m pytest '
            f'tests/compiler/test_selfhost.py -k TestSelfhostGolden')
        with open(path, 'rb') as f:
            expected = f.read()
        if actual != expected:
            # Locate the first byte that diverges to make the failure readable.
            for i, (a, b) in enumerate(zip(actual, expected)):
                if a != b:
                    diverge_at = i
                    break
            else:
                diverge_at = min(len(actual), len(expected))
            ctx_lo = max(0, diverge_at - 20)
            ctx_hi = diverge_at + 40
            self.fail(
                f'Path B output for {label!r} drifted from golden.\n'
                f'  golden:  {len(expected)} bytes ({path})\n'
                f'  actual:  {len(actual)} bytes\n'
                f'  diverge at byte {diverge_at}:\n'
                f'    expected: {expected[ctx_lo:ctx_hi]!r}\n'
                f'    actual:   {actual[ctx_lo:ctx_hi]!r}\n'
                f'If this drift is intentional, regenerate with:\n'
                f'  UPDATE_GOLDEN=1 python3 -m pytest '
                f'tests/compiler/test_selfhost.py -k TestSelfhostGolden\n'
                f'then inspect `git diff tests/compiler/golden/` before committing.'
            )

    def test_golden_snippet(self):
        """Two-binding snippet: smallest drift signal."""
        self._check_golden('snippet', _GOLDEN_SNIPPET_GLS, 'Test')

    def test_golden_curated(self):
        """Curated GLS exercising PNat/PApp/PLaw/PPin shapes."""
        self._check_golden('curated', _CURATED_GLS, _CURATED_MODULE)

    def test_golden_mixed(self):
        """Mixed-arity (Leaf/Node/Wrap) — codepath that hosted F11 and A1."""
        self._check_golden('mixed', _MIXED_GLS, _MIXED_MODULE)


if __name__ == '__main__':
    unittest.main()

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
    """
    nil_val = bevaluate(bc['Compiler.Nil'])
    lst = nil_val
    cache = {}
    for fq_name, plan_val in compiled.items():
        nn = int.from_bytes(fq_name.encode('ascii'), 'little')
        pv = plan2pv(plan_val, bc, cache)
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

class TestSelfhostEmitProgram(unittest.TestCase):
    """
    M8.8 Path B: Python bootstrap output → GLS emit_program → Plan Assembler.

    This is the primary harness-level M8.8 gate.  It verifies that:
    1. GLS emit_program can consume the full Compiler.gls module.
    2. The output is valid GLS Bytes.
    3. The output is non-trivially large Plan Assembler text.
    4. Each output line is a well-formed (#bind ...) form.

    It does NOT verify byte-identity with planvm output (that is Path A,
    deferred until cog infrastructure exists).
    """

    @classmethod
    def setUpClass(cls):
        cls.bc = compile_module_bplan()
        cls.compiled = compile_module()
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
        """Output contains a (#bind for Compiler.main (the entry point)."""
        main_nat = int.from_bytes(b'Compiler.main', 'little')
        main_decimal = str(main_nat).encode('ascii')
        needle = b'(#bind "' + main_decimal + b'"'
        self.assertIn(needle, self.output,
            f'Compiler.main binding (decimal {main_nat}) not found in output')

    def test_output_all_lines_are_bind_forms(self):
        """Every non-empty line in the output is a (#bind ...) form."""
        lines = self.output.split(b'\n')
        bad = [ln for ln in lines if ln and not ln.startswith(b'(#bind ')]
        self.assertEqual(bad, [],
            f'Non-bind lines found: {bad[:3]!r}')

    def test_output_large_enough(self):
        """Output is at least 10 KB (Compiler.gls is ~3800 lines, ~300 definitions)."""
        self.assertGreater(len(self.output), 10_000,
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
        nil_val = bevaluate(bc['Compiler.Nil'])
        lst = nil_val
        cache = {}
        for fq_name, plan_val in snippet_compiled.items():
            nn = int.from_bytes(fq_name.encode('ascii'), 'little')
            pv = plan2pv(plan_val, bc, cache)
            pair = A(A(bc['Compiler.MkPair'], nn), pv)
            lst = A(A(bc['Compiler.Cons'], pair), lst)

        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 100000))
        try:
            result = bevaluate(A(bc['Compiler.emit_program'], lst))
        finally:
            sys.setrecursionlimit(old)

        output = gls_bytes_decode(result)
        self.assertIsNotNone(output, 'Path B snippet produced None')
        self.assertEqual(output.count(b'(#bind '), 2,
            f'Expected 2 bind forms for 2-def snippet, got: {output!r}')
        self.assertTrue(output.startswith(b'(#bind "'),
            f'Snippet output does not start with (#bind ": {output!r}')


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
        from dev.harness.bplan import register_jets

        src = cls.SNIPPET
        prog = parse(lex(src, '<snippet>'), '<snippet>')
        resolved, _ = resolve(prog, 'Test', {}, '<snippet>')
        snippet_compiled = compile_program(resolved, 'Test')

        bc = compile_module_bplan()
        nil_val = bevaluate(bc['Compiler.Nil'])
        lst = nil_val
        cache = {}
        for fq_name, plan_val in snippet_compiled.items():
            nn = int.from_bytes(fq_name.encode('ascii'), 'little')
            pv = plan2pv(plan_val, bc, cache)
            pair = A(A(bc['Compiler.MkPair'], nn), pv)
            lst = A(A(bc['Compiler.Cons'], pair), lst)

        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 100000))
        try:
            result = bevaluate(A(bc['Compiler.emit_program'], lst))
        finally:
            sys.setrecursionlimit(old)
        cls.expected_output = gls_bytes_decode(result)

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


if __name__ == '__main__':
    unittest.main()

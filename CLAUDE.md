# Gallowglass

Gallowglass is a programming language designed for LLMs to write and reason about, targeting the PLAN virtual machine. This repo contains the language specification, Python bootstrap compiler, core prelude (Gallowglass), and self-hosting compiler (Gallowglass).

## Design Principles (Gospel)

These are non-negotiable. When in doubt, return to these.

- **A contract is suspicious if it could only be written by someone who had already read the implementation. A contract is valuable if it could be written by someone who only had the mathematical specification.**
- **Contracts derive tests. Tests do not become contracts.**
- **Representation has audience. `Show` is for users. `Debug` is for developers. `Serialize` is for machines. Never conflate them.**
- Effects are always locally visible in type signatures. Nothing is hidden.
- Pure by default. Effect annotation is explicit, not implicit.
- Structural truth over convenient fictions. The type system never lies.

## Repository Structure

```
gallowglass/
  CLAUDE.md              ← you are here
  DECISIONS.md           ← design rationale for non-obvious choices
  SPEC.md                ← full architecture overview (read this first)
  ROADMAP.md             ← delivery plan and forward work

  spec/
    00-primitives.md     ← Core.Primitives: ~101 operations, 11 modules
    01-glass-ir.md       ← Glass IR formal grammar (PEG + well-formedness)
    02-mutual-recursion.md ← SCC compilation, shared pins, lambda lifting
    03-exhaustiveness.md ← Pattern match exhaustiveness checker design
    04-plan-encoding.md  ← How Gallowglass constructs map to PLAN
    05-type-system.md    ← Types, effects, rows, contracts
    06-surface-syntax.md ← Full surface grammar
    07-seed-format.md    ← Seed serialization format

  bootstrap/
    BOOTSTRAP.md         ← Bootstrap compiler overview
    *.py                 ← Python bootstrap compiler (lexer, parser, scope, codegen, emit)

  prelude/
    PRELUDE.md           ← Prelude scope and organization
    src/Core/            ← Gallowglass source (core prelude)

  compiler/
    COMPILER.md          ← Self-hosting compiler overview
    src/                 ← Gallowglass source (self-hosting compiler)

  tests/
    TESTS.md             ← Test strategy
    bootstrap/           ← Bootstrap compiler tests
    prelude/             ← Prelude tests
    compiler/            ← Self-hosting compiler tests
```

## Before Starting Any Task

1. Read `SPEC.md` for architecture context.
2. Read the relevant `spec/` document for the component you are working on.
3. Read the relevant `BOOTSTRAP.md`, `PRELUDE.md`, or `COMPILER.md` for implementation guidance.
4. Check `DECISIONS.md` if something seems surprising or you want to understand why.

## Upstream authority

Per Sol (PLAN author), the canonical specification of the Plan Assembler text
format and PLAN runtime semantics is **the Haskell implementation in
`vendor/reaver/src/hs/`**. PLAN proper (4 ctors, opcodes 0–2 = Pin/Law/Elim)
and the Plan Asm text format are frozen; the BPLAN named-op set may drift.

- `vendor/reaver/src/hs/PlanAssembler.hs` — Plan Asm text format authority.
- `vendor/reaver/src/hs/Plan.hs` — PLAN runtime + BPLAN/RPLAN dispatch authority.
- Other `vendor/reaver/doc/*` and `vendor/reaver/note/*` materials are
  explanatory/aspirational, not normative.
- `spec/04-plan-encoding.md` and `spec/07-seed-format.md §13` are *derived*
  documents — guides to reading the `.hs`. When derived docs disagree with
  `.hs`, `.hs` wins.

`vendor/` is gitignored. Pin discipline lives in `vendor.lock`. To populate
`vendor/`, run `tools/vendor.sh`. CI runs `tools/vendor.sh verify` to detect
pin drift. `tests/sanity/test_bplan_deps.py` greps `Plan.hs` to confirm every
BPLAN op in `bootstrap/bplan_deps.py` still exists at the right arity — this
is the canary for `vendor.lock` bumps.

## Language Quick Reference

### Naming Conventions (compiler-enforced)
- Functions and values: `snake_case`
- Types and effects: `PascalCase`
- Type variables: single lowercase `a`–`q`
- Row variables: single lowercase `r`–`z`
- Modules: `Dot.Qualified`

### Key Syntax
```gallowglass
-- Function definition: spec above =, impl below
let name : Type
  | pre  Proven (precondition)
  | post Deferred(NoSolver) (postcondition)
  = body

-- Effect row: {Effect1, Effect2 | r} ReturnType
let read_file : Path → {IO, Exn IOError | r} Bytes

-- Handler
handle computation {
  | return x   → x
  | raise e  k → default_value
}

-- Algebraic type
type Result a b =
  | Ok  a
  | Err b

-- Programmer pin (DAG node)
@result = expensive_computation x
```

### Canonical Unicode Operators
`→` `λ` `∀` `∃` `←` `·` `⊕` `⊗` `⊤` `⊥` `∅` `≠` `≤` `≥` `∈` `∉` `⊆`
ASCII alternatives are normalized to Unicode at the lexer — never appear post-lex.

### Effect System
- `Abort` is NOT in any effect row. It is unhandleable, propagates to the VM's virtualization supervisor. *(Not yet enforced by the bootstrap typechecker — AUDIT.md B5; gate test in `tests/bootstrap/test_typecheck.py::test_b5_abort_in_effect_row_is_rejected`.)*
- `External` marks VM boundary crossings; functions calling `external mod` operations must carry `External` in their effect row (spec error E0011). *(Not yet enforced — AUDIT.md B5; gate test in `tests/bootstrap/test_typecheck.py::test_b5_missing_external_is_rejected`.)*
- `{}` empty row means pure. Absence of annotation also means pure.
- Dictionaries are implicit in source, explicit in Glass IR.

## VM Target

PLAN — canonical ABI per `vendor/reaver/src/hs/Plan.hs`. Four constructors:
Pin `<i>`, Law `{n a b}`, App `(f g)`, Nat `@`. **Three opcodes**: Pin (0,
arity 1), Law (1, arity 3), Elim (2, arity 6 — formerly `Case_`). Inc, Force,
arithmetic, and introspection are **BPLAN named primitives** dispatched by
name+arity in `Plan.hs:op 66`; see `bootstrap/bplan_deps.py`. Output format:
Plan Assembler text (`vendor/reaver/src/hs/PlanAssembler.hs`). Hash algorithm:
BLAKE3-256.

All Gallowglass types are erased at compile time. The PLAN output is untyped. Type errors are purely a Gallowglass-layer concern.

## Current Capability

The Python bootstrap compiler, the core prelude, and the self-hosting compiler are all in place. The end-to-end pipeline is gallowglass source → `bootstrap.emit_pla` → Reaver runtime, gated in CI. The core prelude is 112 definitions (65 source-level lets plus instance methods) across 8 modules, with Eq/Ord/Show/Debug typeclasses and constrained instances. Pattern-match exhaustiveness, pin-based module loading (BLAKE3-256), Glass IR emission (round-trip verified), and type-annotated Glass IR are all live. Test count is live — run `python3 -m pytest tests/ -q` for the current passing/skipped totals rather than trusting an inline number.

The self-hosting compiler is validated via the BPLAN harness: GLS `emit_program` processes the full `Compiler.gls` module and produces correct Plan Assembler output. RPLAN self-host validation on Reaver (gallowglass compiling itself under Reaver, byte-identical to the BPLAN harness) is scoped as forward work — see `ROADMAP.md` and `DECISIONS.md §"Why Phase G is a separate arc"` for rationale.

The bootstrap compiler compiles the **restricted dialect** of Gallowglass only.
See `bootstrap/BOOTSTRAP.md` for what the restricted dialect permits.

## Build and Test

```bash
# Install dependencies (blake3 is required for spec-compliant PinIds;
# without it pin.py warns and falls back to SHA-256 — see A3 in AUDIT.md).
pip install -r requirements.txt

# Compile a Gallowglass source file to legacy seed bytes (test-only path).
# The source file should contain bare top-level declarations (let, type, ...)
# — no enclosing `mod ModName { ... }` header. The module name 'Module' is
# supplied by the harness, not parsed from the source.
python3 -c "
import sys
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_seed import emit
path = sys.argv[1]
src = open(path).read()
prog = parse(lex(src, path), path)
resolved, _ = resolve(prog, 'Module', {}, path)
compiled = compile_program(resolved, 'Module')
sys.stdout.buffer.write(emit(compiled, 'Module.main'))
" input.gls > output.seed

# Compile to Plan Assembler text (the production Reaver path); see
# tests/reaver/test_smoke.py for end-to-end usage with `bootstrap.emit_pla`.

# Run tests
python3 -m pytest tests/bootstrap/  # bootstrap compiler tests
python3 -m pytest tests/compiler/   # self-hosting compiler tests
python3 -m pytest tests/prelude/    # prelude tests
python3 -m pytest tests/reaver/     # Reaver runtime gate (skipped if absent)
python3 -m pytest tests/            # all tests
```

### Test skip categories

Skips are all expected and fall into a small number of categories:

- **planvm archived (~110):** Every test gated on `requires_planvm` is now
  unconditionally skipped. xocore-tech/PLAN's xplan VM is no longer a
  Gallowglass deployment target — see `DECISIONS.md §"Why XPLAN compatibility
  is being abandoned"`. The decorator and infrastructure are preserved so
  historical imports still resolve. AUDIT.md C2 tracks collapsing the shim.
- **Reaver runtime absent:** `tests/reaver/` skips when `nix`/`cabal` and the
  Reaver binary aren't available. CI runs them; local runs typically don't.
- **Deep recursion (4):** Stress tests (`TestDeepRecursion` in `test_coverage_gaps.py`)
  that hit the Python evaluator's recursion limit. These will work on the
  PLAN VM; fixing in the Python harness requires native jets (post-1.0).
  AUDIT.md B1 tracks the related "depth guard returns wrong value" bug.

## Key Invariants to Never Violate

- Glass IR round-trips: a Glass IR fragment must reparse to the same PLAN output.
- Abort never appears in an effect row.
- External effects must be in the row of any function crossing the VM boundary.
- Canonical SCC ordering is lexicographic by name — any deviation changes PinIds.
- BLAKE3-256 is the spec-required hash algorithm. The bootstrap soft-falls
  back to SHA-256 with a warning if the `blake3` pip package is missing —
  PinIds in that mode are not spec-compliant. Install via
  `pip install -r requirements.txt`. AUDIT.md A3 tracks tightening this.
- `Show` and `Debug` are distinct typeclasses. Never conflate them.
- Contracts must be statable from the mathematical specification alone.
- Pin content is reduced to WHNF + law spine — **not** to full normal form. Do not assume or assert full normalization of pin contents.
- Every user-facing diagnostic (`ParseError`, `ScopeError`, `TypecheckError`, `CodegenError`) carries a `Loc` and prints `file:line:col: error: <msg>`. New error sites in user-reachable paths must plumb a `Loc` through. Bare-message errors are reserved for compiler-internal invariants the user can't trigger.

## Bootstrap Codegen Pitfalls (read before touching `bootstrap/codegen.py`)

The bootstrap codegen has had several recurring classes of bugs around
constructor pattern matching. All are now fixed and pinned with regression
tests in `tests/bootstrap/test_codegen.py` and `test_coverage_gaps.py`. Read
the fix log in `DECISIONS.md §"Bootstrap Compiler"` before writing new
constructor match patterns — the same shapes keep surfacing edge cases.

**Wildcard arm drop (`_compile_single_arm_field_bind`).** When a constructor
match has exactly one non-wildcard arm and a wildcard, `_compile_con_match`
routes to `_compile_single_arm_field_bind`. The wildcard arm *must* be passed
through to `_compile_adt_dispatch`; if it is not, all constructors (being
PLAN Apps) match the single arm and the wildcard body is silently unreachable.
Pattern: `| Con x → body | _ → default`. Symptom: `f(OtherConstructor)`
returns the same result as `f(Con ...)`. Fix: pass `wild_arm` explicitly.
This first surfaced for `planval_is_nat`, `planval_is_app`, etc.

**Mixed-arity binary path (`_build_field_arm_law`).** When a type has both
unary (arity=1) and binary (arity=2) field-bearing constructors, the binary
path is active (max_arity=2). Unary constructors encode as `A(Nat(tag), field)`
— their `outer_fun` is a bare Nat. The inner Case_ Nat dispatch (`z`/`m`) fires
for them, *not* the App handler. The unary tag=0 case uses the unary arm body
as `z_body`; the unary tag>0 case uses a lambda-lifted `m_body` sub-law. Both
cases are now implemented and tested (`test_match_mixed_arity_*`).

**`first_tag > 0` in `_build_tag_chain`.** When the
field-bearing constructors all have tag > 0 (e.g. `type Tree = | Leaf | Node X
| Branch X Y` where Leaf is nullary tag=0), the inner tag dispatch's
multi-arm branch previously ignored `first_tag` and used `tag_val_pairs[0][1]`
as the `zero_val` of an op2 dispatch. The single-arm branch handled this
correctly; the multi-arm branch did not. Symptom: `Branch a b` arms returned
`<0>` (P(0)). Fix: when `first_tag > 0`, set `z_val = wild`, shift all tags
down by 1, and recurse. (F11 from the field-feedback follow-ups.)

**Outer locals dropped in mixed nullary/field dispatch
(`_compile_adt_dispatch`).** When a type has ≥2 explicitly-named nullary
constructors *and* ≥1 field-bearing constructor, the secondary nullary arms
(tags > 0) used to be compiled with `pred_env = Env(globals=env.globals,
arity=1)` — discarding `env.locals` and any `self_ref_name`. Arm bodies that
referenced an outer-lambda parameter raised `CodegenError: unbound
variable`. Pattern: `type XY = | X | Y | Extra Nat; λ t v → match t { | X →
0 | Y → v | Extra n → n }`. Fix: mirror `make_succ_law`'s capture pattern —
collect free vars across `remaining_nullary` bodies plus `wild_body`, build
a lifted law of arity `n_cap + 1`, partial-apply at the outer env's
perspective. (AUDIT.md A1; regression tests `test_a1_*` in
`tests/bootstrap/test_codegen.py`.)

The **prelude types** (Option, Result, List) only use 2-constructor matches.
The above bugs surface in user-defined types with three or more constructors
in mixed-arity combinations — write tests when you add such a type.

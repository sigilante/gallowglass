# Gallowglass

Gallowglass is a programming language designed for LLMs to write and reason about, targeting the PLAN virtual machine (xocore-tech/PLAN). This repo contains the language specification, Python bootstrap compiler, core prelude (Gallowglass), and self-hosting compiler (Gallowglass). **Alpha milestone: self-hosting validation (M8.8) complete.**

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
  ROADMAP.md             ← delivery plan: milestones M9–1.0 and post-1.0

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
    BOOTSTRAP.md         ← Bootstrap compiler overview and milestones
    *.py                 ← Python bootstrap compiler (lexer, parser, scope, codegen, emit)
    archive/             ← Archived Sire stubs (superseded, reference only)

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
- `Abort` is NOT in any effect row. It is unhandleable, propagates to the VM's virtualization supervisor.
- `External` marks VM boundary crossings.
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

The bootstrap codegen and Python harness are mid-migration to this ABI as of
the Reaver migration arc; legacy xocore-tech/PLAN 5-opcode output (Pin/MkLaw/
Inc/Case_/Force at 0–4) is the current interim. See `DECISIONS.md §"Why
Reaver's Haskell sources are the canonical base truth"` and the migration
status under `## Current Phase` below.

All Gallowglass types are erased at compile time. The PLAN output is untyped. Type errors are purely a Gallowglass-layer concern.

## Current Phase

**Alpha.** All Milestone 8 phases complete. M9–M20 complete. 112 prelude definitions (65 source-level lets + instance methods) across 8 modules. Eq/Ord/Show/Debug typeclasses with constrained instances. 1210 tests passing.

- Phase 0 (spec): complete.
- Phase 1 (Python bootstrap compiler): complete. Milestones 1–7.5 done. Core prelude: 112 definitions (65 lets + instance methods), planvm-valid.
- Phase 3 (self-hosting compiler, M8): complete through M8.8 Path B.
  - M8.1 utilities, M8.2 lexer, M8.3 parser, M8.4 scope resolver, M8.5 codegen, M8.6 emitter, M8.7 driver: all done.
  - M8.8 self-hosting validation: Path B (harness) complete — GLS `emit_program` processes the full Compiler.gls module and produces correct Plan Assembler output. Path A (VM-executed) deferred pending upstream side-effects + virtualization API stabilization (see `IO.md`).
- M9: fix expressions, tuples, mutual recursion (SCC), type checker SCCs — all complete.
- M10: CPS effect handlers, pure builtin, do-notation, tag namespacing, integration test battery, GLS EFix — all complete.
- M11: Typeclasses (DeclClass, DeclInst, constrained lets, dictionary insertion) — all complete.
- M12: Module system (use imports, build driver, cross-module instances) — all complete.
- M12.2: GLS compiler DEff/EHandle/EDo support — complete.
- M12.3: Superclass constraint flat expansion — complete.
- M12.4: GLS compiler DeclUse support — complete.
- M12.5: Data.Csv end-to-end integration tests — complete.
- M13.1: Default methods — complete.
- M13.2: Compound type instances, constrained instances — complete.
- M13.3: Shallow handlers (once) via open-continuation CPS protocol — complete.
- M13.4: GLS compiler parity for M13.1–M13.3 (open-continuation CPS, forward_k, virtual resume substitution) — complete.
- M14.1: Complete Eq (neq default) and Ord (gt, gte, min, max defaults), move arithmetic to Core.Nat — complete.
- M14.2: Pair and Result types — complete.
- M14.3: Collection instances (Eq + Show for Option, List, Result) — complete.
- M14.4: pipe and fixpoint combinators — complete.
- M14.5: Debug class with instances for Nat, Bool, Option, List — complete.
- M15.2: Type aliases — complete (no codegen needed, types fully erased).
- M15.3: List/Cons expressions and patterns — complete (scope-level desugaring to constructor forms).
- M15.4: Or patterns — complete (scope-level arm duplication).
- M15.5: Guards in match arms — complete (scope-level desugaring to if-else + re-match).
- M15.6: String interpolation — complete (parser-level desugaring to text_concat/show chain).
- M15.1: Records — complete (scope-level desugaring: DeclRecord→DeclType, ExprRecord→constructor apps, ExprRecordUpdate→match+rebuild, PatRecord→PatCon).
- M15.7: GLS compiler parity — complete (7a–7f: type aliases, list/cons syntax, or-patterns, guards, string interpolation, records).
- M14.6: Cross-module prelude refactor — complete (all 8 modules use `use` imports, full-prelude integration test).
- M16: Pin-based module loading — complete (PinId via BLAKE3-256, pin-wrapped compilation, prelude as 110-pin DAG, pin store, full-cycle integration tests).
- M17: Glass IR emission — complete (AST-based renderer, FQ names, pin hashes, SCC groups, dependency rendering, round-trip verification, prelude Glass IR emission).
- M18: Type-annotated Glass IR — complete (standalone type serializer pp_type/pp_scheme, constraint preservation in Scheme, TypeEnv wired into Glass IR renderer, cross-module typechecking via prior_type_env, all 8 prelude modules typecheck).
- M19: Pattern match exhaustiveness checking — complete (Maranget usefulness algorithm, constructor registry, nested/tuple/literal pattern support, redundancy warnings, integrated at typecheck time).
- M20: 0.999 syntax — complete (where clauses as parser desugaring, operator sections with backtracking detection, export list enforcement in scope resolver).

The bootstrap compiler compiles the **restricted dialect** of Gallowglass only.
See `bootstrap/BOOTSTRAP.md` for what the restricted dialect permits.

## Build and Test

```bash
# Run the xocore PLAN reference VM
# (requires xocore-tech/PLAN installed)
planvm <seed-file>

# Run the Python bootstrap compiler directly
python3 -c "
from bootstrap.lexer import lex; from bootstrap.parser import parse
from bootstrap.scope import resolve; from bootstrap.codegen import compile_program
from bootstrap.emit import emit
import sys
src = open(sys.argv[1]).read()
from bootstrap.parser import parse
prog = parse(lex(src, sys.argv[1]), sys.argv[1])
resolved, _ = resolve(prog, 'Module', {}, sys.argv[1])
compiled = compile_program(resolved, 'Module')
sys.stdout.buffer.write(emit(compiled, 'Module.main'))
" input.gls > output.seed

# Run tests
python3 -m pytest tests/bootstrap/  # bootstrap compiler tests
python3 -m pytest tests/compiler/   # self-hosting compiler tests
python3 -m pytest tests/prelude/    # prelude tests (some require planvm)
python3 -m pytest tests/            # all tests
```

### Test skip categories

1210 passing, 145 skipped. The skips are all expected:

- **planvm-gated (75):** Seed loading and VM execution tests that require the
  `planvm` binary. These run in the `plan-vm` CI job (builds planvm via Nix).
  Covers prelude seeds (56), compiler seeds/eval (12), seed format (7).
- **Deep recursion (4):** Stress tests (`TestDeepRecursion` in `test_coverage_gaps.py`)
  that hit the Python evaluator's recursion limit. These will work on the actual
  PLAN VM; fixing in the Python harness requires jets (post-1.0).
- **Driver smoke (1):** `test_main_minimal_snippet` requires planvm.

## Key Invariants to Never Violate

- Glass IR round-trips: a Glass IR fragment must reparse to the same PLAN output.
- Abort never appears in an effect row.
- External effects must be in the row of any function crossing the VM boundary.
- Canonical SCC ordering is lexicographic by name — any deviation changes PinIds.
- BLAKE3-256 is the hash algorithm everywhere. No exceptions.
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

**Wildcard arm drop (`_compile_con_body_extraction`).** When a constructor
match has exactly one non-wildcard arm and a wildcard, `_compile_con_match`
routes to `_compile_con_body_extraction`. The wildcard arm *must* be passed
through to `_compile_con_match_case3`; if it is not, all constructors (being
PLAN Apps) match the single arm and the wildcard body is silently unreachable.
Pattern: `| Con x → body | _ → default`. Symptom: `f(OtherConstructor)`
returns the same result as `f(Con ...)`. Fix: pass `wild_arm` explicitly.
This bit us during M8.6 for `planval_is_nat`, `planval_is_app`, etc.

**Mixed-arity binary path (`_build_app_handler`).** When a type has both
unary (arity=1) and binary (arity=2) field-bearing constructors, the binary
path is active (max_arity=2). Unary constructors encode as `A(Nat(tag), field)`
— their `outer_fun` is a bare Nat. The inner Case_ Nat dispatch (`z`/`m`) fires
for them, *not* the App handler. The unary tag=0 case uses the unary arm body
as `z_body`; the unary tag>0 case uses a lambda-lifted `m_body` sub-law. Both
cases are now implemented and tested (`test_match_mixed_arity_*`).

**`first_tag > 0` in `_build_precompiled_nat_dispatch`.** When the
field-bearing constructors all have tag > 0 (e.g. `type Tree = | Leaf | Node X
| Branch X Y` where Leaf is nullary tag=0), the inner tag dispatch's
multi-arm branch previously ignored `first_tag` and used `tag_val_pairs[0][1]`
as the `zero_val` of an op2 dispatch. The single-arm branch handled this
correctly; the multi-arm branch did not. Symptom: `Branch a b` arms returned
`<0>` (P(0)). Fix: when `first_tag > 0`, set `z_val = wild`, shift all tags
down by 1, and recurse. (F11 from the field-feedback follow-ups.)

The **prelude types** (Option, Result, List) only use 2-constructor matches.
The above bugs surface in user-defined types with three or more constructors
in mixed-arity combinations — write tests when you add such a type.

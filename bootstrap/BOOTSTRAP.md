# Bootstrap Compiler

**Language:** Python 3.11+
**Target:** PLAN seed files (`*.seed`)
**Input:** Restricted Gallowglass dialect

This document describes the scope, restricted dialect, and milestone status of the
Gallowglass bootstrap compiler.

The bootstrap compiler is the Phase 1 deliverable. Its sole purpose is to compile
enough Gallowglass to write the core prelude (Phase 2) and, eventually, the
self-hosting compiler (Phase 3). It is thrown away after Phase 3.

---

## 1. Implementation

The bootstrap compiler is written in Python and lives in `bootstrap/*.py`. It
compiles the restricted Gallowglass dialect directly to PLAN values, then serializes
those values to the PLAN seed format understood by `x/plan` (xocore-tech/PLAN).

```
bootstrap/
  lexer.py       ← Bytes → token list
  parser.py      ← Token list → AST
  scope.py       ← Scope resolution: qualified names, module namespacing
  typecheck.py   ← Restricted HM unification
  codegen.py     ← AST → PLAN values (de Bruijn, constructors, pattern match)
  emit.py        ← PLAN value → seed bytes (spec/07-seed-format.md)
  glass_ir.py    ← Debug renderer: PLAN values → Glass IR text
  ast.py         ← AST node definitions
```

### 1.1 Why Python

The original design called for a bootstrap compiler in Sire (PLAN's macro/assembly
language). Python was used instead because:

- The Python compiler was built first as a prototype, and is fully functional.
- It produces valid seed bytes loadable by `x/plan`.
- It serves as the cross-compiler for Phase 2/3: compile Gallowglass in Gallowglass,
  emit the seed, run natively on `x/plan`.
- Sire is harder to write and the bootstrap is thrown away anyway.

The archived Sire outlines are in `bootstrap/archive/sire/` for reference only.

### 1.2 The Bootstrap Path

The Python compiler is a **cross-compiler**: it runs on the developer's machine and
produces seed files that run on the PLAN VM. The self-hosting path is:

```
1. Python compiler compiles restricted Gallowglass → seed bytes
2. Validate seeds run correctly on x/plan (Milestone 6)
3. Write the self-hosting Gallowglass compiler in restricted Gallowglass
4. Python compiler compiles it → compiler.seed
5. x/plan runs compiler.seed — native Gallowglass compiler on PLAN
6. compiler.seed compiles itself → true self-hosting
```

No Sire step required.

---

## 2. Restricted Dialect

The bootstrap compiler implements a strict subset of the full Gallowglass surface
syntax (spec/06-surface-syntax.md).

### 2.1 Included

| Feature | Notes |
|---|---|
| `let` bindings | Top-level and local, non-recursive and recursive |
| `λ` expressions | Anonymous functions, multi-argument |
| Function application | Left-associative, juxtaposition |
| `type` declarations | Sum types (`\|` constructors) only; no record syntax |
| `match` expressions | Nat and nullary constructor patterns |
| `if`/`then`/`else` | Bool dispatch via opcode 2 |
| Nat, Bool, Text, Bytes literals | Primitive literals |
| `pin` expression | Programmer-controlled pinning |
| Basic type annotations | Monomorphic and simple polymorphic (`∀ a.`) |
| `external mod` declarations | VM boundary for Core.* primitives |

### 2.2 Excluded (deferred to self-hosting compiler)

| Feature | Reason |
|---|---|
| `handle` expressions | Effect handlers require CPS transformation |
| Effect rows in types | Parsed, never unified; checked post-Phase-3 |
| Contracts (`\| pre`, `\| post`) | No solver in bootstrap |
| `module` / `import` | Multi-file compilation deferred |
| Typeclasses | Deferred |
| Mutual recursion | Deferred |
| Tuples | Deferred |
| `let rec` / `fix` | Deferred |
| Row-polymorphic records | Deferred |

### 2.3 Effect Handling

The bootstrap type checker ignores effect rows. Functions may include effect
annotations (the parser accepts them), but the type checker treats all types as if
the annotation were absent. This is sound for Phase 1.

---

## 3. Pipeline

```
Source text
    │
    ▼  bootstrap/lexer.py
Token list
    │
    ▼  bootstrap/parser.py
AST (bootstrap/ast.py)
    │
    ▼  bootstrap/scope.py
Qualified AST
    │
    ▼  bootstrap/typecheck.py   (restricted HM, effects ignored)
Typed AST
    │
    ▼  bootstrap/codegen.py     (de Bruijn, constructors, opcode dispatch)
PLAN values (dict[fq_name → PLAN value])
    │
    ▼  bootstrap/emit.py        (spec/07-seed-format.md)
Seed bytes
    │
    ▼  x/plan seed_file         (xocore-tech/PLAN VM)
Result
```

The Python dev harness (`dev/harness/`) provides a pure-Python PLAN evaluator for
unit testing without requiring `x/plan`. Seeds must also validate against `x/plan`
directly (see Milestone 6).

---

## 4. Milestones

### ✅ Milestone 1: Lexer
Tokenizes all restricted dialect source. Tests: `tests/bootstrap/test_lexer.py`.

### ✅ Milestone 2: Parser
Produces AST for all restricted dialect constructs. Tests: `tests/bootstrap/test_parser.py`.

### ✅ Milestone 3: Scope resolver
Qualified names, module namespacing. Tests: `tests/bootstrap/test_scope.py`.

### ✅ Milestone 4: Type checker
Restricted HM unification. Tests: `tests/bootstrap/test_typecheck.py`.

### ✅ Milestone 5: Codegen + Emit + Glass IR
Compiles Gallowglass → valid PLAN seeds. 44 tests pass in Python harness.
Tests: `tests/bootstrap/test_codegen.py`.

### ✅ Milestone 6: planvm seed validation
Seeds produced by the Python compiler load and evaluate correctly under `x/plan`.
Tests: `tests/planvm/test_seed_planvm.py`. 7/7 pass.
CI: `make test-ci` (Docker). Local: `make test`.

### ✅ Milestone 7: Core prelude (initial)
Write `prelude/src/Core/` in the restricted Gallowglass dialect; compile and
validate each module with the Python compiler + `x/plan`.
Modules: `Core.Combinators` (5), `Core.Bool` (6), `Core.Nat` (3),
`Core.Option` (5), `Core.List` (5) — 24 definitions, all planvm-valid.
CI: `make test-prelude-docker`. Local: `make test-prelude`.
Bootstrap limitation noted: wildcard match arms cannot bind the predecessor,
so Nat arithmetic and field extraction from multi-constructor types were
deferred to Milestone 7.5.

### ✅ Milestone 7.5: Bootstrap compiler upgrade — predecessor binding
Three changes to `bootstrap/codegen.py`; prelude upgraded to full implementations.

**Changes made:**
1. **Self-recursion via N(0)**: `self_ref_name` field in `Env`; inside a law
   body, a function referencing its own FQ name compiles to `N(0)` (law self-ref).
2. **PatVar predecessor binding**: `_make_pred_succ_law` in `_build_nat_dispatch`
   lambda-lifts captured locals and binds the wild variable to the predecessor
   passed by Case_. Enables `| k → use_k` where `k` is the predecessor.
3. **Multi-constructor field extraction**: `_compile_con_match_case3` uses
   Case_ (opcode 3) App handler to extract fields. For unary `| Some x → f x`,
   the App branch receives `(fun=tag, arg=field)` and binds `x = arg`.
4. **Bool global quoting**: nat globals (`True=1`, `False=0`, nullary constructors)
   inside law bodies now use the quote form `A(N(0), N(k))` instead of being
   pinned, so they return bare nats that work correctly with Case_ dispatch.
5. **Core.PLAN opcode mapping**: `external mod Core.PLAN { inc : Nat → Nat }`
   compiles `Core.PLAN.inc` to `P(N(2))` (the real Inc opcode), enabling
   arithmetic functions (`add`, `mul`).

**Unblocked (now in prelude):**
- `Core.Nat`: `pred`, `add`, `mul` + corrected `nat_eq`, `nat_lt` (7 total)
- `Core.Option`: `map_option`, `bind_option` + proper `with_default` (7 total)
- `Core.List`: `head`, `tail`, `map`, `filter`, `foldl`, `foldr` (11 total)

Tests: `tests/bootstrap/test_codegen.py` (44 pass), `tests/prelude/` (24 planvm tests).

### ✅ Milestone 8: Self-hosting compiler — **ALPHA CANDIDATE**

Write the Gallowglass self-hosting compiler in the restricted dialect; compile it
with the Python compiler; validate self-hosting output.

Output format: **Plan Assembler** (textual, Reaver format), not binary seed.
See DECISIONS.md: "Why target Plan Assembler output instead of binary seed format?"
and `spec/07-seed-format.md` §13 for the grammar.

Sub-milestones:
- **M8.1 Utilities** ✅ — string/bytes ops, nat arithmetic helpers
- **M8.2 Lexer** ✅ — tokenises restricted Gallowglass source to token list
- **M8.3 Parser** ✅ — token list → `Decl` AST nodes
- **M8.4 Scope resolver** ✅ — qualifies all `EVar` references to FQ `Module.name` nats.
  Three bootstrap codegen bugs fixed to get here: (1) `let`-binding De Bruijn shift in
  lambda-lifted match arms, (2) broken `expr_tag` dispatch for ENat bypassed with
  structural `planval_is_nat`/`planval_is_app` predicates, (3) same shift in
  `sr_resolve_decls`. Tests: `tests/compiler/test_scope.py` — 15 pass.
- **M8.5 Codegen** ✅ — three-pass `compile_program`: DType/DExt/DLet → `PlanVal`
- **M8.6 Plan Assembler emitter** ✅ — `emit_program`: `List (Pair Nat PlanVal)` → `Bytes`
  Tests: `tests/compiler/test_emit.py` — 38 pass, 1 skipped (planvm-gated
  `TestSeedLoading`; all evaluation tests now active via BPLAN jets).
  Two bootstrap codegen bugs fixed: wildcard-arm drop in `_compile_con_body_extraction`
  and unary tag=0 z_body in the binary path of `_build_app_handler`. See DECISIONS.md.
- **M8.7 Driver** ✅ — `main : Bytes → Bytes` chains lex→parse→scope→codegen→emit.
  Module name hardcoded to "Compiler" (nn = 8243113893085146947).
  Tests: `tests/compiler/test_driver.py` — 3 pass, 3 skipped (planvm-gated).
- **M8.8 Self-hosting validation** ✅ (Path B) / pending (Path A)
  - Path B (harness): Python bootstrap → `plan2pv` bridge → GLS `emit_program` processes
    full Compiler.gls module (all definitions) and produces correct Plan Assembler output.
    Tests: `tests/compiler/test_selfhost.py` — 17 pass, 2 planvm-gated skipped.
  - Path A (planvm byte-identical): deferred pending cog wrapping (`main : Bytes → Bytes`
    must be wrapped as a planvm cog to read stdin and write stdout). This is the final
    alpha gate.

---

## 5. Invariants

- **Canonical SCC order is lexicographic.** Deviation silently changes PinIds.
- **No compile-time evaluation.** The compiler produces un-reduced PLAN terms.
  Exception: constant-folding of literal Nat arithmetic is permitted.
- **One module per file.** The bootstrap has no multi-module file support yet.
- **Error messages include source locations.** Every diagnostic includes `file:line:col`.

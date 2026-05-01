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
| `match` expressions | Nat, nullary constructor, unary/binary constructor, and tuple patterns |
| `if`/`then`/`else` | Bool dispatch via opcode 2 |
| Nat, Bool, Text, Bytes literals | Primitive literals |
| `pin` expression | Programmer-controlled pinning |
| Basic type annotations | Monomorphic and simple polymorphic (`∀ a.`) |
| `external mod` declarations | VM boundary for Core.* primitives |
| `fix` expressions | Self-referential anonymous recursion |
| Tuples | Binary tuples `(a, b)`; tuple patterns in match |
| Mutual recursion | Lexicographically-ordered SCC compilation via shared-pin rows |
| `eff` declarations | Effect ops compile to 3-arg CPS laws; `handle`/do supported |
| `handle` expressions | CPS dispatch: `comp dispatch_fn return_fn` |
| do-notation (`x ← rhs in body`) | CPS bind: lambda-lifts continuation over captured locals |
| `pure v` | No-op CPS computation: `λ dispatch k → k v`; terminates do chains |

### 2.2 Excluded (deferred to self-hosting compiler)

| Feature | Reason |
|---|---|
| Effect rows in types | Parsed, never unified; checked post-Phase-3 |
| Contracts (`\| pre`, `\| post`) | No solver in bootstrap |
| `module` / `import` | Multi-file compilation deferred |
| Typeclasses | Deferred |
| Row-polymorphic records | Deferred |

### 2.3 Effect Handling

The bootstrap type checker ignores effect rows. Functions may include effect
annotations (the parser accepts them), but the type checker treats all types as if
the annotation were absent. This is sound for Phase 1.

### 2.4 Restricted-dialect idioms

The bootstrap compiler accepts a strict subset of Gallowglass; the rules below
cover the patterns that work today and the workarounds for forms the bootstrap
codegen does not yet handle.

#### 2.4.1 Variant types: mixed-arity sum types are supported

The bootstrap codegen handles ADTs with constructors of mixed arities
directly. Three different arities in the same match work:

```gallowglass
type Tree =
  | Leaf                   -- arity 0
  | Node   Nat             -- arity 1
  | Branch Tree Tree       -- arity 2

let depth : Tree → Nat
  = λ tt → match tt {
      | Leaf       → 0
      | Node nn    → 1
      | Branch a b → 2
    }
```

This was historically a footgun — earlier bootstrap revisions silently
returned `<0>` (P(0)) for the binary arm under certain tag layouts.
F11 (post-feedback) fixed the last such case (`first_tag > 0` in the
inner tag dispatch); see the regression suite at
`tests/bootstrap/test_codegen.py::test_match_mixed_arity_*` and
`test_match_nullary_unary_binary_*`. The `demos/calculator.gls`
`eval` function exercises the same pattern end-to-end.

##### Older idiom: single-constructor record + Nat tag

`Compiler.gls` was written before mixed-arity matches were reliable, so it
encodes every AST node as a single-constructor record with a Nat tag and a
payload nested in `Pair`s, then dispatches with an `if (nat_eq tag K)`
chain:

```gallowglass
type Effect = | MkEffect Nat (Pair Nat (Pair Nat Nat))
--                       tag    a       (b   c)

let interpret : Effect → Bytes
  = λ ee → match ee {
      | MkEffect tag pp →
          if nat_eq tag 0 then handle_point pp
          else if nat_eq tag 1 then handle_dns (pair_fst pp)
          else handle_xfer (pair_fst pp)
    }
```

New code can use the direct sum-type form above. The tagged-record idiom
remains useful when (a) several variants share the same payload shape, or
(b) you want the tag to be statically computable and inspectable.

#### 2.4.2 Recursive Bool dispatch

For recursion controlled by a `Bool`, both `if-then-else` and `match` dispatch
work correctly: the bootstrap codegen wraps non-base branches in Pin'd thunk
laws so neither branch is forced before the condition is evaluated. Use whichever
reads more naturally:

```gallowglass
-- Either form is fine:
let mod_go : Nat → Nat → Nat
  = λ aa bb → if (lte bb aa) then mod_go (sub aa bb) bb else aa

let mod_go : Nat → Nat → Nat
  = λ aa bb → match (lte bb aa) {
      | False → aa
      | True  → mod_go (sub aa bb) bb
    }
```

(Earlier bootstrap revisions evaluated both `if-then-else` branches eagerly.
That is fixed; current revisions defer both branches.)

#### 2.4.3 Wildcard succ-arm captures

`match nn { | 0 → base | _ → body_using_outer_locals }` lambda-lifts outer
captures and the enclosing function's self-reference into the wildcard arm.
PatVar (`| _kk → ...`) and PatWild (`| _ → ...`) succ arms are equivalent for
the purpose of capture lifting; PatVar additionally binds the predecessor to
the named variable.

#### 2.4.4 Effect handlers that resume with constructor values

Handler op arms can pass any value to the resumed continuation, including
constructor applications. The continuation is just a PLAN value (`kk val` is
`App(kk, val)`), so resuming with a `Some x`, `MkPair a b`, or `Ok v` works
with no special ceremony. The downstream do-bind binds the constructor value
to its variable; pattern-matching on it happens wherever the bound variable
flows next — typically in the `return` arm of the same handler.

```gallowglass
type MyOpt = | MyNone | MySome Nat

eff Lookup {
  lookup : () → MyOpt
}

let comp = oo ← lookup () in pure oo

-- Handler resumes with `MySome 99`; the return arm pattern-matches it.
let extracted = handle comp {
  | return rr → match rr { | MyNone → 0 | MySome xx → xx }
  | lookup _ kk → kk (MySome 99)
}
-- extracted = 99
```

The same pattern works for any constructor — unary (`Some`, `Ok`), binary
(`MkPair`), or single-constructor records. See
`tests/bootstrap/test_coverage_gaps.py::TestEffectHandlerEdgeCases` for the
worked test cases (F6).

#### 2.4.5 Known sharp edges that still bite

These are not bugs in the codegen — they're operational quirks of the
Python harness or current restrictions of the demo build path. Each
field-tested complaint from `feedback_for_gallowglass.md` not already
covered in §§2.4.1–2.4.4 lives here.

##### Recursion-limit guidance

The Python harness evaluator (`dev/harness/plan.py`) uses recursive
`evaluate`/`apply` calls. Each PLAN-level reduction step costs one
Python frame, and laziness combinators inflate the cost: a list of
length N processed by `foldl`/`foldr`/`map`/`append` typically costs
~10–20 Python frames per cell.

Concrete bumps observed in practice (these numbers are calibrated
against the demos in `demos/` and are reasonable upper bounds):

| Workload                                         | `sys.setrecursionlimit` |
|--------------------------------------------------|------------------------:|
| Simple arithmetic, ≤100 list cells               | default (1 000)         |
| Recursive numeric (factorial, Fibonacci ≤30)     | 10 000                  |
| `Compiler.gls`-class computations (~500 defs)    | 100 000                 |
| Multi-fold over large nested lists (e.g. 3-deep) | 200 000                 |

The harness evaluator additionally raises `RecursionError` past
`EVALUATE_DEPTH_LIMIT` (`dev/harness/plan.py`, currently 10 000) and
`bevaluate` past `BEVALUATE_DEPTH_LIMIT` (100 000). Both are PLAN-level
counts and trip well after Python's own limit fires for typical
programs — but if your program is deep-but-narrow you may hit them
first. The fix in either case is the same: bump
`sys.setrecursionlimit` *and* the relevant depth limit, or rewrite
the algorithm to be tail-iterative.

The proper post-1.0 fix is jetting the bread-and-butter combinators
(`length`, `map`, `foldl`, `foldr`, `append`, `concat_list`) so they
collapse to a single native step in `dev/harness/bplan.py` instead of
a per-cell PLAN reduction. Several of those are already jetted; check
`bplan.py`'s `_PRELUDE_JETS` registry to see the current set.

##### Demos and `use` imports

`demos/*.gls` files compile in isolation: the test harness invokes
the bootstrap with `module_env={}`, so even though the prelude
modules compile, they aren't in scope. Each demo redefines
`add`/`mul`/`map`/`foldl`/etc. inline at the top.

This is a fixable shortcoming of the demo harness (M12 module support
is in place; the demo runner just doesn't thread a prebuilt
`module_env` through). For now: copy the utilities you need from
`Compiler.gls`'s prelude inlining at lines 25–205, or from the
relevant `Core/*.gls` source. Keeping demo source self-contained also
makes each demo readable as a single file without cross-references.

#### 2.4.6 Reading existing code

`Compiler.gls` (the self-hosting compiler in `compiler/src/`) is ~3000 lines
of restricted-dialect Gallowglass and is the canonical reference for "how do
I write \<X\>?" questions. It exercises every pattern in this section: the
tagged-record idiom for variants, recursive Bool dispatch for the lexer's
state machine, wildcard succ-arm captures throughout, effect handlers for
the parser's error-reporting style, mutual recursion via shared-pin SCCs,
and typeclass dictionary passing.

Lines 25–205 cover the tagged-record encoding for AST nodes — start there
for variant-type questions. The full prelude inlining at the top of the
file also serves as a worked reference for the dialect-safe idioms.

For shorter examples, the working demos in `demos/` (and `demos/README.md`)
are calibrated to teach one concept at a time.

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

### ✅ Milestone 9.1–9.3: Restricted dialect extensions

Three codegen additions enabling the prelude and self-hosting compiler to use richer idioms.

**M9.1 — `fix` expressions:**
`fix (λ self args → body)` compiles correctly: `params[0]` becomes `self_ref_name`
(maps to N(0) law self-reference), `params[1:]` are user params, law arity =
`len(user_params)`. `_collect_all_names` and `_collect_free` updated to traverse ExprFix.

**M9.2 — Tuples:**
Binary tuple construction `(a, b)` encodes as `A(tag_0, a, b)` using the quote form
`A(N(0), N(0))` for tag 0 inside law bodies (not Pin). Tuple match `(x, y)` dispatches
via `_compile_con_match_case3` with synthetic ConInfo(tag=0, arity=2).

**M9.3 — Mutual recursion (SCC compilation):**
Tarjan's SCC detection added to `compile()` pass 3. Single-element SCCs compile as before.
Multi-element SCCs use the shared-pin row encoding from spec/02-mutual-recursion.md:
selector law `L(n+1, 0, dispatch_body)` applied to n lambda-lifted member laws forms
a Pin row; `(shared_pin i) shared_pin` extracts and fully applies member i. Each member
law gets an extra first param `__shared__` (index 1). Wrapper laws for external callers
embed the shared pin as a non-bapp App literal in the law body.
Canonical SCC ordering: lexicographic by FQ name (consistent with PinId stability).

Tests: `tests/bootstrap/test_codegen.py` (53 pass — 9 new tests for fix, tuples, mutual recursion).

### ✅ Milestone 9.4: Type checker extensions

Three additions to `bootstrap/typecheck.py`:

**ExprFix:** was incorrectly returning the lambda type. Now: fresh `t`, unify lambda
type with `TArr(t, t)`, return `t`. Correct for `fix λ self args → body : T` where
the lambda has type `T → T`.

**SCC-ordered checking:** `_check_decls` uses `_build_dep_graph` + `_tarjan_scc` to
process `DeclLet` groups in topological order. Multi-element SCCs use `_check_mutual_scc`:
instantiate all provisional types, check all bodies, then generalize unannotated members
together — preventing premature generalization from disconnecting mutual unification variables.

**`_collect_expr_refs`:** full ExprVar walker over all expression forms, used for
building the dependency graph.

Tests: `tests/bootstrap/test_typecheck.py` (79 pass — 8 new tests covering fix inference,
annotated fix, self-ref unification, fix type errors, mutual recursion annotated/unannotated,
forward references, and mutual type error propagation).

### ✅ Milestone 10.1: Effect row types in type checker

See M10.1 entry in ROADMAP.md.

### ✅ Milestone 10.2: Effect handler codegen (CPS transform)

CPS compilation of `eff` declarations, `handle` expressions, and do-notation (`x ← rhs in body`)
added to `bootstrap/codegen.py` and `bootstrap/scope.py`.

**Changes made:**
1. **`scope.py` effect op binding**: `_collect_decl` for `DeclEff` now registers each op as a
   `BindingValue` so callers can reference it by name (e.g. `inc` resolves to `Module.Eff.inc`).
2. **`_register_eff`**: Each op compiles to a 3-arg law `L(3, name, dispatch(tag, op_arg, k))` —
   calling `E.op arg` produces a 2-arg partial application that is the CPS computation value.
3. **`_compile_handle`**: Assembles `A(A(comp_val, dispatch_fn), return_fn)` (top level) or
   `bapp(bapp(...))` (law body). Dispatch fn and return fn are lambda-lifted from outer locals.
4. **`_compile_dispatch_fn`**: Builds `L(n_cap+3, name, nat_dispatch_body)` — N(1)=op_tag,
   N(2)=op_arg, N(3)=resume; dispatches on op_tag via `_build_precompiled_nat_dispatch`.
5. **`_compile_return_fn`**: Builds `L(n_cap+1, name, body)` — N(last)=return value.
6. **`_compile_do`**: CPS bind `x ← rhs in body` compiles to an `(n_cap+2)`-arg law; the inner
   continuation `λ x → body_comp dispatch k_outer` is lambda-lifted as an `(n_cap+3)`-arg law.

Tests: `tests/bootstrap/test_codegen.py` (63 pass — 10 new tests for eff op compilation,
single-op handle, two-op dispatch, do-notation sequencing, and outer local capture).

### ✅ Milestone 10.3: `pure` for do-notation

`pure v` registered as a builtin CPS law `L(3, "pure", bapp(N(3), N(1)))` in codegen and as
a `BindingValue` in the scope resolver. `pure v` compiles to `A(pure_law, v_compiled)` — a
2-arg partial application (CPS computation) that, when applied to any dispatch_fn and k,
simply calls `k v`. Enables do chains to terminate with a pure computed value.

Tests: `tests/bootstrap/test_codegen.py` — 3 new tests (pure standalone, pure terminating
a do chain, pure with return arm transform). 4 new tests including M10.4 state-threading
validation below. 67 tests total.

### ✅ Milestone 10.5: Per-effect tag namespacing

`_resolve_handler_arm` in `scope.py` now resolves each `HandlerOp.op_name` to its FQ form
(e.g. `"inc"` → `"Test.Counter.inc"`) before storing it in the AST. The codegen's
`_lookup_op_tag` then does a direct FQ lookup in `effect_op_tags`, which is keyed on FQ names
from `_register_eff`. Two effects with the same short op name are caught as an ambiguous
reference at scope resolution, not silently mis-tagged.

Tests: 1 new test (two effects with distinct op names each handled correctly). 462 bootstrap
tests pass.

### ✅ Milestone 10.4: State-threading handler validation

Multi-op do chain (`ss ← get_st () in pp ← put_st ss in pure ss`) with two-op effect
dispatches correctly through nested lambda-lifted continuation laws. Confirms that
captured variables (`ss`) survive lambda lifting across nested do-binds. 461 bootstrap
tests pass.

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
  This applies to all four error classes — `ParseError`, `ScopeError`,
  `TypecheckError`, `CodegenError` — and to any new error class added
  later. When introducing a new `raise SomeError(...)` site, plumb a
  `Loc` through unless the error truly arises from a compiler-internal
  invariant (in which case the bare-message form is acceptable). The
  field-feedback report flagged the absence of source locations on
  codegen errors as the single biggest UX gap; F2 closed that gap and
  the property must be preserved as new diagnostics get added.

# Phase H — Session handoff (2026-05-13)

**Status:** in progress, multi-session.
**Roadmap reference:** `ROADMAP.md § "Phase H — Compile-self fixed point"`.

## Where we are

Phase H deliverables (1) and (2) from the ROADMAP are landed; the
self-host pipeline is now byte-identical to the bootstrap for a
growing set of fixtures.

### Commits this session (head → ROADMAP base)

```
d077ec2 fix(self-host): sr_dispatch — switch outer match to nat_eq on expr_tag
aa6af56 fix(self-host): sr_dispatch reaches EVar — restructure outer match
cadcaa0 test(selfhost): pin nullary multi-arm constructor match fixture
05bdc64 fix(self-host): multi-field ADT match — `<hint>_inner` law name
b268e1f fix(self-host): ADT match — `<hint>_app` name + quote-wrapped 0 fallback
e5d9b40 fix(self-host): external-decl parser — fix inverted EOF arms + skip logic
7ab0dcf fix(self-host): byte-identical multi-arm nat match — idx threading
b27061b fix(self-host): four match-on-nat fixes — pinned by two new fixtures
01585cf test(selfhost): add if-expression byte-identity fixture
457e5a5 fix(self-host): byte-identical if-expressions — quote-wrap + lifted-law name
9970050 docs(roadmap): mark Phase H #1 and #2 complete
3f0766c feat(self-host): Phase H #2 — cross-binding symbol dedup via PNamed variant
65efbc4 fix(self-host): Phase H #1 — BPLAN-66 wrap + trampoline + branch thunking
9346f66 docs(roadmap): add Phase H — compile-self fixed point work plan  (the prior session's base)
```

### Test state

- `tests/bootstrap/` — 832 passed, 4 skipped, 2 xfailed (pre-existing).
- `tests/compiler/` — 227 passed, 3 skipped.
- `tests/prelude/` — 167 passed.
- `tests/reaver/` — 46 passed, 1 xfailed. Includes 14 selfhost
  fixtures (12 byte-identical + 2 smoke).

### Byte-identity fixtures (`tests/reaver/test_selfhost.py`)

The 12 G3 byte-identical fixtures, in order of when they landed:

1. `test_single_nat_binding` — `let xx = 42`
2. `test_identity_function` — `let id = fn x -> x`
3. `test_type_annotation` — `let zz : Nat = 1`
4. `test_multi_decl_source_order`
5. `test_nested_lambda` — `let kk = fn x -> fn y -> x`
6. `test_application_in_body` — `let ap = fn ff -> fn xx -> ff xx`
7. **`test_if_expression`** — `let mm = if 1 then 5 else 10`  *(new)*
8. **`test_match_top_level_wildcard`** — `let mm = match 0 { | _ → 9 }`
9. **`test_match_nat_in_function_body`** — `let pick = λ x → match x { | 0 → 100 | _ → 200 }`
10. **`test_match_multi_arm`** — three named arms + wildcard
11. **`test_external_mod_decl`** — `external mod X { sub : Nat }`
12. **`test_match_adt_single_field`** — `Maybe a = None | Some a; unwrap (Some 42)`
13. **`test_match_adt_nullary_multi_arm`** — `Color = Red | Green | Blue`
14. **`test_match_adt_multi_field`** — `IntList = INil | ICons Nat IntList`

Plus `test_same_constructor_literal_field_collapses` (xfail, pre-existing).

## What works end-to-end

The self-host pipeline (`compiler/src/Compiler.gls`) now produces
byte-identical Plan Asm output to the bootstrap (`bootstrap/*.py`) for:

- Top-level let bindings of Nat literals, lambdas, type annotations.
- Multi-decl programs.
- Nested lambdas; lambda application in a body.
- `if`/`then`/`else` (with BPLAN-66 + thunked branches + trampoline).
- Match-on-nat at top level and in function bodies, wildcard and
  multi-arm cases.
- External-mod declarations.
- ADT match: nullary multi-arm, single-field, binary-field.

## What does NOT work yet

### Task #10 — Lifted-law outer-local capture in recursive / nested contexts

The hot remaining issue.  Demonstrated by:

- `/tmp/recursive.gls` — `let count_down = λ n → match n { | 0 → 0 | _ → count_down (sub n 1) }`.
  Diverges by 1 byte; lifted wild_succ law arity 2 vs reference's 3
  (missing self-capture).
- `/tmp/cross_ref.gls` — same shape but cross-binding ref instead of
  self-recursion.  Larger diff; same root cause.
- `/tmp/adt_multi_arm.gls` — `Shape = Empty | Circle Nat | Square Nat`.
  Multi-arm field-bearing constructors.  Lifted dispatch law arity
  mismatch.

#### What investigation has shown

1. `sr_dispatch` IS called and CAN rewrite EVar — verified via
   `EVar 88888` sentinel that makes all bodies become PNat 0.

2. `sr_dispatch`'s EVar arm fires for SHALLOW recursion (one level
   from top): `Compiler_main = (77777 5)` confirmed when probe
   replaced EVar arm with `ENat 77777`.

3. `sr_dispatch`'s EVar arm does NOT fire for DEEP recursion through
   `sr_rewrite_arms → sr_rewrite_arm → pe → sr_rewrite_expr → sr_dispatch`.
   The deep `count_down (sub n 1)` body keeps bare names.

4. `sr_dispatch`'s ELam and EMatch handlers also fail to fire when
   processing count_down's lambda body — replacement-sentinel probes
   confirmed.  Strange because they're in the same wildcard-arm body
   as EApp handler (which works for main's case).

5. Same behavior whether outer match is `match expr { | EVar n → A | _ → B }`
   (constructor-match, commit aa6af56) or
   `match (nat_eq (expr_tag expr) 0) { ... }` (nat-match, commit d077ec2).
   Both fail in the same way.

#### Strongest hypothesis

The wildcard arm body B (containing the entire nested tag-dispatch
chain — EApp, ELam, ELet, ..., EDo handlers) is lambda-lifted into a
sub-law that captures the outer environment.  When this sub-law is
invoked from a deep recursive call through `sr_rewrite_arms /
sr_rewrite_arm / pe`, something in the closure-capture or slot-indexing
prevents the inner tag-dispatch chain from reaching deeper arms like
ELam and EMatch.  EApp arm reaches because it's the outermost in the
nested-match chain.

#### Concrete next steps

In order of leverage:

1. **Inspect bytecode directly.**  Run the bootstrap on a minimal
   Compiler.gls extract that has just sr_dispatch + dependencies.
   Compare the wildcard-arm-body sub-law's compiled shape (slots,
   captures, body) to what we expect.  Identify which arm in the
   nested chain compiles wrong.

2. **Inline sr_rewrite_arms into the EMatch handler.**  Bypass the
   helper-function indirection — maybe the issue is specific to
   reaching sr_dispatch via a helper rather than directly.

3. **Rewrite the multi-arm Expr dispatch as `cg_is_X` chain.**
   Replace sr_dispatch's nested-match body with a series of
   `if cg_is_lam expr then ELam_handler else if cg_is_emat expr then ...`
   The `cg_is_X` helpers are simple, post-b61bb7e-fix, and known to
   work.  Each becomes its own law (eliminates the wildcard-arm-body
   sub-law).

4. **Bypass resolve entirely for self-refs.**  In
   `cg_make_pred_succ_law`, ALWAYS bind both the FQ name AND the
   short bare name in env1.locals when uses_self is true (mirroring
   Python's `_make_pred_succ_law` lines 3125-3127).  This way, even
   if the body's EVar has bare name (resolve broken), the env3
   lookup finds it.  Requires extracting the short name from the
   FQ; needs a small helper.

5. **Sidestep the issue entirely.**  Use a different dispatch
   structure in sr_dispatch — e.g., an explicit if-chain with
   `Reaver.BPLAN.eq` on `expr_tag` comparisons instead of nested
   `match (nat_eq ...) { ... }`.  Or use a List-of-(tag, handler)
   table iterated linearly.

### Other open follow-ups (lower priority)

- **Multi-arm unary-mixed** (e.g., `match s { | Empty → 0 | Circle r → r | Square w → w }`)
  has the same capture issue at `cg_build_unary_handler_body`'s
  multi-arm path (calls `cg_build_precompiled_nat_dispatch` with a
  fresh pred_env that drops outer captures).  Several hardcoded
  `PPin (PLaw 0 ...)` sites remain in `cg_build_unary_m_body` /
  `cg_build_m_body`.  Mechanical hint-threading like the `_inner` /
  `_app` fixes once #10 root cause is resolved.

- **The compile-self gate itself.**  After #10 is resolved (or
  enough of #10 to make Compiler.gls's compile-self work), run
  `python3 tools/selfcompile.py compiler/src/Compiler.gls`.
  Without jets, this takes hours under Reaver — schedule as
  slow-CI or background run.  Then lift into
  `TestPhaseHFixedPoint::test_compile_self` in
  `tests/reaver/test_selfhost.py`.

## Repro recipes

```bash
# Run the byte-identity fixtures (fast, ~30s):
python3 -m pytest tests/reaver/test_selfhost.py -q

# Run the full reaver suite:
python3 -m pytest tests/reaver/ -q

# Run a custom probe through the diff harness:
python3 tools/selfcompile.py /path/to/source.gls

# Compile Compiler.gls via bootstrap and dump a specific binding:
python3 -c "
import sys
sys.setrecursionlimit(200000)
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_pla import emit_program
with open('compiler/src/Compiler.gls') as f: src = f.read()
prog = parse(lex(src, 'Compiler.gls'), 'Compiler.gls')
resolved, _ = resolve(prog, 'Compiler', {}, 'Compiler.gls')
compiled = compile_program(resolved, 'Compiler')
asm = emit_program(compiled)
import re
m = re.search(r'\(#bind Compiler_sr_dispatch (.*?)\)\\n', asm, re.DOTALL)
print(m.group(1) if m else 'not found')
"
```

## Key files

- `compiler/src/Compiler.gls` — the self-host compiler source.
  Notable areas:
  - L3372-3413 — `cg_build_op2` / `cg_build_reflect_dispatch`
    (BPLAN-66 wrap, commit 65efbc4).
  - L4244-4276 — `cg_concat_under` helper (local mirror of
    name_concat_under).
  - L4278-4329 — `cg_make_pred_succ_law` (lifted-law builder,
    lifted-name `<hint>_succ` from commit 457e5a5).
  - L4400-4421 — `cg_build_nat_dispatch` (with idx threading from
    commit 7ab0dcf).
  - L4870-4910 — `cg_build_app_handler` (with `<hint>_app` name from
    commit b268e1f).
  - L5000-5025 — `cg_compile_lam_as_law` / `_lifted` (hint
    threading).
  - L6242-6407 — `sr_dispatch` (current outer-match form is
    nat_eq-based, commit d077ec2).
  - L6418-6420 — `sr_rewrite_expr` (the pass-self wrapper).

- `tests/reaver/test_selfhost.py` — byte-identity fixtures.

- `bootstrap/codegen.py` — the Python bootstrap codegen, the
  reference for byte-identity comparisons.

- `bootstrap/emit_pla.py` — Python's Plan Asm emitter; reference
  for emit-time behavior including cross-binding dedup via
  `_maybe_symbol`.

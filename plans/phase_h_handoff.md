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
- `tests/reaver/` — 51 passed, 1 xfailed. Includes 19 selfhost
  fixtures (17 byte-identical + 2 smoke).

### Byte-identity fixtures (`tests/reaver/test_selfhost.py`)

The 13 G3 byte-identical fixtures, in order of when they landed:

1. `test_single_nat_binding` — `let xx = 42`
2. `test_identity_function` — `let id = fn x -> x`
3. `test_type_annotation` — `let zz : Nat = 1`
4. `test_multi_decl_source_order`
5. `test_nested_lambda` — `let kk = fn x -> fn y -> x`
6. `test_application_in_body` — `let ap = fn ff -> fn xx -> ff xx`
7. `test_if_expression` — `let mm = if 1 then 5 else 10`
8. `test_match_top_level_wildcard` — `let mm = match 0 { | _ → 9 }`
9. `test_match_nat_in_function_body` — `let pick = λ x → match x { | 0 → 100 | _ → 200 }`
10. `test_match_multi_arm` — three named arms + wildcard
11. `test_external_mod_decl` — `external mod X { sub : Nat }`
12. `test_match_adt_single_field` — `Maybe a = None | Some a; unwrap (Some 42)`
13. `test_match_adt_nullary_multi_arm` — `Color = Red | Green | Blue`
14. `test_match_adt_multi_field` — `IntList = INil | ICons Nat IntList`
15. `test_self_recursion_in_match_wildcard` — `count_down = λ n → match n { | 0 → 0 | _ → count_down (sub n 1) }`
16. `test_cross_binding_bare_ref_in_match_wildcard` — same shape with `helper` cross-binding ref instead of self-recursion
17. **`test_match_adt_multi_arm_unary_mixed`** — `Shape = Empty | Circle Nat | Square Nat` (nullary + multi-arm unary)  *(new)*

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

### Task #10 — Self-recursion case landed (2026-05-13)

`/tmp/recursive.gls` (count_down self-recursion) is now byte-identical;
pinned as `test_self_recursion_in_match_wildcard`.  The fix is a
safety net mirroring Python's `_body_uses_self_ref`: when sr_dispatch
fails to qualify a bare EVar to its FQ form, the codegen still
recognises self-use by comparing the body's bare names against the
short tail of `cenv_self`'s FQ.  Specifically:

* New `cg_short_after_dot` helper (Compiler.gls L4185–4213) extracts
  the segment after the last `.` of an LE-encoded name nat using
  `Reaver.BPLAN.eq` (O(1)).  **Beware:** comparisons against encoded
  FQ nats must NOT use the recursive `nat_eq` (O(min m n) walks a
  Case_ chain for ~2^100+ nats — first attempt hung Reaver at >300s
  per fixture).
* `cg_body_uses_self`, `cg_make_pred_succ_law`,
  `cg_build_app_handler`, `cg_build_binary_handler_body`, and
  `cg_compile_var` all check both FQ and short tail.  Lifted-law
  builders alias the short name to the same slot as the FQ in their
  inner envs.
* `cg_var_from_env` emit-side fix: pin'd-binding refs in body context
  tag as `PNamed n val` (not `PPin (PNamed n inner)`) so emit
  produces bare `Reaver_BPLAN_sub` instead of double-pinned
  `(#pin Reaver_BPLAN_sub)`.  Mirrors Python's identity-based
  `_maybe_symbol` dedup.

### Cross-binding bare-ref case landed (2026-05-13)

Sibling of Task #10.  Pinned as
`test_cross_binding_bare_ref_in_match_wildcard`.  The fix is the
globals-by-short fallback in `cg_var_from_env`: when local and
direct-FQ globals lookup both fail, scan globals for the first FQ
whose short tail matches the bare name.  New helpers
`cg_global_lookup_by_short` and `cg_resolve_global_val` (Compiler.gls
L5108–5168).  This is the resolver-safety-net counterpart to the
Task #10 cg_body_uses_self / cg_compile_var short-tail check.

Ambiguity caveat: if two globals have the same short tail (e.g.
`Mod.foo` and `Other.foo`), the first match in globals-iteration order
wins.  Python's resolver wouldn't tolerate that — it would error or
pick by scope.  For the cases hit by Compiler.gls today there's no
ambiguity, but a stricter check would be nice.

### Multi-arm unary-mixed case landed (2026-05-13)

`type Shape = | Empty | Circle Nat | Square Nat; let area = λ s → match s
{ | Empty → 0 | Circle r → r | Square w → w }` is now byte-identical;
pinned as `test_match_adt_multi_arm_unary_mixed`.  Three coupled fixes:

* **Constructor-name short-tail fallback** (the actual root cause).
  `cg_contab_lookup_safe` — direct ctab lookup with a short-tail
  fallback, parallel to the globals-by-short fallback for cross-binding
  EVars.  Without this, bare constructor names in match arms (when
  sr_dispatch fails to qualify) miss the FQ-keyed ctab and the tag
  defaults to 0, collapsing the multi-arm dispatch structure.
  Diagnosed via a runtime probe that returned `add tag0 (mul n_cap 100)
  + 1000000`: emit showed `_1000200` (tag0=0, n_cap=2) for a case where
  tag0 should have been 1.

* **Captures-preserving `pred_env`** in
  `cg_build_precompiled_nat_dispatch`: previously created a fresh empty
  env (arity 1), dropping caller-env locals.  Now bumps the caller
  env's arity, mirrors Python's `make_ext_env`.

* **`tag0 > 0` outer-z fallback** via top-level helpers
  `cg_pcd_z_for_op2` and `cg_pcd_pairs_for_inner`.  When no field arm
  has tag 0, the outer Elim's z is `cg_quote_nat 0 n_cap` and ALL tags
  shift down by 1.  Mirrors Python's `_build_tag_chain`'s
  `first_tag > 0` branch.  Extracted into top-level helpers so the
  conditional doesn't get let-lifted into a deep sub-law (where outer
  captures wouldn't reach reliably).

#### Investigation history (sr_dispatch deep-recursion bug)

The underlying sr_dispatch issue — its ELam/EMatch/EVar handlers do
not fire for nested EVars reached through `sr_rewrite_arms →
sr_rewrite_arm → pe → sr_rewrite_expr → sr_dispatch` — is **still
unresolved**.  The 2026-05-13 fix above sidesteps it for self-refs
specifically (approach #4 from the prior session's plan).  For
cross-binding refs the deeper sr_dispatch bug still bites.

Earlier probe findings (kept for reference):

1. `sr_dispatch` IS called and CAN rewrite EVar — verified via
   `EVar 88888` sentinel that makes all bodies become PNat 0.

2. `sr_dispatch`'s EVar arm fires for SHALLOW recursion (one level
   from top): `Compiler_main = (77777 5)` confirmed when probe
   replaced EVar arm with `ENat 77777`.

3. `sr_dispatch`'s EVar arm does NOT fire for DEEP recursion through
   the helper chain.  The deep `count_down (sub n 1)` body keeps bare
   names.

4. `sr_dispatch`'s ELam and EMatch handlers also fail to fire when
   processing count_down's lambda body — replacement-sentinel probes
   confirmed.

5. Same behavior whether outer match is `match expr { | EVar n → A | _ → B }`
   (constructor-match, commit aa6af56) or
   `match (nat_eq (expr_tag expr) 0) { ... }` (nat-match, commit d077ec2).

#### Strongest hypothesis (still open)

The wildcard arm body B (the entire nested tag-dispatch chain) is
lambda-lifted into a sub-law that captures the outer environment.
When invoked from a deep recursive call, something in the closure
capture or slot indexing prevents the inner tag-dispatch chain from
reaching deeper arms.

#### Next steps for the remaining cases

1. **Inspect bytecode directly.**  Run the bootstrap on a minimal
   Compiler.gls extract that has just sr_dispatch + dependencies.
   Identify which arm in the nested chain compiles wrong.

2. **Rewrite the multi-arm Expr dispatch as `cg_is_X` chain.**  The
   `cg_is_X` helpers are post-b61bb7e-fix and known to work.  Each
   becomes its own law, eliminating the wildcard-arm-body sub-law.

3. **Globals-by-short lookup in `cg_var_from_env`.**  For
   cross-binding bare refs (e.g. `/tmp/cross_ref.gls`), the
   self-ref short-name safety net doesn't help.  A globals search
   by short-suffix would resolve bare `helper` to `Compiler.helper`
   in the lookup itself.

### Other open follow-ups (lower priority)

- **The compile-self gate itself.**  After the remaining cross-ref /
  multi-arm-unary cases land (or enough of them to make Compiler.gls's
  compile-self work), run
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

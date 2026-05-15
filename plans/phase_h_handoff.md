# Phase H — Session handoff (2026-05-14)

**Status:** in progress, multi-session.
**Roadmap reference:** `ROADMAP.md § "Phase H — Compile-self fixed point"`.

> **Next session: see [§ Work plan for next session](#work-plan-for-next-session) at the bottom.**
> That section is the actionable starting point. The earlier sections are
> background context that explains how we got to byte 293921.

## Where we are

Phase H deliverables (1) and (2) from the ROADMAP are landed; the
self-host pipeline is now byte-identical to the bootstrap for a
growing set of fixtures, and the compile-self gate (Compiler.gls
compiling itself) has been pushed from byte 334 → byte ~78148 (last
session) → byte 293921 (this session) of ~1.17M bytes byte-identical
(~25%).

### Commits this session (head → ROADMAP base)

```
3232fc0 fix(self-host): top_of_law tracking for nested-let lambda lifting
ba3dfee fix(self-host): five compile-self divergence fixes
055dde3 fix(self-host): Dwarf-flagged durability gaps in Phase H arc
ace491b fix(self-host): add Core.PLAN.unpin to BPLAN prims table
b3b2a05 fix(self-host): parse_con_arity counts atom types, not raw tokens
6d42eb2 docs(plans): compile-self gate result — diverges at parse_con_arity
c216e46 fix(self-host): multi-arm unary-mixed ADT byte-identical
3e019d7 fix(self-host): globals-by-short fallback for cross-binding bare refs
9c42174 fix(self-host): self-ref short-name safety net for recursive let bodies
4453b6c docs(plans): session handoff for Phase H continuation  (prior session's head)
```

### Compile-self gate progression

| After commit  | First divergence byte | Issue                                                                              |
| ------------- | --------------------- | ---------------------------------------------------------------------------------- |
| (initial)     | 334                   | `parse_con_arity` counted tokens for `Cons a (List a)` → arity 5 not 2             |
| b3b2a05       | 3674                  | `Core.PLAN.unpin` missing from BPLAN prims table                                   |
| ace491b       | 3771                  | `Core.IO.Prim.write_op` not specialized to `(#pin 9)`                              |
| ba3dfee §1    | 4640                  | RPLAN ops used gateway 66 instead of 82                                            |
| ba3dfee §2    | 6566                  | Lifted-law names missing `_{tag}` / `_pred` hint segments                          |
| ba3dfee §3    | 10525                 | `cg_compile_app` threaded hint into children (Python uses default '')              |
| ba3dfee §4    | 11040                 | Free-var iteration order mismatched Python's dict insertion order                  |
| ba3dfee §5    | 12370                 | Binary handler's `cenv_self henv` was always None (henv had no self_ref)           |
| ba3dfee §6    | ~26000                | `parse_ident_list` truncated at `_` (TkUnderscore) in field-pat position           |
| ba3dfee §7    | 26702                 | Type alias `type Foo = Bar` emitted stray `(#bind Compiler_ 0)`                    |
| ba3dfee §8    | ~37725                | Length's inner-law arity wrong: app-handler partial-apply used `_0` not `_1`       |
| 3232fc0       | ~78148                | Single-unary-arm + wildcard lacks tag-check (analog to "Wildcard arm drop" bootstrap workaround) |

Compile-self runs in ~470–580s under Reaver (no jets) per pass.
The next-known divergence is the single-unary-arm-with-wildcard
tag-check.  Many further bugs likely remain; see "Open follow-ups".

### Test state

- `tests/bootstrap/` — 832 passed, 4 skipped, 2 xfailed (pre-existing).
- `tests/compiler/` — 227 passed, 3 skipped.
- `tests/prelude/` — 167 passed.
- `tests/reaver/` — 53 passed, 1 xfailed. Includes 22 selfhost
  fixtures (20 byte-identical + 2 smoke).

(Test counts reflect the suite *before* the compile-self iteration
arc — the iteration arc only edited `compiler/src/Compiler.gls`
internals, not test fixtures.  All existing fixtures continue to
pass after every iteration.)

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

### Dwarf review follow-up (2026-05-14)

The Dwarf agent flagged three durability gaps after the Phase H arc
landed.  Two are now fixed and pinned; one is documented.

* **EOF infinite recursion at depth > 0** in
  ``parse_con_arity_go`` — fixed by hoisting ``is_arity_stop`` above
  the depth branch (Compiler.gls L2557).  Unbalanced open parens
  (``type Foo = | Bar (Nat``) previously recursed forever once the
  token stream ran dry; Python rejects the same input with a parse
  error, so this is a "graceful failure" property not byte-testable.

* **`pred_env` locals-drop in `cg_build_nat_dispatch`'s multi-arm
  succ-law** (Compiler.gls L4520) and **the parallel path in
  `cg_build_m_body`** (Compiler.gls L4832).  Both previously built a
  fresh empty `pred_env`, so arm bodies referencing outer-lambda
  locals collapsed to `PNat 0`.  Both now do free-var analysis
  mirroring Python's `make_succ_law` (`bootstrap/codegen.py` L1932)
  and partial-apply captured locals at the call site.  Pinned by
  `test_nullary_match_captures_outer_local`.

* **Silent shadowing in `cg_global_lookup_by_short` and
  `cg_contab_lookup_by_short`** — still open.  These fall back to
  "first FQ with matching short tail" on lookup miss; if two FQs in
  scope share a short tail, the first in iteration order silently
  wins.  Python's resolver would reject the bare reference as
  ambiguous.  No current fixture triggers this; documented for
  follow-up.

Also slated: the Hobbit review's suggested helper-inlining cuts for
`cg_global_lookup_by_short`, `cg_contab_lookup_by_short`, and the
`cg_pcd_z_for_op2` / `cg_pcd_pairs_for_inner` pair (whose "let-lifting
hazard" rationale was speculative).

### Open follow-ups (deferred this arc)

* **Single-unary-arm + wildcard tag-check.**  Next compile-self
  divergence (byte ~78148, in `tok_eat_ident`).  Python's bootstrap
  added a tag-check in `_build_field_arm_law`'s single-unary-arm
  case (codegen.py L2664-2710) so that non-matching constructors
  return the wild value via a reflect dispatch.  The self-host's
  `cg_build_unary_handler_body` single-arm path lacks this — it
  directly emits the arm body.

* **Subsequent compile-self divergences.**  Likely many.  Each
  iteration in this session moved the gate forward by 2× — 10× per
  fix; at byte 78K of 1.16M we have ~30+ further fixes anticipated.
  Specific suspected categories: more single-arm constructor
  matches without tag-checks; record-field syntax handling;
  effect-system handlers; constrained-let dictionary threading.

* **Dwarf #4 — ambiguity check in short-tail fallbacks.**
  `cg_global_lookup_by_short` and `cg_contab_lookup_by_short`
  silently pick the first FQ that shares the queried short tail.
  Implementing a "loud failure" requires changing the compiler's
  error contract (no current error path); deferred.  No Compiler.gls
  input currently has ambiguous short tails.

* **Hobbit cuts.**  Hobbit's review suggested inlining the
  single-call-site helpers `cg_global_lookup_by_short`,
  `cg_contab_lookup_by_short`, and the `cg_pcd_z_for_op2` /
  `cg_pcd_pairs_for_inner` pair.  Re-evaluated: the first two are
  self-recursive list walks that don't inline cleanly in Gallowglass
  (no idiomatic local recursion); the third was tried mid-session
  and regressed the multi-arm unary-mixed case.  All three left in
  place.

### The compile-self gate — first divergence at parenthesized type

Ran 2026-05-13 with all the session's fixes in place
(`tools/selfcompile.py compiler/src/Compiler.gls`).  Completed in
570s under Reaver (much faster than predicted — no hang).
**Diverges.**

First divergence is at byte 334, in the `Cons` constructor binding:

```
ref:    (#bind Compiler_Cons (#law "1936617283" (_0 _1 _2)         ((1 _1) _2)))                 -- arity 2
actual: (#bind Compiler_Cons (#law "1936617283" (_0 _1 _2 _3 _4 _5) (((((1 _1) _2) _3) _4) _5))) -- arity 5
```

**Root cause:** `parse_con_arity` (Compiler.gls L2553) counts *tokens*
between the constructor name and the next stop-token, not *atom types*.
For `Cons a (List a)`, the tokens after `Cons` are `a`, `(`, `List`,
`a`, `)` — five tokens, so arity = 5.  Python's parser uses
`_parse_atom_type` (bootstrap/parser.py L330) which treats
`(List a)` as a single parenthesized atom.

**Fix shape:** rewrite `parse_con_arity` to count atoms (single
identifier OR balanced-paren group).  Comment at L2552 notes the
current implementation was kept simple to avoid the "multi-nat-arm
pred_env bug" — that constraint is no longer in force now that
[[cg_short_after_dot]] and related fixes have landed in earlier
commits.  A paren-depth-tracking variant should be safe.

Output size: reference 1143093 bytes, actual 526350 bytes —
divergence cascades from the constructor arity mismatch.  Many
other parser bugs likely surface once this one is fixed; expect
multiple iteration rounds before compile-self is byte-identical.

### Other open follow-ups (lower priority)

- **Lift compile-self into a test fixture.**  Once the parser issues
  above clear and `tools/selfcompile.py compiler/src/Compiler.gls`
  reports `OK n bytes`, add `TestPhaseHFixedPoint::test_compile_self`
  in `tests/reaver/test_selfhost.py`.  The current 570s runtime is
  fast enough for slow-CI but probably too slow for the default
  pytest run — gate it behind an env-var skip.

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

---

## Work plan for next session

**Session anchor (2026-05-14, end of session):** compile-self diverges
at byte 293921 of 1167435 (~25% identical), inside the boundary
between `Compiler_parse_pin_rhs_pe` and what should follow it.

### What changed this session (commits, oldest → newest)

```
132d578 fix(self-host): single-unary-arm + wildcard tag-check byte-identical
b028d2f ci(tutorials): skip notebook smoke tests unless edited
d1eb86b fix(self-host): hint propagation through z_body/m_body + binary single-arm tag-check
e7afdb3 fix(self-host): binary handler m slot is const2(0), not wild
```

Plus a `test_single_unary_arm_with_wildcard` byte-identity fixture
pinning the single-unary-arm path.

Selfhost suite: **23 passing + 1 xfailed** (was 22+1 at session start).
No regressions in any of `tests/bootstrap/`, `tests/compiler/`,
`tests/prelude/`.

### Divergence progression this session

| After commit | Divergence byte | Issue                                                              |
| ------------ | --------------- | ------------------------------------------------------------------ |
| (start)      | 78148           | Single-unary-arm + wildcard lacks tag-check (`tok_eat_ident`)      |
| 132d578      | 105280          | Free-var union missed `wild_body`; PCD base case lifted via `_wild_succ` |
| 132d578      | 109909          | `cg_build_app_handler` z passed hint=0 (`is_atom_start`)           |
| d1eb86b §3   | 229869          | Multi-arm dispatch hint=0; missing `<hint>_tag` rename             |
| d1eb86b §4   | 232433          | Binary single-arm path lacked tag-check (`has_guard_sentinel`)     |
| e7afdb3      | 293921          | Binary handler m-slot lifted wild instead of `const2(body_nat(0))` |

### The wall — what blocks us at byte 293921

The next divergence is **structural**, not a local codegen tweak:

```
REF[…]:  …(#pin Compiler_skip_ann) _3))))))\n(#bind Compiler_parse_expr      …
ACT[…]:  …(#pin Compiler_skip_ann) _3))))))\n(#bind Compiler_parse_expr_dispatch …
```

REF's next binding is `parse_expr`; ACT's is `parse_expr_dispatch`.
The two are part of a 5-member mutually-recursive SCC:

```python
SCC 454: [Compiler.parse_expr, Compiler.parse_expr_dispatch,
          Compiler.parse_handle_arms, Compiler.parse_handle_expr,
          Compiler.parse_handle_op_arm]
```

Python's bootstrap (`bootstrap/codegen.py::_compile_mutual_scc`,
~L3822) detects this SCC via Tarjan and emits all 5 bindings under a
**shared-pin encoding** (see `spec/02-mutual-recursion.md`):

```
shared_pin = P(selector_law  law_0  law_1  …  law_{n-1})
```

with each `law_i` lambda-lifted to accept the shared pin as its
first argument. External wrapper laws of original arity are then
emitted in lexicographic order within the SCC.

The self-host (`compiler/src/Compiler.gls::cg_pass3`) just walks
let-decls in source order and compiles each with `cg_compile_let_one`
— **no SCC detection, no shared-pin encoding.** Each binding stands
alone with EFix-based self-reference, and cross-SCC calls resolve
via the global env.

The two encodings are semantically equivalent for Reaver (both
work at runtime), but byte-divergent. Closing this gap is the
next major arc.

### Recommended task order

#### Task A — Shared-pin SCC encoding in Compiler.gls (BIG)

**Estimate:** multi-session. This is the principal remaining work.

Add Tarjan SCC analysis to `compile_program`, then route
multi-member SCCs through a `cg_compile_mutual_scc` helper that
mirrors `bootstrap/codegen.py::_compile_mutual_scc` (~L3822 onward).

Concrete subtasks (in suggested order):

1. **Dep graph builder.** Port `_build_dep_graph` to Compiler.gls.
   For each `DLet`, walk its body collecting free `EVar` names that
   resolve to other top-level lets. Output: `List (Pair Nat (List Nat))`
   (binding → list-of-dependencies, by FQ nat). The body-walker
   already exists for free-vars in `cg_collect_free` — repurpose or
   parallel it.

2. **Tarjan SCC.** Port `_tarjan_scc` (codegen.py L3773). Recursive
   DFS with `index`, `lowlink`, `stack`. Within Gallowglass this needs
   a pass-self pattern (no native mutual recursion) and explicit state
   passing — likely a 3-tuple `(Nat /* next index */, List (Pair Nat Nat) /* index/lowlink */, List Nat /* stack */)`.

3. **SCC ordering.** Sort each SCC's members lexicographically by FQ
   name (`sorted(scc)` in Python L3808). Reaver.BPLAN.eq + a
   `nat_lt` already-exists helper handles the comparison; the
   sort itself can be insertion-sort on the list (mirror
   `cg_insert_sorted_pair` at L4344).

4. **Shared-pin selector law.** For SCC size n ≥ 2, build
   `selector_law = L(n+1, 0, body)` where `body` dispatches on
   the last argument (index 1..n) and returns the corresponding
   `law_i` slot. Pattern: an op2 chain
   `op2(law_0, succ(op2(law_1, succ(...), pred-1)), pred)`.
   Mirror `bootstrap/codegen.py::_compile_mutual_scc` ~L3849.

5. **Lambda-lifted member laws.** Each `law_i` gets one extra
   parameter (the shared pin) prepended; references to other SCC
   members in its body become `(shared_pin index_j)` applications.
   Self-reference uses the corresponding index.

6. **External wrappers.** After the shared pin is built, emit a
   wrapper law of the original arity per member that partial-applies
   `(shared_pin idx)` with the wrapper's params. These are the
   bindings external callers see.

7. **Pass through `cg_pass3`.** Replace the per-decl
   `cg_compile_let_one` walk with: iterate decls in source order;
   for each `DLet`, look up its SCC; if single-member, compile as
   today; if multi-member and not yet emitted, emit the whole SCC
   group (selector + wrappers) atomically.

Reference reading:
- `spec/02-mutual-recursion.md` — full encoding spec.
- `bootstrap/codegen.py` L3773-3816 (`_tarjan_scc`).
- `bootstrap/codegen.py` L3822-3935 (`_compile_mutual_scc`).
- `bootstrap/codegen.py` L350-405 (`compile_program`'s SCC dispatch).

**Validation strategy:**
- Add a `test_mutual_recursion_byte_identical` fixture pairing a
  minimal mutual-rec pair (e.g. `is_even`/`is_odd`) before tackling
  the 5-member SCC inside Compiler.gls.
- Each step should leave the existing 23 selfhost fixtures green.
- Once the shared-pin emit lands, the compile-self gate should
  jump well past byte 293921 — Compiler.gls has many SCCs (parser
  chain, sr_dispatch chain, etc.) all of which currently emit in
  source order.

#### Task B — Document the next-after divergences (cheap)

Before Task A lands, the compile-self gate can be probed for *what*
the next divergences look like by temporarily reordering Compiler.gls's
source to match Python's lexicographic SCC sort. This won't fix
byte-identity (shared-pin shape still differs) but it will surface
post-SCC bugs early.

A simpler diagnostic: run `tools/selfcompile.py` on a minimal mutual-rec
fixture (`is_even`/`is_odd`) and confirm the divergence shape matches
the expected shared-pin vs source-order split. This is a 30-line
fixture, fast to write.

#### Task C — Hold-over follow-ups (small, opportunistic)

These are unchanged from the prior session's plan; address if
convenient between Task A iterations:

* **Dwarf #4** — ambiguity check in short-tail fallbacks
  (`cg_global_lookup_by_short`, `cg_contab_lookup_by_short`).
  Documented in earlier sections; no current trigger.

* **Hobbit cuts** — helper-inlining suggestions for
  `cg_global_lookup_by_short`, `cg_contab_lookup_by_short`, and the
  `cg_pcd_z_for_op2`/`cg_pcd_pairs_for_inner` pair. Re-evaluated
  twice now: list-walks don't inline cleanly in Gallowglass; left
  in place.

* **Compile-self as a test fixture.** Once byte-identical, add
  `TestPhaseHFixedPoint::test_compile_self` gated behind an env-var
  (the 600s runtime is too slow for the default pytest run but fine
  for slow-CI).

### Repro recipes (unchanged from prior session)

```bash
# Selfhost byte-identity fixtures (fast, ~30s):
python3 -m pytest tests/reaver/test_selfhost.py -q

# Single fixture isolation:
python3 -m pytest tests/reaver/test_selfhost.py::TestPhaseG3ByteIdentity::test_single_unary_arm_with_wildcard -v

# Custom probe through the diff harness:
python3 tools/selfcompile.py /path/to/source.gls

# The full compile-self gate (~600s, run sparingly):
timeout 600 python3 tools/selfcompile.py compiler/src/Compiler.gls \
  --write-actual /tmp/cs.actual.txt 2>/tmp/cs.diff.txt
cat /tmp/cs.diff.txt
```

To find which binding contains a given divergence byte:

```bash
python3 -c "
import sys, re
sys.setrecursionlimit(200000)
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit_pla import emit_program
src = open('compiler/src/Compiler.gls').read()
prog = parse(lex(src, '<>'), '<>')
resolved, _ = resolve(prog, 'Compiler', {}, '<>')
compiled = compile_program(resolved, 'Compiler')
out = emit_program(compiled)
binds = [(m.start(), m.group(1)) for m in re.finditer(r'\(#bind (\w+) ', out)]
TARGET = 293921  # ← replace with the current divergence byte
for i, (start, name) in enumerate(binds):
    if start <= TARGET and (i+1 == len(binds) or binds[i+1][0] > TARGET):
        print(f'Byte {TARGET} is in {name}, offset {TARGET-start}')
        break
"
```

To decode an LE-packed law-name nat (when output shows e.g.
`(#law "1886413151" …)`):

```bash
python3 -c "
n = 1886413151
out=[]
while n>0: out.append(n&0xFF); n>>=8
print(bytes(out))
# → b'_app'
"
```

### Key files (updated)

- `compiler/src/Compiler.gls` — the self-host compiler source. New
  notable areas as of this session:
  - L4711-4754 — `cg_pcd_pairs_for_inner` / `cg_pcd_z_for_op2` helpers.
  - L4738-4830 — `cg_build_precompiled_nat_dispatch` with wild-aware
    z-arm (commits d1eb86b / e7afdb3).
  - L4914-4980 — `cg_build_z_body` / `cg_build_m_body` with hint
    parameter (commit d1eb86b).
  - L5039-5159 — `cg_build_unary_handler_body` single-arm + wild
    tag-check (commit 132d578).
  - L5193-5237 — `cg_build_unary_m_body` Nil-tag>0 branch (commit
    e7afdb3, returns `const2(body_nat(0))` regardless of wild).
  - L5293-5354 — `cg_build_binary_handler_body` single-arm tag-check
    (commit d1eb86b).
  - L5368-5414 — `cg_build_app_handler` with wild_body in free-var
    union (commit 132d578).

- `tests/reaver/test_selfhost.py` — byte-identity fixtures.
  `test_single_unary_arm_with_wildcard` added this session.

- `bootstrap/codegen.py` — Python bootstrap reference. The SCC work
  in Task A targets ~L350-405 (compile_program), L3773-3816
  (_tarjan_scc), L3822-3935 (_compile_mutual_scc).

- `spec/02-mutual-recursion.md` — the formal specification of shared
  pins; read this before starting Task A.

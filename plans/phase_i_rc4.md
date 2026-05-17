# Phase I — Toward 1.0.0-rc4

**Status:** rc4-1 ✅ closed; rc4-2 deferred to a follow-up continuation.

**Scope:** close the two remaining self-host codegen gaps that rc3 mapped
but didn't close, so the 1.0 "self-hosted" qualifier can be honestly
claimed.  See `ROADMAP.md §1.0` for the promoted acceptance criteria.

The Python bootstrap already compiles both features correctly — these are
pure byte-identity gaps in the self-host's emission, not user-facing
correctness bugs.

## Status as of 2026-05-16

| Gate | Status | Notes |
|---|---|---|
| `test_typeclass_simple` | ✅ closed | xfail flipped to pass; byte-identical to Python |
| `test_do_notation_simple` | ⏳ deferred | rc4-2; not yet started |
| Phase H `test_compile_self` | ⏳ pending | needs re-run after Compiler.gls changes (long; ~45 min) |
| `compiler/dist/Compiler.plan` regen | ✅ done | MANIFEST updated, sanity tests pass |

## rc4-1 — Typeclass constrained-let codegen  ✅

**Gate test:** `tests/reaver/test_selfhost.py::test_typeclass_simple`
— closed; passes byte-identical to Python.

**What landed (5 commits on `phase-i-1.0.0-rc4`):**

* `TkFatArrow` lex token (`=>` and `⇒`) — was the foundational
  blocker; the bare `=` lexed mid-constraint shredded `skip_ann`.
* `DLet` AST extended to carry a constraint class-name list,
  packed as a `Pair (List Nat) Expr` to keep DLet binary (avoids
  the bootstrap mixed-arity dispatch hazard).
* `extract_constraints_from_ann` walks the type-annotation tokens
  capturing `Class typevar =>` prefix patterns.
* `PConstrained (List Nat) PlanVal` sentinel + `planval_is_constrained`
  / `planval_get_constraints` / `planval_get_constrained_underlying`
  accessors; `cg_var_from_env` transparently unwraps so emission
  paths see the underlying law.
* `cg_compile_constrained_let` wraps the body in extra dict-param
  lambdas (one per method per constraint via `cg_wrap_constraint_lams`),
  compiles via the normal path, and tags with `PConstrained`.
* `cg_collect_class_methods` registry built from `DClass` decls,
  threaded through `cg_pass3_go` as a new parameter.
* `cg_compile_inst_members` emits the single-method dict shortcut
  (`Module.inst_<Class>_<Type>` = the one method's val).
* `cg_compile_app` introspects the EApp root: if its global value
  is `PConstrained`, routes to `cg_compile_constrained_app` which
  walks the chain for user args, resolves the instance dict via
  `cg_resolve_instance_dict`, and applies the assembled chain.
* `cg_find_first_fq_for_law` — canonical-name resolution by law
  NAME nat + ARITY.  Mirrors Python emit's `bind_table[id(val)]`
  semantics for the case where multiple bindings (impl, per-method
  inst, single-method shortcut) share the same PLaw object.

**Documented limitations:**

* Multi-method classes: the single-method dict shortcut path is the
  only one wired up.  Multi-method instance dicts would need a
  record-shape encoding that the call site decomposes.
* Multi-constraint lets: parsing supports a single `Class typevar =>`
  prefix; grouped constraints (`(Eq a, Ord a) =>`) and chained
  constraints (`Eq a => Ord a =>`) aren't yet recognised.
* Type-key inference at call sites: ENat → "Nat" only.  Lets, app
  results, etc. aren't resolved; they'd need a typecheck pass.
* `cg_find_first_fq_for_law` keys on PLaw NAME nat + ARITY rather
  than full structural equality (Reaver.BPLAN.eq is a nat-value
  compare; `op 66 ["Equal"]` would be true deepseq+(==) but isn't
  in `bplan_deps.py` / Reaver.BPLAN's external mod).  Correct for
  the bare-EVar inlining shortcut (same object → same name + arity)
  but could collide if two distinct top-level lets compile to PLaws
  with identical name nats AND arities — extremely unlikely since
  name nats are `encode_name` of the binding name.

## Historical sketch — rc4-1 plan (pre-execution)

**Reference:** `bootstrap/codegen.py::_compile_constrained_let` and
`_compile_constrained_app` (~200 LoC together).  Also
`_constrained_lets` registry and `_class_methods`/`_instance_dicts`
state on the `Compiler` object.

Three coupled changes to `compiler/src/Compiler.gls`:

1. **Arity adjustment for constrained lets.**
   Source: `let same : ∀ a. Eq a => a → a → Nat = λ x y → eq x y`.
   Python adds one dict-param per constraint, so the compiled law for
   `same` is arity 3 (dict + 2 user params) and the dict shows up as
   `_3` from inside the body.  Class-method references inside the body
   resolve to dict-projection (`_3` for a single-method class, or
   `(_3 idx)` for multi-method) rather than to global symbol lookup.

2. **Single-method dict shortcut emission.**
   When a class has exactly one method, Python emits
   `Compiler_inst_Eq_Nat` as a direct alias to the method law, not as
   a one-element record.  Detect arity-1 classes in
   `cg_compile_inst_members` and emit the shortcut.

3. **Call-site dict insertion.**
   At `same 7 7`, Python's `_compile_constrained_app` recognises that
   `same` is in `_constrained_lets` and inserts the resolved instance
   dict as the first arg: `same inst_Eq_Nat 7 7`.  Dict resolution
   uses type inference at the call site (see `_infer_type_key` /
   `_type_to_instance_key`).  For the gate test, Nat literals make
   the dict resolution trivial — the harder cases (lets, application
   results) can fall back to surface-syntax heuristics for now since
   the bootstrap typecheck pass isn't in the self-host.

**Verification strategy:**
- Add a tiny harness that compiles the gate-test source via both
  Python and the self-host (run through `python3 tools/selfcompile.py`
  on a small typeclass fixture).
- Diff bytes; fix the first divergence; repeat.
- Once the small fixture is byte-identical, run the Phase H compile-
  self gate to confirm we haven't regressed `Compiler.gls` itself
  (none of `Compiler.gls`'s own bindings are constrained, so it
  should be a no-op there).

## rc4-2 — Effect handler CPS alignment

**Gate test:** `tests/reaver/test_selfhost.py::test_do_notation_simple`
(currently xfail; flip to pass).

**Reference:** `bootstrap/codegen.py::_compile_handle` and
`_compile_do` (post-M13.3 open-continuation protocol; the GLS side
got the protocol port in M13.4 but didn't reach byte-identity).

Three known divergences from Python's emit, per rc3 investigation:

1. **Extra captured-slot indirections in the dispatch chain.**
   Self-host's lifted continuation laws have one or two extra slots
   that Python doesn't.  Audit `cg_compile_dispatch_fn` and
   `cg_build_handle_dispatch` capture-set computation against
   `_compile_handle`'s — likely the self-host is capturing the
   handler's own bound names where Python uses sentinel substitution.

2. **Cross-references emit as `(#pin inc)` rather than the FQ
   `Compiler_Counter_inc`.**  The eff-op name resolution is using
   the bare op-name nat instead of the scope-qualified one.  Likely
   `cg_register_eff_ops` is storing the wrong key, or
   `cg_compile_dispatch_fn`'s arm-body compile is looking it up by
   bare name.

3. **Mis-numbering of let-binding slots inside lifted continuations.**
   The `_5((_2 _3))` shapes in observed output show let-bindings
   allocating slots inconsistent with Python's numbering — `cg_compile_do`'s
   inner-continuation param order (`[caps, k_open_outer, dispatch, x]`)
   may need a tweak, or the let-allocator inside the lifted law is
   off by one.

**Verification strategy:** same diff-driven loop as rc4-1 against a
single small handle/do fixture.

## After both gaps close

1. **Re-bootstrap `compiler/dist/Compiler.plan`** via Python — the
   seed must reflect the new Compiler.gls source.  Update
   `compiler/dist/MANIFEST.json` (BLAKE3 + `size_bytes` via
   `os.path.getsize`, not `len(string)` — Compiler.gls has Unicode).
2. **Run Phase H compile-self gate** (`GALLOWGLASS_RUN_COMPILE_SELF=1
   pytest tests/reaver/test_selfhost.py::TestPhaseHFixedPoint`).
   Expected runtime ~20-45 min under Reaver no-jets.
3. **Flip the two xfails to pass** in `tests/reaver/test_selfhost.py`.
4. **Tag v1.0.0-rc4**, push, verify CI green across all matrix
   entries.
5. **Red-team review:** dispatch Dwarf (failure modes), Hobbit
   (overengineering), Elf (naming + long-term shape), Gnome (actual
   behavior), Angel (transparency/documentation) in parallel against
   the rc4 tag.  Address findings before tagging 1.0 final.

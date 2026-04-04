# Gallowglass Roadmap

**Last updated:** 2026-04-04
**Current status:** Alpha — M8 complete (Path B). M9.1–9.4 complete. M10.1–10.7 complete. 667 tests passing.

This document is the delivery plan: what ships in what order and why. The *what* of each feature is in `SPEC.md` and the `spec/` documents. The *why* of ordering decisions is in `DECISIONS.md`.

---

## ✅ Alpha

**Alpha is declared.** The original M8.8 Path A gate (cog wrapper + planvm byte-identical
round-trip) has been superseded by the PLAN spec update: the cog/driver model no longer
exists. It has been replaced by direct side-effects with virtualization for sandboxed
pure execution. There is no stable cog wrapping API to target.

The alpha acceptance criteria, as met:

- M8.8 Path B: GLS `emit_program` processes full `Compiler.gls` and produces correct
  Plan Assembler output via the Python harness ✅
- planvm seed loading: `Compiler.main` seed is a valid seed ✅ (planvm-gated CI)
- M9 restricted dialect improvements (fix, tuples, mutual recursion, typechecks) ✅
- M10 effect handlers (CPS codegen, pure, do-notation, namespacing) ✅
- Integration test battery (Fibonacci, Ackermann, Sudan) ✅
- 667 tests passing ✅

**Deferred to post-alpha (pending upstream stabilization):**

M8.8 Path A equivalent — running the compiler via the VM's side-effect I/O model to
validate planvm byte-identical output — is deferred until the direct side-effects +
virtualization API stabilizes (see `IO.md`). This is tracked as a post-1.0 CI gate,
not an alpha blocker.

Everything below is post-alpha.

---

## ✅ M9 — Restricted dialect improvements

Goal: make the restricted dialect useful for real programs before adding
architectural complexity.

### ✅ M9.1 — `fix` expressions

`fix λ self args → body` surface syntax. First param maps to law self-ref (N(0));
law arity = len(user_params). `_compile_fix` in `bootstrap/codegen.py`.

### ✅ M9.2 — Tuples

`(a, b)` encodes as `A(tag_0, a, b)` using quote form for tag 0 in law bodies.
`PatTuple` dispatches via `_compile_con_match_case3` with ConInfo(tag=0, arity=2).

### ✅ M9.3 — Mutual recursion (SCC compilation)

Tarjan SCC detection in `compile()` pass 3. Multi-element SCCs use shared-pin row
encoding (`spec/02-mutual-recursion.md`). Canonical SCC ordering: lexicographic.

### ✅ M9.4 — Type checker extensions

`ExprFix` now correctly infers the fix type `T` (not the lambda type `T→T`).
`_check_decls` groups DeclLets into SCCs (Tarjan) and processes in topological order,
deferring generalization for multi-member SCCs. 79 tests passing.

**Ordering note:** M9.4 is a prerequisite for M10 (effect rows in the type checker
before checking handlers). All other M9 items are independent.

---

## M10 — Effect handlers

The defining language feature. `handle` expressions with explicit `resume`
continuations (Koka-style). Handlers compile using direct-style CPS: the
continuation `k` in a handler arm is a partially applied PLAN law.

### ✅ M10.1 — Effect row types in type checker

`TRow(effects: dict, tail: TMeta)` and `TComp(row, ty)` added. Full row unification
(flatten-and-distribute algorithm). `DeclEff` registers each op as
`∀ params r. A → {E args | r} B`. `ExprHandle` checked against spec §5.1:
computation type `{E, R} α`, return arm `x : α → β`, op arms `arg, k → β`, result `{R} β`.
Tests: `tests/bootstrap/test_typecheck.py` (89 pass — 10 new).

### ✅ M10.2 — Codegen: CPS transform for effect handlers

`eff` ops compile to 3-arg CPS laws. `handle comp { arms }` assembles as
`A(A(comp_val, dispatch_fn), return_fn)`. Do-notation `x ← rhs in body` compiles
to a CPS bind via nested lambda-lifted laws. Outer local captures are lambda-lifted
into both the dispatch law and the inner continuation law.
Tests: `tests/bootstrap/test_codegen.py` (63 pass — 10 new).

### ✅ M10.3 — `pure` for do-notation

`pure v` registered as builtin CPS law `L(3, "pure", bapp(N(3), N(1)))`.
`pure v` = `A(pure_law, v)` — a 2-arg partial application that calls `k v`.
Enables do chains to terminate with a pure computed value.

### ✅ M10.4 — State-threading handler validation

Multi-op do chains with captured free variables compile and evaluate correctly.
Tests confirmed nested lambda lifting across `ss ← get_st () in pp ← put_st ss in pure ss`.

### ✅ M10.5 — Per-effect tag namespacing

`_resolve_handler_arm` resolves each `HandlerOp.op_name` to its FQ form so the codegen uses
per-effect tag numbering. Two effects sharing a short op name produce a scope ambiguity error
rather than silent mis-dispatch.

**M10.6 — Integration test battery (`tests/bootstrap/test_programs.py`):**
Fibonacci (self-recursive let + fix), Ackermann, Sudan function. 20 tests.
All programs compile via Python bootstrap and evaluate against expected Nat
outputs using the PLAN reference evaluator. ✅

**M10.7 — GLS compiler: `EFix` + `TkFix` support:**
Added `TkFix` token, `EFix Nat (Pair (List Nat) Expr)` AST constructor,
keyword lexer recognition of `fix`, `parse_fix_body_pe`, `cg_compile_fix`.
Updated `expr_tag`, `cg_cf_dispatch`, `cg_hv_dispatch`, `sr_dispatch`,
`cg_compile_complex`, and `parse_expr_dispatch`. M8.8 self-hosting invariant
preserved (667 tests passing). ✅

**Remaining M10 scope:**
- Full surface syntax integration with effect annotations in the prelude
- GLS compiler: `DEff`/`EHandle`/`EDo` support (deferred to post-M10)
- Runtime: no change — continuations are ordinary PLAN values

**Unblocked by M10:** `IO`, `Exn`, `State`, `Generator` effects. The CSV and
calculator demos become interactive. The full surface syntax of SPEC.md §3.4
becomes compilable.

---

## M11 — Typeclasses

Implicit dictionary synthesis at call sites, instance declaration, coherence.

Scope:
- Parser: `class`, `instance`, constraint syntax (`Eq a =>`)
- Type checker: constraint collection, instance resolution, dictionary elaboration
- Codegen: constraints become explicit Law arguments (Glass IR explicit-dict form)
- Coherence: enforced via content-addressing (no orphan instance problem by construction;
  see `DECISIONS.md §"Why content-addressed identity?"`)

**Unblocked by M11:** `Show`, `Eq`, `Ord`, `Add`, `Serialize` instances. The
standard prelude becomes expressible without explicit dictionary passing.

---

## M12 — Module system

Multi-file compilation, `use`/`import`, package identity.

Scope:
- Build system: dependency graph resolution (acyclic by construction)
- `use Module.Path { names }` syntax and name resolution
- Instance visibility: explicit instance imports (see `DECISIONS.md §"Why explicit
  instance imports?"`)
- Package identity: module PinIds are stable across renames

**Unblocked by M12:** The full Core prelude can be split across files. Cross-module
typeclass instances. The `mod` declaration syntax from `spec/06-surface-syntax.md §11`.

---

## 1.0

All of the above complete. Acceptance criteria:

- Full Gallowglass surface syntax (`spec/06-surface-syntax.md`) compiles correctly
- Core prelude (`prelude/src/Core/`) fully implemented and split across modules
- Effect handlers, typeclasses, and mutual recursion all working and self-hosted
- The `Data.Csv` example from `spec/06-surface-syntax.md §15` compiles and runs
- CI passes: Python harness + planvm seed loading + M8.8 Path A equivalent for 1.0 compiler

---

## Post-1.0

These are not on the critical path to 1.0 and are deferred explicitly.

### Rust VM
Dual-VM CI: running programs on both planvm and the Rust VM, detecting divergence.
Primary runtime post-1.0. Designed with snapshot retention and debugger needs from
the start. Built after self-hosting because building it first would mean building
against speculative usage patterns. See `DECISIONS.md §"Why a purpose-built Rust VM?"`.

### Debugger and Glass IR
Glass IR as a live view over running programs, snapshot queries, effect injection.
Full `spec/01-glass-ir.md` implementation. Requires the Rust VM (snapshot retention
is a VM concern). The spec is complete; the implementation waits for the VM.

### Contract system
Pre/post contracts with tiered discharge: syntactic (always), built-in procedures
(linear arithmetic, list length), runtime checks (degraded), SMT backend (optional).
See `DECISIONS.md §"Why three tiers?"`. The contract syntax is in the parser from M9
onward; discharge is initially `Deferred(NoSolver)` for everything. The solver tiers
are added incrementally post-1.0.

### Jet registry and optimizer
The jet-matching optimizer written in PLAN itself, as described in `DECISIONS.md
§"Why does jet matching logic live in the optimizer?"`. Requires a stable set of
jets to optimize against — i.e., a working prelude and several real programs.

### Pattern matching codegen: Hd/Sz/CaseN/Ix convention
The current codegen uses opcode 3 (`Case_`) directly for all pattern matching. Sol
confirmed this is "extremely heavy" — the intended convention is `Hd`/`Sz` for branch
identification, `CaseN` jets for switching, and `Ix` for field extraction. Migrating
the codegen (both Python bootstrap and GLS self-hosting compiler) to this convention
is a post-1.0 correctness-preserving optimization. See `DECISIONS.md §"Why Case_ for
pattern matching now?"`.

### Text/Bytes high-bit length encoding
Current encoding uses a plain `(byte_length, content_nat)` pair. Sol recommends using
a high bit to encode the length for efficiency (avoids a separate length field for
small strings). Migration requires updating the bootstrap emitter, prelude, and any
code that introspects Text/Bytes representation. Deferred until the encoding is
finalized upstream.

### VM I/O integration (M8.8 Path A equivalent for 1.0)
Once the direct side-effects + virtualization API stabilizes, wrap `Compiler.main` to
read source from the VM's I/O channel and write Plan Assembler to the output channel.
Run the compiled compiler on its own source via the VM and assert byte-identical output.
This is the definitive planvm-executed self-hosting gate.

---

## What is NOT on this roadmap

- **Dependent types**: explicitly out of scope. See `DECISIONS.md §"Why algebraic
  effects with row typing rather than dependent types?"`.
- **Garbage collector**: PLAN's heap is a persistent Merkle-DAG; there is no
  allocation/collection cycle in the traditional sense.
- **FFI beyond External**: the `External` effect and `external mod` cover the VM
  boundary. A traditional C FFI is not planned.

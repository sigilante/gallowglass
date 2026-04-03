# Gallowglass Roadmap

**Last updated:** 2026-04-03
**Current status:** Alpha candidate — M8 complete (Path B), M8.8 Path A pending cog I/O.

This document is the delivery plan: what ships in what order and why. The *what* of each feature is in `SPEC.md` and the `spec/` documents. The *why* of ordering decisions is in `DECISIONS.md`.

---

## Alpha closeout

**Blocker: cog I/O model** (awaiting confirmation from PLAN authors).

The remaining work is wrapping `Compiler.main : Bytes → Bytes` as a planvm cog that
reads source from stdin and writes Plan Assembler to stdout. Once the I/O model is
confirmed:

- Implement cog wrapper in `Compiler.gls` (small addition to Section 26)
- Run `compiler.seed` on `Compiler.gls` source via planvm
- Assert output matches Path B byte-for-byte (M8.8 Path A)
- Tag alpha release

Everything below is post-alpha.

---

## M9 — Restricted dialect improvements

Goal: make the restricted dialect useful for real programs before adding
architectural complexity. All work is self-hosting: changes go into `Compiler.gls`
first, then tested against itself.

### M9.1 — `fix` and `let rec` in surface syntax

Currently, recursive functions work via the de Bruijn self-reference slot (index 0)
but there is no surface syntax for anonymous recursion. `fix` and `let rec` both
lower to the same Law encoding already used.

### M9.2 — Tuples

Syntactic sugar for binary/unary constructor pairs. `(a, b)` desugars to `MkPair a b`
in codegen. Pattern `(x, y)` desugars to a two-field match. No new PLAN encoding;
purely a parser and codegen addition.

### M9.3 — Mutual recursion (SCC compilation)

The spec and encoding are complete (`spec/02-mutual-recursion.md`). Currently the
compiler rejects any SCC with more than one definition. This milestone implements
the shared-pin encoding for mutually recursive groups. Unblocks a large class of
real programs (e.g. even/odd, mutually recursive parsers, rose trees).

### M9.4 — Type checker

The self-hosting compiler currently trusts well-typed input. This milestone adds
restricted Hindley-Milner: type inference for the restricted dialect, monomorphic
and simply-polymorphic (`∀ a.`) types. Effect rows are parsed but not unified (that
comes in M10). No typeclasses yet.

**Ordering note:** M9.4 is a prerequisite for M10 (you need effect rows in the type
checker before you can check handlers). The other M9 items are independent.

---

## M10 — Effect handlers

The defining language feature. `handle` expressions with explicit `resume`
continuations (Koka-style). Handlers compile using direct-style CPS: the
continuation `k` in a handler arm is a partially applied PLAN law.

Scope:
- Parser: `handle expr { | return x → e | op args k → e }` syntax
- Type checker: effect row unification, row polymorphism (`| r`)
- Codegen: CPS transform for handler arms; continuation reification as PLAN laws
- Runtime: no change — continuations are ordinary PLAN values

This milestone touches every phase of the compiler. It is the largest single
milestone post-alpha and is the prerequisite for the full effect-annotated prelude.

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

---

## What is NOT on this roadmap

- **Dependent types**: explicitly out of scope. See `DECISIONS.md §"Why algebraic
  effects with row typing rather than dependent types?"`.
- **Garbage collector**: PLAN's heap is a persistent Merkle-DAG; there is no
  allocation/collection cycle in the traditional sense.
- **FFI beyond External**: the `External` effect and `external mod` cover the VM
  boundary. A traditional C FFI is not planned.

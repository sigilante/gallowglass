# Bootstrap Compiler

**Language:** Sire (PLAN assembly language)
**Target:** PLAN seed files
**Input:** Restricted Gallowglass dialect

This document specifies the scope, restricted dialect, implementation milestones, and compilation strategy for the Gallowglass bootstrap compiler.

The bootstrap compiler is the Phase 1 deliverable. Its sole purpose is to compile enough of Gallowglass to write the core prelude (Phase 2) and, eventually, the self-hosting compiler (Phase 3). It is thrown away after Phase 3.

---

## 1. Implementation Language: Sire

Sire is the macro/assembly language of the PLAN VM, defined in `boot.sire` of xocore-tech/PLAN. A Sire program is a sequence of top-level bindings that expand into PLAN laws and pins. The bootstrap compiler is written in Sire and is compiled by the xocore Sire toolchain.

### 1.1 Why Sire?

- Sire is the lowest practical abstraction above raw PLAN; writing in it forces alignment with the VM's actual model.
- Sire bindings become pinned PLAN values directly, so the bootstrap compiler's output is naturally in the same format as its own representation.
- Using Sire avoids bootstrapping a second language (e.g. OCaml or Haskell) as an intermediate.
- The self-hosting compiler, once working, can supersede the bootstrap compiler without any foreign toolchain dependency.

### 1.2 Sire Source Layout

```
bootstrap/src/
  main.sire       ← Top-level driver: reads source, calls pipeline, emits seed
  lexer.sire      ← Lexer: bytes → token list
  token.sire      ← Token type definitions
  parser.sire     ← Parser: token list → AST
  ast.sire        ← AST type definitions
  scope.sire      ← Scope resolution: qualified names, module namespacing
  typecheck.sire  ← Type checker: restricted HM unification
  lower.sire      ← Lowering: AST → Glass IR (restricted subset)
  codegen.sire    ← Code generator: Glass IR → PLAN laws/pins
  emit.sire       ← Seed emitter: PLAN value → seed bytes
  prelude.sire    ← Compiler's own prelude (list, option, map utilities)
```

---

## 2. Restricted Dialect

The bootstrap compiler implements a strict subset of the full Gallowglass surface syntax (spec/06-surface-syntax.md). The restriction is not arbitrary: every feature excluded is either (a) expressible by desugaring into included features, or (b) only needed once we have the core prelude.

### 2.1 Included

| Feature | Notes |
|---|---|
| `let` bindings | Top-level and local, non-recursive and recursive |
| `λ` expressions | Anonymous functions, multi-argument |
| Function application | Left-associative, juxtaposition |
| `type` declarations | Sum types (`\|` constructors) only; no record syntax |
| `match` expressions | Exhaustiveness is checked by the bootstrap checker (spec/03) |
| `if`/`then`/`else` | Desugars to match on Bool |
| Tuple literals | Up to 8-tuples; desugars to left-associated App |
| `let rec` / mutually recursive | Single-SCC only; no cross-SCC mutual recursion |
| `module` / `import` | Module declaration and qualified imports |
| Nat, Bool, Text, Bytes literals | Primitive literals |
| `pin` expression | Programmer-controlled pinning |
| Operator sections | `(+ 1)`, `(1 +)` |
| Basic type annotations | Monomorphic and simple polymorphic (`∀ a.`) |
| `external mod` declarations | VM boundary for Core.* primitives |
| Basic typeclasses | `class`, `instance`; no functional dependencies, no associated types |

### 2.2 Excluded (deferred to self-hosting compiler)

| Feature | Reason |
|---|---|
| `handle` expressions | Effect handlers require continuation passing, deferred |
| Effect rows in types | Type inference still works without; effects are checked post-Phase-3 |
| Contracts (`\| pre`, `\| post`) | No solver in bootstrap; contract syntax is parsed but ignored |
| Refinement types | Same |
| Row-polymorphic records | Nominal records with fixed fields only |
| Type class functional dependencies | Not needed for prelude |
| `fix` expressions | Expressible as `let rec` |
| `with dict` | Dictionary elaboration handled implicitly |
| List comprehensions | Desugared manually |
| String interpolation | Not in core prelude |
| `do` notation | Not in restricted dialect |

### 2.3 Effect Handling in the Bootstrap

The bootstrap compiler does not check or enforce effect rows. Functions may include effect annotations in their type signatures (the parser accepts them), but the type checker ignores effect rows. This is sound because:

1. The core prelude uses only `External` and `IO` effects at VM boundaries, which the bootstrap treats as opaque.
2. The self-hosting compiler (Phase 3) will re-type-check the prelude with full effect inference.
3. Contracts are deferred — bootstrap programs cannot violate contracts that are not checked.

---

## 3. Pipeline

```
Source bytes
    │
    ▼
Lexer (lexer.sire)
    │  token list
    ▼
Parser (parser.sire)
    │  AST (ast.sire)
    ▼
Scope resolver (scope.sire)
    │  qualified AST
    ▼
Type checker (typecheck.sire)   ← restricted HM, no effect rows
    │  typed AST
    ▼
Lowering (lower.sire)           ← AST → Glass IR (restricted)
    │  Glass IR nodes
    ▼
SCC analysis + lambda lifting   ← spec/02-mutual-recursion.md
    │  PLAN law graph
    ▼
Code generator (codegen.sire)   ← laws/pins, de Bruijn indices
    │  PLAN values
    ▼
Seed emitter (emit.sire)        ← spec/07-seed-format.md
    │
    ▼
Seed bytes (stdout)
```

### 3.1 SCC Compilation

Follows spec/02-mutual-recursion.md exactly:

- Tarjan's algorithm detects SCCs in the dependency graph.
- Singleton SCCs with no self-reference: emitted as a plain law.
- Singleton SCCs with self-reference: emitted as a plain law (self-reference uses de Bruijn index 0).
- Multi-member SCCs: emitted as a shared pin `({0 (n+1) 0} law₀ law₁ ... lawₙ₋₁)`.
- Canonical ordering within each SCC: **lexicographic by fully-qualified name**. Any deviation changes PinIds.

### 3.2 Type Checker

The bootstrap type checker implements the Hindley-Milner core (Algorithm W):

- Unification with occurs check.
- Let-generalization at top-level bindings.
- Monomorphic local bindings (no let-polymorphism for local `let`).
- Typeclass constraints: single-parameter, no functional dependencies.
- Effect rows: parsed, stored, never unified. All effect-annotated types are treated as if the annotation were absent.

Reporting: the bootstrap type checker emits errors but does not attempt recovery. The first type error terminates compilation.

---

## 4. Module System

The bootstrap module system is minimal:

- `module Name` declares the current module. One module per file.
- `import Qualified.Name (sym1, sym2)` imports specific names.
- `import Qualified.Name` imports all exported names.
- All top-level bindings are exported by default. There is no `export` list in the bootstrap.
- The compiler is invoked with an ordered list of source files; later files may import from earlier files.

Module names map to file paths by convention: `Core.List` → `Core/List.gls`. The `--include` flag adds search paths.

---

## 5. Milestones

### Milestone 1: Lexer complete

- Tokenizes all restricted dialect source.
- Handles Unicode normalization (ASCII → canonical Unicode per spec/06 §1.2).
- Tests: `tests/bootstrap/test_lexer.py` round-trips all token types.

### Milestone 2: Parser complete

- Produces AST for all restricted dialect constructs.
- Rejects all excluded constructs with a clear error.
- Tests: `tests/bootstrap/test_parser.py` covers all grammar productions.

### Milestone 3: Type checker complete

- Algorithm W unification.
- Typeclass instance resolution (single-parameter).
- Tests: `tests/bootstrap/test_typecheck.py` covers principal type examples.

### Milestone 4: Codegen complete

- Compiles a trivial Gallowglass program (`let main = 42`) to a valid seed.
- The seed loads and evaluates correctly in the Python dev harness.
- Tests: `tests/bootstrap/test_codegen.py` round-trips trivial programs.

### Milestone 5: Self-compile candidate

- The bootstrap compiler can compile `bootstrap/src/` as a Gallowglass program.
- (The bootstrap compiler itself is written in Sire, but the self-hosting compiler in Phase 3 will be written in Gallowglass. This milestone verifies the output of Phase 3's compiler matches the output of Phase 1.)

---

## 6. Invariants

These must never be violated:

- **Canonical SCC order is lexicographic.** Any deviation silently changes PinIds of all downstream definitions.
- **`pin` desugars before SCC analysis.** A `@x = e` binding pins `e` and makes `x` a reference to the pin, not to `e` directly.
- **The bootstrap does not evaluate at compile time.** Evaluation is the VM's job. The compiler produces un-reduced PLAN terms. Exception: constant-folding of literal Nat arithmetic is permitted as an optimization.
- **One module per file.** The bootstrap has no multi-module file support.
- **Error messages include source locations.** Every diagnostic includes `file:line:col`.

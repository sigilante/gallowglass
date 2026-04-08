# Gallowglass Language Guide

**Version:** 0.3 (alpha)
**Date:** 2026-04-08
**Status:** M18 complete. 1149 tests passing, 145 skipped. All 8 prelude modules typecheck.

Gallowglass is a statically typed functional programming language targeting the
PLAN virtual machine. It is designed for LLMs to write correctly and reason about
accurately: high local constraint, explicit effects, pure by default, canonical
naming enforced by the compiler.

This document describes the language **as currently implemented** by the Python
bootstrap compiler. Where the specification (`spec/`) describes features not yet
implemented, the gap is noted.

---

## 1. Values and Types

### Primitive Types

| Type    | Description                            | Literal syntax         |
|---------|----------------------------------------|------------------------|
| `Nat`   | Natural number (arbitrary precision)   | `0`, `42`, `1000`      |
| `Bool`  | Boolean (True=1, False=0 at PLAN level)| `True`, `False`        |
| `Text`  | UTF-8 string                           | `"hello"`, `"a #{x} b"`|
| `Bytes` | Raw byte sequence                      | `b"data"`, `0x"4F"`    |

All types are erased at compile time. The PLAN output is untyped. Type errors are
purely a compile-time concern.

### Algebraic Types

```gallowglass
type Option a =
  | None
  | Some a

type Result a b =
  | Ok a
  | Err b

type List a =
  | Nil
  | Cons a (List a)
```

Nullary constructors compile to bare nats (tag number). Unary and binary
constructors compile to `App(tag, field)` or `App(App(tag, field1), field2)`.

### Tuples

```gallowglass
let pair = (1, 2)
let (x, y) = pair
```

Binary tuples `(a, b)` are supported in expressions and patterns. They encode
as `App(App(0, a), b)` — a 2-field constructor with tag 0.

### Records

```gallowglass
type Point = { x : Nat, y : Nat }

let origin = { x = 0, y = 0 }
let moved = origin { x = 3 }

match point {
  | { x = px, y = py } -> add px py
}
```

Records desugar to single-constructor algebraic types during scope resolution.
Field-name punning is supported: `{ x }` means `{ x = x }`.

### Type Aliases

```gallowglass
type Byte = Nat
```

Type aliases are resolved during scope resolution. No runtime code is generated.

---

## 2. Functions

### Lambda Expressions

```gallowglass
let id = λ x -> x
let add = λ m n -> Core.PLAN.inc (add m (pred n))
```

ASCII alternatives are normalized to Unicode at the lexer: `\` or `lambda`
become `λ`, `->` becomes `→`.

### Let Bindings

```gallowglass
let name : Type = body

-- With type annotation
let add : Nat -> Nat -> Nat = λ m n -> ...

-- Local let
let result = let x = 42 in add x 1
```

Top-level `let` declarations are compiled to PLAN laws. Self-recursive references
use law self-reference (de Bruijn index 0).

### Type Annotations

```gallowglass
let id : ∀ a. a -> a = λ x -> x
let const : ∀ a b. a -> b -> a = λ x y -> x
```

The type checker uses Algorithm W (Hindley-Milner) with let-generalization.
Annotations are checked but types are fully erased at codegen.

### Fix Expressions

```gallowglass
let factorial = fix λ self n ->
  match n {
    | 0 -> 1
    | k -> mul n (self k)
  }
```

`fix` provides explicit recursion. The first parameter is the self-reference.

---

## 3. Pattern Matching

```gallowglass
match expr {
  | pattern1 -> body1
  | pattern2 -> body2
}
```

### Pattern Forms

| Pattern            | Description                          |
|--------------------|--------------------------------------|
| `_`                | Wildcard (matches anything)          |
| `x`                | Variable binding                     |
| `42`               | Nat literal                          |
| `"hello"`          | Text literal                         |
| `None`             | Nullary constructor                  |
| `Some x`           | Unary constructor with binding       |
| `Cons h t`         | Binary constructor with bindings     |
| `(x, y)`           | Tuple pattern                        |
| `h :: t`           | List cons pattern (desugars to Cons) |
| `[]`               | Empty list (desugars to Nil)         |
| `{ x = px }`       | Record pattern                       |
| `p1 \| p2`         | Or-pattern (duplicates arm body)     |
| `pat if guard`     | Guard (desugars to if-else + rematch)|

### If-Then-Else

```gallowglass
if condition then true_branch else false_branch
```

Desugars to a match on Bool constructors.

### Nat Matching

Nat patterns use predecessor binding:

```gallowglass
match n {
  | 0 -> "zero"
  | k -> "nonzero, predecessor is k"
}
```

The variable `k` binds to `n - 1` (the predecessor), not to `n` itself. This
matches the PLAN opcode 3 (Case_) semantics directly.

---

## 4. Module System

### Module Structure

Each `.gls` file is a module. Module names are dot-qualified: `Core.Nat`,
`Core.List`, `Core.Text`.

### Imports

```gallowglass
-- Import specific names
use Core.Nat { add, mul, Eq }

-- Qualified access (Module.name)
use Core.Bool
```

The build driver (`bootstrap/build.py`) resolves module dependencies via Kahn's
topological sort. Circular dependencies are rejected.

### External Modules

```gallowglass
external mod Core.PLAN {
  inc : Nat -> Nat
}
```

External modules map to VM primitives. `Core.PLAN` provides the five PLAN opcodes:
`pin` (0), `mk_law` (1), `inc` (2), `reflect` (3), `force` (4).

---

## 5. Type Classes

### Class Declaration

```gallowglass
class Eq a {
  eq : a -> a -> Bool
  neq : a -> a -> Bool = λ a b -> if eq a b then False else True
}
```

Single-parameter type classes with optional default method implementations.

### Instance Declaration

```gallowglass
instance Eq Nat {
  eq = nat_eq
}

instance Eq a => Eq List {
  eq = λ xs ys -> list_eq_go eq xs ys
}
```

Constrained instances (e.g., `Eq a => Eq List`) receive dictionary parameters
for the constraint. Dictionary insertion at call sites is automatic.

### Constrained Functions

```gallowglass
let neq : Eq a => a -> a -> Bool = λ x y ->
  if eq x y then False else True
```

The compiler inserts an implicit dictionary parameter for each constraint. At
call sites, the dictionary is resolved from the concrete type.

### Prelude Type Classes

| Class     | Methods              | Instances                              |
|-----------|----------------------|----------------------------------------|
| `Eq a`    | `eq`, `neq`          | Nat, Bool, Text, Option, List, Result  |
| `Ord a`   | `lt`, `lte`, `gt`, `gte`, `min`, `max` | Nat, Bool     |
| `Add a`   | `add`                | Nat, Text                              |
| `Show a`  | `show`               | Nat, Bool, Option, List                |
| `Debug a` | `debug`              | Nat, Bool, Option, List                |

`Show` is for users; `Debug` is for developers. They are distinct classes
and must never be conflated.

---

## 6. Effect System

### Effect Declaration

```gallowglass
eff State s {
  get : ⊤ -> s
  put : s -> ⊤
}
```

### Handlers

```gallowglass
handle computation {
  | return x -> result_body
  | get _ k -> handler_for_get k
  | put s k -> handler_for_put s k
}
```

The continuation `k` is a first-class value. Calling `k value` resumes the
computation at the point where the effect was performed.

### Do-Notation

```gallowglass
let example : {State Nat} Text =
  n <- get () in
  _ <- put (add n 1) in
  pure "done"
```

`x <- rhs in body` is CPS sugar: it compiles to a continuation-passing call.
`pure v` terminates a do-chain.

### Effect Rows in Types

```gallowglass
let stateful : Nat -> {State Nat | r} Nat = λ n -> ...

-- Pure function (empty effect row)
let pure_fn : Nat -> Nat = λ n -> add n 1
```

Effect rows are sets of effects with an optional row variable `r` for
polymorphism. `{}` (empty row) means pure. Absence of an effect annotation
also means pure.

### Key Invariant

`Abort` never appears in an effect row. It is unhandleable and propagates to
the VM's virtualization supervisor.

---

## 7. Compilation Target

Gallowglass compiles to PLAN — a minimal graph-reduction VM with four
constructors and five opcodes:

| Constructor  | Syntax   | Description                    |
|-------------|----------|--------------------------------|
| Pin         | `<v>`    | Content-addressed immutable ref|
| Law         | `{n a b}`| Named function (name, arity, body)|
| App         | `(f g)`  | Function application           |
| Nat         | `@n`     | Natural number                 |

| Opcode | Name   | Operation                           |
|--------|--------|-------------------------------------|
| 0      | Pin    | Content-address a value             |
| 1      | Law    | Construct a law                     |
| 2      | Inc    | Increment a nat                     |
| 3      | Case_  | Pattern match (nat zero/succ, app)  |
| 4      | Force  | Force evaluation to WHNF            |

Hash algorithm: BLAKE3-256 everywhere. No exceptions.

### Pin-Based Module Loading (M16)

Each compiled definition gets a PinId — the BLAKE3-256 hash of its seed
serialization. The prelude is published as a pinned DAG: 111 pins across
8 modules. Programs reference upstream dependencies by hash.

```
prelude/manifest/prelude.json    -- combined manifest: FQ name -> PinId
prelude/manifest/Core.Nat.json   -- per-module manifest
```

### Glass IR (M17 + M18)

Glass IR is the human/LLM-readable intermediate representation. Every compiler
decision is visible: fully-qualified names, explicit dictionaries, pin hashes,
type annotations.

```gallowglass
-- Snapshot: pin#374da474
-- Source: Core.Combinators.id
-- Budget: 4096 tokens

let Core.Combinators.id [pin#374da474] : ∀ a. a -> a
  = λ x -> x
```

Glass IR fragments are emitted per definition to `prelude/glass_ir/`.
Type annotations are inferred via Algorithm W and rendered with `∀` quantifiers
and `⇒` constraint arrows.

---

## 8. Core Prelude

8 modules, 65 source-level definitions (112 compiled definitions including
instance methods), 111 pins.

### Module Dependency Order

```
Core.Combinators  (no deps)
  -> Core.Nat     (uses Core.PLAN.inc)
  -> Core.Bool    (uses Core.Nat.Eq)
  -> Core.Text    (uses Core.Nat, Core.Bool)
  -> Core.Pair    (uses Core.Nat, Core.Bool)
  -> Core.Option  (uses Core.Nat, Core.Bool, Core.Text)
  -> Core.List    (uses Core.Nat, Core.Bool, Core.Text, Core.Option)
  -> Core.Result  (uses Core.Nat, Core.Bool, Core.Text)
```

### Module Contents

**Core.Combinators** (7 definitions): `id`, `const`, `flip`, `compose`, `apply`,
`pipe`, `fixpoint`

**Core.Nat** (11 definitions): `pred`, `is_zero`, `nat_eq`, `nat_lt`, `nat_lte`,
`nat_gte`, `add`, `mul`, `sub`, `div_nat`, `mod_nat`. Classes: `Eq`, `Ord`, `Add`.
Instances: `Eq Nat`, `Ord Nat`, `Add Nat`.

**Core.Bool** (6 definitions): `not`, `and`, `or`, `xor`, `bool_eq`, `bool_select`.
Instance: `Eq Bool`, `Ord Bool`.

**Core.Text** (12 definitions): `text_length`, `text_content`, `text_eq`,
`text_concat`, `mk_char`, `show_digit`, `show_nat_rev`, `show_nat`, `debug_nat`,
`debug_bool`, `sub_text`, `char_at`. Classes: `Show`, `Debug`. Instances:
`Show Nat`, `Show Bool`, `Debug Nat`, `Debug Bool`, `Eq Text`, `Add Text`.

**Core.Pair** (5 definitions): `fst`, `snd`, `map_fst`, `map_snd`, `swap`.

**Core.Option** (5 definitions): `is_none`, `is_some`, `with_default`,
`map_option`, `bind_option`. Instances: `Eq Option`, `Show Option`, `Debug Option`.

**Core.List** (12 definitions): `is_nil`, `is_cons`, `head`, `tail`, `singleton`,
`length`, `map`, `filter`, `foldl`, `foldr`, `list_eq_go`, `debug_list_go`.
Instances: `Eq List`, `Show List`, `Debug List`.

**Core.Result** (7 definitions): `is_ok`, `is_err`, `unwrap`, `unwrap_err`,
`map_ok`, `map_err`, `bind_result`. Instance: `Eq Result`.

---

## 9. Specification Gaps

Features specified in `spec/` but not yet implemented in the bootstrap compiler.
These are explicitly deferred, not bugs.

### Type System Gaps

| Feature                    | Spec reference    | Status                          |
|----------------------------|-------------------|---------------------------------|
| Multi-parameter classes    | spec/05 SS5.2     | Deferred to self-hosting        |
| Associated types           | spec/05           | Deferred to self-hosting        |
| Functional dependencies    | spec/05           | Deferred post-1.0               |
| Refined types              | spec/05 SS2.8     | Parsed, no solver               |
| Existential types (`∃`)    | spec/05           | Parsed, not checked             |
| Higher-kinded types        | spec/05           | Not implemented                 |
| Row-polymorphic records    | spec/05 SS5.3     | Deferred                        |
| Kind inference             | spec/05           | Not implemented                 |

### Effect System Gaps

| Feature                         | Status                              |
|---------------------------------|-------------------------------------|
| Effect polymorphism in handlers | Only fixed rows in handlers         |
| Deep vs shallow handler control | `once` modifier parsed; shallow via open-continuation CPS |
| Effect constraints in classes   | Not implemented                     |

### Surface Syntax Gaps

| Feature                    | Spec reference    | Status                          |
|----------------------------|-------------------|---------------------------------|
| Contracts (pre/post/inv)   | spec/05 SS8       | Parsed, `Deferred(NoSolver)`    |
| Quotation / metaprogramming| spec/06 SS6.11    | Parsed, not evaluated           |
| Macros                     | spec/06           | Not implemented                 |
| Package declarations       | spec/06           | Reserved keywords only          |
| Export lists               | spec/06           | All bindings implicitly exported|
| Nested list patterns       | spec/06           | `[a, b]` pattern deferred; `h :: t` and `[]` work |
| Deriving                   | spec/06           | Not implemented                 |

### Runtime/Infrastructure Gaps

| Feature                    | Status                                       |
|----------------------------|----------------------------------------------|
| Rust VM                    | Post-1.0                                     |
| Debugger                   | Post-1.0 (requires Rust VM)                  |
| Jet registry / optimizer   | Post-1.0                                     |
| Contract solver tiers      | Post-1.0 (parser ready)                      |
| VM I/O integration         | Pending upstream stabilization               |
| Glass IR text round-trip   | AST round-trip works; text parse deferred     |
| Trace / Pending in Glass IR| Requires debugger + runtime                  |
| Dictionary elaboration in Glass IR | Requires codegen runtime state        |

### What IS Fully Implemented

- Complete Hindley-Milner type inference with let-generalization
- Algebraic data types with pattern matching (Nat, Bool, constructors)
- Type classes with single-parameter constraints and default methods
- Constrained instances (`Eq a => Eq List`)
- Cross-module compilation with dependency resolution
- Algebraic effects with CPS handlers, do-notation, `pure`
- Effect row types with row unification
- Mutual recursion via SCC (Tarjan) with shared-pin row encoding
- Records (via scope-level desugaring to ADTs)
- String interpolation (`"hello #{name}"`)
- Or-patterns and guards in match arms
- Pin-based module loading with BLAKE3-256 content addressing
- Glass IR emission with type annotations
- Self-hosting compiler (restricted dialect) validated through M8.8 Path B

---

## 10. Build and Test

```bash
# Run all tests
python3 -m pytest tests/

# Run specific test suites
python3 -m pytest tests/bootstrap/     # compiler tests
python3 -m pytest tests/prelude/       # prelude tests
python3 -m pytest tests/compiler/      # self-hosting compiler tests

# Build prelude as pinned DAG with Glass IR
python3 -m bootstrap.build_prelude --seeds --glass-ir

# Compile a single file
python3 -c "
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.codegen import compile_program
from bootstrap.emit import emit
import sys
src = open(sys.argv[1]).read()
prog = parse(lex(src, sys.argv[1]), sys.argv[1])
resolved, _ = resolve(prog, 'Module', {}, sys.argv[1])
compiled = compile_program(resolved, 'Module')
sys.stdout.buffer.write(emit(compiled, 'Module.main'))
" input.gls > output.seed
```

### Test Skip Categories

145 skipped tests are all expected:

- **planvm-gated (75):** Seed loading and VM execution tests requiring the `planvm`
  binary. These run in the `plan-vm` CI job.
- **Deep recursion (4):** Stress tests hitting Python's recursion limit. These work
  on the actual PLAN VM.
- **Driver smoke (1):** Requires planvm.
- **Remaining (65):** planvm-gated prelude seed, compiler seed, and seed format tests.

---

## 11. Unicode Conventions

Gallowglass uses Unicode operators canonically. ASCII alternatives are accepted
at the lexer and normalized:

| Unicode | ASCII alternative | Meaning            |
|---------|-------------------|--------------------|
| `→`     | `->`              | Function arrow     |
| `λ`     | `\`, `lambda`     | Lambda             |
| `∀`     | `forall`          | Universal quantifier|
| `⇒`     | `=>`              | Constraint arrow   |
| `←`     | `<-`              | Do-bind / effect   |
| `≠`     | `!=`              | Not equal          |
| `≤`     | `<=`              | Less or equal      |
| `≥`     | `>=`              | Greater or equal   |

Post-lexer, only Unicode forms appear. ASCII alternatives never survive lexing.

### Naming Conventions (Compiler-Enforced)

| Category        | Convention      | Examples                |
|-----------------|-----------------|-------------------------|
| Functions/values| `snake_case`    | `add`, `map_option`     |
| Types/effects   | `PascalCase`    | `Option`, `State`       |
| Type variables  | single `a`-`q`  | `a`, `b`, `c`           |
| Row variables   | single `r`-`z`  | `r`, `s`                |
| Modules         | `Dot.Qualified` | `Core.Nat`, `Core.List` |

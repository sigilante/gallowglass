# Core Prelude

**Phase:** Milestone 7.5
**Dialect:** Restricted Gallowglass (see `bootstrap/BOOTSTRAP.md §2`)
**Compiler:** Python bootstrap (`bootstrap/`)
**VM:** xocore-tech/PLAN (`x/plan` / `planvm`)

The Core prelude is the first layer of the Gallowglass standard library written
in Gallowglass itself.  Its sole purpose is to provide the primitives that the
self-hosting compiler (Milestone 8) will need to bootstrap.

---

## Design Constraints

### 1. No cross-module imports

The bootstrap compiler does not yet support multi-file compilation (`module` /
`import` declarations are deferred to the self-hosting compiler).  Each prelude
file is compiled independently.  Dependencies between modules are expressed by
inlining the required definitions, not by importing them.

This is intentional: the prelude is small, and inlining is cleaner than
implementing a module system just to throw it away.

### 2. Core.PLAN primitives

`external mod Core.PLAN { ... }` declarations compile to real PLAN opcode pins.
The bootstrap codegen maps the known `Core.PLAN` operations directly:

| Gallowglass declaration        | Compiles to | Harness opcode |
|-------------------------------|-------------|----------------|
| `Core.PLAN.pin : a → Pin`     | `P(N(0))`   | opcode 0 (Pin) |
| `Core.PLAN.mk_law : ...`      | `P(N(1))`   | opcode 1 (Law) |
| `Core.PLAN.inc : Nat → Nat`   | `P(N(2))`   | opcode 2 (Inc) |
| `Core.PLAN.reflect : ...`     | `P(N(3))`   | opcode 3 (Case_) |
| `Core.PLAN.force : a → a`     | `P(N(4))`   | opcode 4 (Force) |

All other `external mod` declarations produce opaque sentinel pins (valid seeds
but not callable at runtime).

### 3. Surface syntax the bootstrap compiles

| Surface form                        | Compiles to                        |
|-------------------------------------|------------------------------------|
| `if c then t else f`                | opcode 3 (Case_) applied to 6 args |
| `match n { 0 → e0 \| k → use_k }`  | opcode 3 + predecessor binding     |
| `match opt { None → e \| Some x → f x }` | opcode 3 App handler (field extraction) |
| Nat literals                        | `N(k)` or `A(N(0), N(k))` in body |
| `λ x → body`                        | PLAN Law                           |
| `type T = \| C1 \| C2 a`           | nullary=bare nat, unary=App(tag,field) |
| Self-recursion (`let f = λ ... → f ...`) | N(0) in law body              |

### 4. Self-contained files

Each `.gls` file must compile and produce planvm-valid seeds for every exported
`let` declaration.  The test suite validates this automatically.

---

## Module Dependency Order

```
Core.Combinators    -- id, const, flip, compose, apply  (no deps)
Core.Bool           -- not, and, or, xor, bool_eq, bool_select  (uses if)
Core.Nat            -- pred, is_zero, nat_eq, nat_lt, add, mul  (uses Core.PLAN.inc)
Core.Option         -- is_none, is_some, with_default, map_option, bind_option
Core.List           -- is_nil, is_cons, singleton, head, tail, map, filter, foldl, foldr
Core.Text           -- Text type, Show typeclass, show_nat, show_bool  (uses Core.Nat + Core.Bool)
```

Each module is validated independently.  There are no runtime cross-module
calls (definitions are inlined when needed).

---

## Module Summary

| Module           | Definitions | Description                                         |
|------------------|-------------|-----------------------------------------------------|
| Core.Combinators | 5           | id, const, flip, compose, apply                     |
| Core.Bool        | 6           | not, and, or, xor, bool_eq, bool_select             |
| Core.Nat         | 7           | pred, is_zero, nat_eq, nat_lt, add, mul, is_zero    |
| Core.Option      | 7           | None, Some + 5 functions                            |
| Core.List        | 11          | Nil, Cons + 9 functions                             |
| Core.Text        | 13 + Show   | text_length/content/eq/concat, sub, div/mod, show_nat/bool, Show class |

Total: ~**49 definitions**, all planvm-valid.

### Core.Text.Prim externals

`external mod Core.Text.Prim { ... }` compiles to pre-built PLAN laws in the
bootstrap codegen (not opaque sentinels):

| Declaration                       | Compiles to                                      |
|-----------------------------------|--------------------------------------------------|
| `mk_text  : Nat → Nat → Text`     | `P(L(2, 'mk_text', A(A(N(0),N(1)),N(2))))`      |
| `text_len : Text → Nat`           | `P(L(1, 'text_field', Case_ app_fun_selector))`  |
| `text_nat : Text → Nat`           | `P(L(1, 'text_field', Case_ app_arg_selector))`  |

`Text` is encoded as `A(N(byte_length), N(content_nat))` — a raw PLAN App of two
Nats, **not** the GLS tagged-pair encoding `A(A(N(0), f1), f2)`.

---

## File Layout

```
prelude/
  PRELUDE.md               ← this file
  src/Core/
    Combinators.gls        ← pure combinators
    Bool.gls               ← Bool type and logical operations
    Nat.gls                ← Nat utilities (inc via Core.PLAN.inc)
    Option.gls             ← Option (Maybe) type with field extraction
    List.gls               ← List type and higher-order functions
    Text.gls               ← Text type, Show typeclass, show_nat/bool

tests/prelude/
  test_core_combinators.py ← compile + planvm validation
  test_core_bool.py
  test_core_nat.py
  test_core_option.py
  test_core_list.py
  test_core_text.py        ← 44 harness tests (bplan jets) + 15 planvm seed tests
```

---

## Invariants

- Every exported `let` in a prelude module must produce a planvm-valid seed.
- Only `Core.Nat` uses `external mod Core.PLAN` (for `inc`).
- Pattern match is the canonical way to dispatch on Nat and algebraic types.
- `if/then/else` dispatches on Bool (False=0, True=1 nat encoding).
- Bool globals (`True`, `False`) and nullary constructors compile to bare nats
  (quote form) inside law bodies — never pinned — so Case_ dispatch is correct.

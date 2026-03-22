# Core Prelude

**Phase:** Milestone 7
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

### 2. No external VM primitives (yet)

`external mod` declarations compile to opaque sentinel pins
(`P(N(encode_name(fq)))`).  These are accepted by planvm as valid seeds but
cannot be *called* at runtime.  Operations that require VM opcodes directly
(increment, nat-case, reflect) must be expressed via the surface syntax the
bootstrap already compiles:

| Surface form              | Compiles to                        |
|---------------------------|------------------------------------|
| `if c then t else f`      | opcode 3 (Case_) applied to 6 args |
| `match n { 0 → e0 \| _ → e1 }` | opcode 2 (nat iteration)     |
| Nat literals              | `N(k)` or `A(N(0), N(k))` in body |
| `λ x → body`              | PLAN Law                           |
| `type T = \| C1 \| C2`   | constructor functions              |

No `external mod` declarations appear in the prelude until the self-hosting
compiler can resolve them to real BPLAN primitives.

### 3. Self-contained files

Each `.gls` file must compile and produce planvm-valid seeds for every exported
`let` declaration.  The test suite validates this automatically.

---

## Module Dependency Order

```
Core.Combinators    -- id, const, flip, compose  (no deps)
Core.Bool           -- Bool, and, or, not         (no extern deps; uses if)
Core.Nat            -- zero, succ_of, pred_of, add, is_zero  (uses match)
Core.Option         -- Option, map_option, bind_option, with_default
Core.List           -- List, map, filter, foldl, foldr, append, reverse, length
```

Each module is validated independently.  There are no runtime cross-module
calls (definitions are inlined when needed).

---

## File Layout

```
prelude/
  PRELUDE.md               ← this file
  src/Core/
    Combinators.gls        ← pure combinators
    Bool.gls               ← Bool type and logical operations
    Nat.gls                ← Nat utilities built on pattern match
    Option.gls             ← Option (Maybe) type
    List.gls               ← List type and higher-order functions

tests/prelude/
  test_core_combinators.py ← compile + planvm validation
  test_core_bool.py
  test_core_nat.py
  test_core_option.py
  test_core_list.py
```

---

## Invariants

- Every exported `let` in a prelude module must produce a planvm-valid seed.
- No module may use `external mod`.
- No module may use operators (`+`, `-`, `*`, `==`, `<`) — these desugar to
  `Core.Nat.add` etc., which are unresolved sentinel pins.
- Pattern match is the canonical way to dispatch on Nat and algebraic types.
- `if/then/else` dispatches on Bool (False=0, True=1 nat encoding).

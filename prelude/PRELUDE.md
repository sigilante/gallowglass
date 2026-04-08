# Core Prelude

**Phase:** M18 complete (type-annotated Glass IR)
**Dialect:** Restricted Gallowglass (see `bootstrap/BOOTSTRAP.md §2`)
**Compiler:** Python bootstrap (`bootstrap/`)
**VM:** xocore-tech/PLAN (`x/plan` / `planvm`)

The Core prelude is the first layer of the Gallowglass standard library written
in Gallowglass itself. It provides the foundational types, classes, and functions
that all Gallowglass programs build on.

---

## Design Constraints

### 1. Cross-module imports (M12)

The bootstrap compiler supports multi-file compilation via `use` imports.
Modules are compiled in dependency order by the build driver (`bootstrap/build.py`).
Each module declares its dependencies explicitly:

```gallowglass
use Core.Nat { Eq, add, mul, sub, div_nat, mod_nat, nat_eq, nat_lt, nat_gte, pred, is_zero }
```

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

### 3. Pin-based module loading (M16)

The prelude is published as a pinned DAG: 111 pins across 8 modules. Each
definition gets a PinId (BLAKE3-256 hash of its seed serialization). Manifests
map FQ names to PinIds.

### 4. Glass IR emission (M17 + M18)

Every `let` definition is emitted as a Glass IR fragment with FQ names, pin
hashes, and inferred type annotations. All 8 modules typecheck.

---

## Module Dependency Order

```
Core.Combinators    (no deps)
  → Core.Nat        (uses Core.PLAN.inc)
  → Core.Bool       (uses Core.Nat.Eq)
  → Core.Text       (uses Core.Nat, Core.Bool)
  → Core.Pair       (uses Core.Nat, Core.Bool)
  → Core.Option     (uses Core.Nat, Core.Bool, Core.Text)
  → Core.List       (uses Core.Nat, Core.Bool, Core.Text, Core.Option)
  → Core.Result     (uses Core.Nat, Core.Bool, Core.Text)
```

---

## Module Summary

| Module           | Let defs | Instance methods | Total compiled | Description |
|------------------|----------|------------------|----------------|-------------|
| Core.Combinators | 7        | 0                | 7              | id, const, flip, compose, apply, pipe, fixpoint |
| Core.Nat         | 11       | 8                | 21             | Arithmetic, comparison. Eq, Ord, Add classes + instances |
| Core.Bool        | 6        | 4                | 8              | Logical ops. Eq Bool, Ord Bool instances |
| Core.Text        | 12       | 10               | 25             | Text ops. Show, Debug classes + instances |
| Core.Pair        | 5        | 0                | 6              | fst, snd, map_fst, map_snd, swap |
| Core.Option      | 5        | 6                | 13             | Option type. Eq, Show, Debug instances |
| Core.List        | 12       | 6                | 20             | List ops. Eq, Show, Debug instances |
| Core.Result      | 7        | 2                | 11             | Result type. Eq instance |

**Total: 65 let definitions, 112 compiled definitions, 111 pins.**

### Type Classes

| Class     | Methods                                  | Instances                          |
|-----------|------------------------------------------|------------------------------------|
| `Eq a`    | `eq`, `neq` (default)                    | Nat, Bool, Text, Option, List, Result |
| `Ord a`   | `lt`, `lte`, `gt`, `gte`, `min`, `max`   | Nat, Bool                          |
| `Add a`   | `add`                                    | Nat, Text                          |
| `Show a`  | `show`                                   | Nat, Bool, Option, List            |
| `Debug a` | `debug`                                  | Nat, Bool, Option, List            |

---

## Core.Text.Prim externals

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
    Combinators.gls        ← pure combinators (id, const, flip, compose, apply, pipe, fixpoint)
    Nat.gls                ← Nat utilities, Eq/Ord/Add classes + instances
    Bool.gls               ← Bool type, logical ops, Eq/Ord instances
    Text.gls               ← Text type, Show/Debug classes + instances
    Pair.gls               ← Binary pair operations
    Option.gls             ← Option type, Eq/Show/Debug instances
    List.gls               ← List type and higher-order functions, Eq/Show/Debug instances
    Result.gls             ← Result type, Eq instance
  manifest/                ← per-module and combined pin manifests (JSON)
  glass_ir/                ← per-definition Glass IR fragments (.gls, gitignored)
  pins/                    ← per-definition seed files (gitignored)

tests/prelude/
  test_core_combinators.py
  test_core_bool.py
  test_core_nat.py
  test_core_option.py
  test_core_list.py
  test_core_text.py
  test_core_pair.py
  test_core_result.py
  test_full_prelude.py     ← cross-module integration
  test_pin_prelude.py      ← pin DAG verification
  test_glass_ir_prelude.py ← Glass IR emission + type annotation tests
```

---

## Invariants

- Every exported `let` in a prelude module must produce a planvm-valid seed.
- Only `Core.Nat` uses `external mod Core.PLAN` (for `inc`).
- Pattern match is the canonical way to dispatch on Nat and algebraic types.
- `if/then/else` dispatches on Bool (False=0, True=1 nat encoding).
- Bool globals (`True`, `False`) and nullary constructors compile to bare nats
  (quote form) inside law bodies — never pinned — so Case_ dispatch is correct.
- `Show` and `Debug` are distinct typeclasses. Never conflate them.
- Pin content is reduced to WHNF + law spine — not to full normal form.
- Canonical SCC ordering is lexicographic by name — any deviation changes PinIds.

# Gallowglass Phrasebook

A dense reference of canonical Gallowglass patterns, written to be
included in an LLM's context window. Each section gives the smallest
example that exercises one feature, with the inferred type or
expected result. Where the same idea has a common pitfall, the
pitfall is explicit.

The companion document `doc/language-guide.md` is the long-form
reference; this is the LLM-shaped subset.

---

## Identifiers — the rule that bites first

Single-character lowercase identifiers (`a`–`z`) lex as **type
variables** (`a`–`q`) or **row variables** (`r`–`z`), not as binding
names. They cannot appear as `let` bindings or pattern binders.

```gallowglass
let p = 5            -- ParseError: expected TSnake, got TTypeVar 'p'
let pp = 5           -- OK
λ x → x              -- ParseError on the binder x
λ xx → xx            -- OK

match v {
  | Some x → x       -- ParseError on `x`
  | Some xx → xx     -- OK
  | Some _ → 0       -- OK (wildcard)
  | Some pred → pred -- OK (multi-char binder)
}
```

Snake_case names are at least two characters or include `_`.

---

## Declarations vs let-in expressions

A top-level `let foo = …` is a **declaration** — no `in`.
An inline `let x = … in body` is an **expression** — `in` is required.

```gallowglass
-- Top-level (declaration form)
let twice : Nat → Nat = λ n → n + n

-- Inline (expression form)
let total = let aa = 5 in aa + 1   -- aa : Nat = 5; total = 6
```

The bootstrap parser does not implicitly insert `in`. Without it,
the parser absorbs the next token as part of the rhs and surfaces a
typecheck error like `cannot unify 'Nat' with '… → ?9'`.

---

## Function types and lambdas

```gallowglass
let add : Nat → Nat → Nat = λ aa bb → aa + bb
let add_one : Nat → Nat   = λ n → n + 1

-- Multi-param lambdas are sugar for nested unaries.
-- λ a b → expr  ≡  λ a → λ b → expr.
```

Type annotations are documentation + a constraint. Compiler infers
them when omitted.

---

## Algebraic types

```gallowglass
type Coin =
  | Heads
  | Tails

type Option a =
  | None
  | Some a

type Pair a b =
  | MkPair a b

type Tree a =
  | Leaf
  | Node (Tree a) a (Tree a)
```

Constructor names are the data values (or, when they take arguments,
constructor functions). Each constructor in a type gets a tag
0..N-1 in declaration order.

---

## Pattern matching

```gallowglass
let coin_value : Coin → Nat
  = λ c → match c {
      | Heads → 1
      | Tails → 0
    }

let option_default : Nat → Option Nat → Nat
  = λ d opt → match opt {
      | None    → d
      | Some xx → xx
    }
```

Match arms are constructor + binder patterns. `_` is the wildcard.
Nat literal patterns work too:

```gallowglass
let is_zero : Nat → Bool
  = λ n → match n {
      | 0 → True
      | _ → False
    }
```

A non-zero Nat pattern with a binder gets the **predecessor**, not
the value:

```gallowglass
let is_pos : Nat → Bool
  = λ n → match n {
      | 0 → False
      | k → True   -- k is bound to (n - 1), not used here
    }
```

Exhaustiveness checking errors at compile time on missing arms.

---

## Recursion via `fix`

The bootstrap compiler does not support self-recursive `let`.
Introduce a self-reference with `fix`:

```gallowglass
let factorial : Nat → Nat
  = fix λ self n → match n {
      | 0 → 1
      | _ → n * (self (sub n 1))   -- assuming a `sub` is in scope
    }
```

`fix λ self … → body` makes `self` available inside `body` as the
function being defined.

---

## Imports

```gallowglass
use Core.Pair                              -- qualified: Pair.MkPair
use Core.Pair unqualified { MkPair }       -- bare: MkPair
use Core.Pair unqualified { Pair, MkPair } -- bring type AND ctor
```

Without an `unqualified` clause, names are accessible as
`<short module name>.<name>`.

---

## Effects and handlers

Effects appear in type signatures. Pure functions have empty rows:

```gallowglass
-- Pure: empty row implicit
let double : Nat → Nat = λ n → n + n

-- Effectful: explicit row
external mod Reaver.RPLAN { output : Nat → Nat }

let greet : Nat → {RPLAN} Nat
  = λ _ → Reaver.RPLAN.output 42
```

`External` is the standard effect for VM boundary crossings.
Handlers discharge effects locally:

```gallowglass
handle computation {
  | return xx     → xx
  | raise ee k    → default_value
}
```

`Abort` is *not* in any effect row — it's unhandleable and propagates
to the VM supervisor.

---

## Typeclasses

```gallowglass
class Eq a {
  let eq : a → a → Bool
  let neq : a → a → Bool
  let neq = λ aa bb → not (eq aa bb)   -- default method
}

instance Eq Nat {
  eq = nat_eq
}

instance Eq a => Eq (List a) {
  eq = λ xs ys → match xs { … }
}
```

Constraint syntax: `Eq a =>`. The bootstrap supports single-class
constraints; dual-class (`(Eq a, Show a) =>`) is forward work.

Class-method dispatch only fires through a constrained `let`, not at
bare call sites:

```gallowglass
-- works:
let display : ∀ a. Show a => a → Text = λ x → show x
display 42
-- fails: bare `show 42` surfaces as `unbound variable 'Core.Text.show'`
```

---

## Show / Debug

```gallowglass
use Core.Text { Show, show }

show 42         -- "42"
show True       -- "True"
show "hi"       -- "\"hi\""
```

`Show` is for users (`MkPair 3 7` → `"(3, 7)"`); `Debug` is for
developers (`MkPair 3 7` → `"MkPair 3 7"`). The Jupyter kernel's
type-driven renderer uses `con_info` directly rather than `Show`,
so compound values render as `MkPair 3 7` regardless of which
instance is in scope.

---

## Where clauses and operator sections

```gallowglass
let hypotenuse : Nat → Nat → Nat
  = λ aa bb → result
    where { sq_a = aa * aa
          ; sq_b = bb * bb
          ; result = sq_a + sq_b
          }

let incremented = map (+ 1) my_list   -- (+ 1) is a section
let halved      = map (/ 2) my_list
```

`where` clauses are layout-sensitive after `where {` (semicolons
separate bindings).

---

## Programmer pins

`@name = expr` content-addresses an expression: it gets evaluated
once and bound to `name` as a Pin. Subsequent references reuse the
pinned value via its hash.

```gallowglass
@compiled_table = expensive_setup ()
let lookup = λ key → table_get compiled_table key
```

Pins are how cross-binding deduplication works in the emit layer.

---

## Common pipeline

The bootstrap compiler runs lex → parse → scope → typecheck →
codegen → emit. Each phase produces a structured error with
file:line:col on failure, never a bare message. To inspect what
your code compiles to, use Glass IR:

```python
from bootstrap.glass_ir import render_module, render_fragment
```

`render_module` shows the whole module; `render_fragment` shows one
definition with its pin hash and dep list.

---

## Things the bootstrap does *not* yet support

- Self-recursive top-level `let` — use `fix`.
- Dual-class constraints `(C a, D a) =>` — single class only.
- Class-method dispatch at bare call sites — wrap in a constrained let.
- `Show a => Show List` instances fully reducing — type-driven
  renderer in the Jupyter kernel papers over this.
- Inline assembly / raw PLAN tree literals — use `Core.PLAN.*`
  externs.
- Tail-call optimization — Reaver's evaluator is naive recursion;
  deep recursion crashes (Python harness ~100K frames; Reaver ~tens
  of thousands).
- Nested list patterns in `match` — depth-1 only.
- Records with type-class derivation (`deriving (Eq, Show)`).

Forward work tracked in `ROADMAP.md`.

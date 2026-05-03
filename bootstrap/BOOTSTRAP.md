# Bootstrap Compiler

**Language:** Python 3.11+
**Target:** PLAN seed files (`*.seed`)
**Input:** Restricted Gallowglass dialect

This document describes the scope and restricted dialect of the
Gallowglass bootstrap compiler.

The bootstrap compiler's sole purpose is to compile enough Gallowglass to write
the core prelude and, eventually, the self-hosting compiler. It is thrown away
once the self-hosting compiler is operational.

---

## 1. Implementation

The bootstrap compiler is written in Python and lives in `bootstrap/*.py`. It
compiles the restricted Gallowglass dialect directly to PLAN values, then serializes
those values to the PLAN seed format understood by `x/plan` (xocore-tech/PLAN).

```
bootstrap/
  lexer.py       ← Bytes → token list
  parser.py      ← Token list → AST
  scope.py       ← Scope resolution: qualified names, module namespacing
  typecheck.py   ← Restricted HM unification
  codegen.py     ← AST → PLAN values (de Bruijn, constructors, pattern match)
  emit.py        ← PLAN value → seed bytes (spec/07-seed-format.md)
  glass_ir.py    ← Debug renderer: PLAN values → Glass IR text
  ast.py         ← AST node definitions
```

### 1.1 Why Python

The original design called for a bootstrap compiler in Sire (PLAN's macro/assembly
language). Python was used instead because:

- The Python compiler was built first as a prototype, and is fully functional.
- It produces valid seed bytes loadable by `x/plan`.
- It serves as the cross-compiler for the prelude and the self-hosting
  compiler: compile Gallowglass in Gallowglass, emit the seed, run natively
  on `x/plan`.
- Sire is harder to write and the bootstrap is thrown away anyway.

The Sire outlines have been removed from the tree; git history preserves them
for anyone curious about the never-executed stubs.

### 1.2 The Bootstrap Path

The Python compiler is a **cross-compiler**: it runs on the developer's machine and
produces seed files that run on the PLAN VM. The self-hosting path is:

```
1. Python compiler compiles restricted Gallowglass → seed bytes
2. Validate seeds run correctly on the PLAN runtime
3. Write the self-hosting Gallowglass compiler in restricted Gallowglass
4. Python compiler compiles it → compiler.seed
5. The runtime executes compiler.seed — native Gallowglass compiler on PLAN
6. compiler.seed compiles itself → true self-hosting
```

No Sire step required.

---

## 2. Restricted Dialect

The bootstrap compiler implements a strict subset of the full Gallowglass surface
syntax (spec/06-surface-syntax.md).

### 2.1 Included

| Feature | Notes |
|---|---|
| `let` bindings | Top-level and local, non-recursive and recursive |
| `λ` expressions | Anonymous functions, multi-argument |
| Function application | Left-associative, juxtaposition |
| `type` declarations | Sum types (`\|` constructors) only; no record syntax |
| `match` expressions | Nat, nullary constructor, unary/binary constructor, and tuple patterns |
| `if`/`then`/`else` | Bool dispatch via opcode 2 |
| Nat, Bool, Text, Bytes literals | Primitive literals |
| `pin` expression | Programmer-controlled pinning |
| Basic type annotations | Monomorphic and simple polymorphic (`∀ a.`) |
| `external mod` declarations | VM boundary for Core.* primitives |
| `fix` expressions | Self-referential anonymous recursion |
| Tuples | Binary tuples `(a, b)`; tuple patterns in match |
| Mutual recursion | Lexicographically-ordered SCC compilation via shared-pin rows |
| `eff` declarations | Effect ops compile to 3-arg CPS laws; `handle`/do supported |
| `handle` expressions | CPS dispatch: `comp dispatch_fn return_fn` |
| do-notation (`x ← rhs in body`) | CPS bind: lambda-lifts continuation over captured locals |
| `pure v` | No-op CPS computation: `λ dispatch k → k v`; terminates do chains |

### 2.2 Excluded (deferred to self-hosting compiler)

| Feature | Reason |
|---|---|
| Effect rows in types | Parsed, never unified |
| Contracts (`\| pre`, `\| post`) | No solver in bootstrap |
| `module` / `import` | Multi-file compilation deferred |
| Typeclasses | Deferred |
| Row-polymorphic records | Deferred |

### 2.3 Effect Handling

The bootstrap type checker ignores effect rows. Functions may include effect
annotations (the parser accepts them), but the type checker treats all types as if
the annotation were absent. This is sound for the bootstrap dialect.

### 2.4 Restricted-dialect idioms

The bootstrap compiler accepts a strict subset of Gallowglass; the rules below
cover the patterns that work today and the workarounds for forms the bootstrap
codegen does not yet handle.

#### 2.4.1 Variant types: mixed-arity sum types are supported

The bootstrap codegen handles ADTs with constructors of mixed arities
directly. Three different arities in the same match work:

```gallowglass
type Tree =
  | Leaf                   -- arity 0
  | Node   Nat             -- arity 1
  | Branch Tree Tree       -- arity 2

let depth : Tree → Nat
  = λ tt → match tt {
      | Leaf       → 0
      | Node nn    → 1
      | Branch a b → 2
    }
```

This was historically a footgun — earlier bootstrap revisions silently
returned `<0>` (P(0)) for the binary arm under certain tag layouts.
F11 (post-feedback) fixed the last such case (`first_tag > 0` in the
inner tag dispatch); see the regression suite at
`tests/bootstrap/test_codegen.py::test_match_mixed_arity_*` and
`test_match_nullary_unary_binary_*`. The `demos/calculator.gls`
`eval` function exercises the same pattern end-to-end.

##### Older idiom: single-constructor record + Nat tag

`Compiler.gls` was written before mixed-arity matches were reliable, so it
encodes every AST node as a single-constructor record with a Nat tag and a
payload nested in `Pair`s, then dispatches with an `if (nat_eq tag K)`
chain:

```gallowglass
type Effect = | MkEffect Nat (Pair Nat (Pair Nat Nat))
--                       tag    a       (b   c)

let interpret : Effect → Bytes
  = λ ee → match ee {
      | MkEffect tag pp →
          if nat_eq tag 0 then handle_point pp
          else if nat_eq tag 1 then handle_dns (pair_fst pp)
          else handle_xfer (pair_fst pp)
    }
```

New code can use the direct sum-type form above. The tagged-record idiom
remains useful when (a) several variants share the same payload shape, or
(b) you want the tag to be statically computable and inspectable.

#### 2.4.2 Recursive Bool dispatch

For recursion controlled by a `Bool`, both `if-then-else` and `match` dispatch
work correctly: the bootstrap codegen wraps non-base branches in Pin'd thunk
laws so neither branch is forced before the condition is evaluated. Use whichever
reads more naturally:

```gallowglass
-- Either form is fine:
let mod_go : Nat → Nat → Nat
  = λ aa bb → if (lte bb aa) then mod_go (sub aa bb) bb else aa

let mod_go : Nat → Nat → Nat
  = λ aa bb → match (lte bb aa) {
      | False → aa
      | True  → mod_go (sub aa bb) bb
    }
```

(Earlier bootstrap revisions evaluated both `if-then-else` branches eagerly.
That is fixed; current revisions defer both branches.)

#### 2.4.3 Wildcard succ-arm captures

`match nn { | 0 → base | _ → body_using_outer_locals }` lambda-lifts outer
captures and the enclosing function's self-reference into the wildcard arm.
PatVar (`| _kk → ...`) and PatWild (`| _ → ...`) succ arms are equivalent for
the purpose of capture lifting; PatVar additionally binds the predecessor to
the named variable.

#### 2.4.4 Effect handlers that resume with constructor values

Handler op arms can pass any value to the resumed continuation, including
constructor applications. The continuation is just a PLAN value (`kk val` is
`App(kk, val)`), so resuming with a `Some x`, `MkPair a b`, or `Ok v` works
with no special ceremony. The downstream do-bind binds the constructor value
to its variable; pattern-matching on it happens wherever the bound variable
flows next — typically in the `return` arm of the same handler.

```gallowglass
type MyOpt = | MyNone | MySome Nat

eff Lookup {
  lookup : () → MyOpt
}

let comp = oo ← lookup () in pure oo

-- Handler resumes with `MySome 99`; the return arm pattern-matches it.
let extracted = handle comp {
  | return rr → match rr { | MyNone → 0 | MySome xx → xx }
  | lookup _ kk → kk (MySome 99)
}
-- extracted = 99
```

The same pattern works for any constructor — unary (`Some`, `Ok`), binary
(`MkPair`), or single-constructor records. See
`tests/bootstrap/test_coverage_gaps.py::TestEffectHandlerEdgeCases` for the
worked test cases (F6).

#### 2.4.5 Known sharp edges that still bite

These are not bugs in the codegen — they're operational quirks of the
Python harness or current restrictions of the demo build path. Each
field-tested complaint from `feedback_for_gallowglass.md` not already
covered in §§2.4.1–2.4.4 lives here.

##### Recursion-limit guidance

The Python harness evaluator (`dev/harness/plan.py`) uses recursive
`evaluate`/`apply` calls. Each PLAN-level reduction step costs one
Python frame, and laziness combinators inflate the cost: a list of
length N processed by `foldl`/`foldr`/`map`/`append` typically costs
~10–20 Python frames per cell.

Concrete bumps observed in practice (these numbers are calibrated
against the demos in `demos/` and are reasonable upper bounds):

| Workload                                         | `sys.setrecursionlimit` |
|--------------------------------------------------|------------------------:|
| Simple arithmetic, ≤100 list cells               | default (1 000)         |
| Recursive numeric (factorial, Fibonacci ≤30)     | 10 000                  |
| `Compiler.gls`-class computations (~500 defs)    | 100 000                 |
| Multi-fold over large nested lists (e.g. 3-deep) | 200 000                 |

The harness evaluator additionally raises `RecursionError` past
`EVALUATE_DEPTH_LIMIT` (`dev/harness/plan.py`, currently 10 000) and
`bevaluate` past `BEVALUATE_DEPTH_LIMIT` (100 000). Both are PLAN-level
counts and trip well after Python's own limit fires for typical
programs — but if your program is deep-but-narrow you may hit them
first. The fix in either case is the same: bump
`sys.setrecursionlimit` *and* the relevant depth limit, or rewrite
the algorithm to be tail-iterative.

The proper post-1.0 fix is jetting the bread-and-butter combinators
(`length`, `map`, `foldl`, `foldr`, `append`, `concat_list`) so they
collapse to a single native step in `dev/harness/bplan.py` instead of
a per-cell PLAN reduction. Several of those are already jetted; check
`bplan.py`'s `_PRELUDE_JETS` registry to see the current set.

##### Demos and `use` imports

`demos/*.gls` files compile in isolation: the test harness invokes
the bootstrap with `module_env={}`, so even though the prelude
modules compile, they aren't in scope. Each demo redefines
`add`/`mul`/`map`/`foldl`/etc. inline at the top.

This is a fixable shortcoming of the demo harness (the multi-module
build driver is in place; the demo runner just doesn't thread a
prebuilt `module_env` through). For now: copy the utilities you need from
`Compiler.gls`'s prelude inlining at lines 25–205, or from the
relevant `Core/*.gls` source. Keeping demo source self-contained also
makes each demo readable as a single file without cross-references.

#### 2.4.6 Reading existing code

`Compiler.gls` (the self-hosting compiler in `compiler/src/`) is ~3000 lines
of restricted-dialect Gallowglass and is the canonical reference for "how do
I write \<X\>?" questions. It exercises every pattern in this section: the
tagged-record idiom for variants, recursive Bool dispatch for the lexer's
state machine, wildcard succ-arm captures throughout, effect handlers for
the parser's error-reporting style, mutual recursion via shared-pin SCCs,
and typeclass dictionary passing.

Lines 25–205 cover the tagged-record encoding for AST nodes — start there
for variant-type questions. The full prelude inlining at the top of the
file also serves as a worked reference for the dialect-safe idioms.

For shorter examples, the working demos in `demos/` (and `demos/README.md`)
are calibrated to teach one concept at a time.

---

## 3. Pipeline

```
Source text
    │
    ▼  bootstrap/lexer.py
Token list
    │
    ▼  bootstrap/parser.py
AST (bootstrap/ast.py)
    │
    ▼  bootstrap/scope.py
Qualified AST
    │
    ▼  bootstrap/typecheck.py   (restricted HM, effects ignored)
Typed AST
    │
    ▼  bootstrap/codegen.py     (de Bruijn, constructors, opcode dispatch)
PLAN values (dict[fq_name → PLAN value])
    │
    ▼  bootstrap/emit_seed.py        (spec/07-seed-format.md)
Seed bytes
    │
    ▼  PLAN runtime
Result
```

The Python dev harness (`dev/harness/`) provides a pure-Python PLAN evaluator for
unit testing without requiring an external runtime. Seeds also validate against
the Reaver runtime in `tests/reaver/`.

---

## 4. Capabilities

The bootstrap compiler implements the full pipeline lex → parse → scope →
typecheck → codegen → emit, plus Glass IR rendering. Test suites live in
`tests/bootstrap/` and `tests/compiler/`.

- **Lexer / parser / scope resolver:** the restricted dialect. Scope resolver
  qualifies all `EVar` references to FQ `Module.name` nats.
- **Type checker:** Hindley-Milner with let-generalization, SCC-ordered
  checking via Tarjan, mutual recursion handled before generalization.
  Effect rows are parsed but unified.
- **Codegen:** restricted dialect → PLAN values, including pattern matching
  on Nat / nullary / unary / binary constructors and tuples, mutual
  recursion via shared-pin SCCs, `fix` expressions, effect handler CPS
  transform (3-arg dispatch laws, do-notation, `pure`, per-effect tag
  namespacing), open-continuation shallow handlers, typeclass dictionary
  insertion, default methods, constrained instances, multi-module builds
  with cross-module instances.
- **Emit:** PLAN values → seed bytes (binary, legacy) and → Plan Assembler
  text (`bootstrap.emit_pla`, the production Reaver path).
- **Glass IR:** type-annotated AST renderer with FQ names, pin hashes, SCC
  groups, and round-trip verification.

The self-hosting compiler in `compiler/src/Compiler.gls` is validated via
the BPLAN harness: the Python bootstrap compiles `Compiler.gls`, the
resulting GLS `emit_program` processes the full module, and the output
matches the Python emitter byte-for-byte.

---

## 5. Invariants

- **Canonical SCC order is lexicographic.** Deviation silently changes PinIds.
- **No compile-time evaluation.** The compiler produces un-reduced PLAN terms.
  Exception: constant-folding of literal Nat arithmetic is permitted.
- **One module per file.** The bootstrap has no multi-module file support yet.
- **Error messages include source locations.** Every diagnostic includes `file:line:col`.
  This applies to all four error classes — `ParseError`, `ScopeError`,
  `TypecheckError`, `CodegenError` — and to any new error class added
  later. When introducing a new `raise SomeError(...)` site, plumb a
  `Loc` through unless the error truly arises from a compiler-internal
  invariant (in which case the bare-message form is acceptable). The
  field-feedback report flagged the absence of source locations on
  codegen errors as the single biggest UX gap; F2 closed that gap and
  the property must be preserved as new diagnostics get added.

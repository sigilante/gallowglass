# Self-Hosting Compiler

**Language:** Restricted Gallowglass dialect (same as prelude)
**Input:** Restricted Gallowglass source text
**Output:** Plan Assembler text
**Target:** Self-hosting — the compiler compiles itself

This document describes the architecture and implementation constraints for the
Gallowglass self-hosting compiler.

---

## 1. Goal

Write the bootstrap compiler's functionality in restricted Gallowglass, compile
it with the Python bootstrap compiler, run the resulting program on the PLAN
runtime, and confirm it produces the same output as the Python compiler. The
compiler then compiles itself: the self-hosting build accepts its own source
and emits an equivalent program.

Once full self-host validation is achieved, the Python bootstrap is discarded.

---

## 2. Architecture

Same pipeline as the Python bootstrap:

```
Source bytes  (read from stdin or a file)
    │
    ▼  Lexer        bytes → List Token
Token list
    │
    ▼  Parser       List Token → Expr (AST)
AST
    │
    ▼  Scope        Expr → Expr  (qualify names, resolve module prefix)
Resolved AST
    │
    ▼  Codegen      Expr → PlanVal  (de Bruijn, Case_, law construction)
PLAN values
    │
    ▼  Emit         PlanVal → Bytes  (seed format, spec/07-seed-format.md)
Seed bytes    (written to stdout)
```

Type checking is **deferred**. The restricted dialect's type checker is
straightforward HM, but union-find requires mutable state that is awkward in
pure PLAN. The self-hosted compiler trusts well-typed input; a type checker
is forward work, after self-hosting is confirmed end-to-end.

---

## 3. Key Types

All types are algebraic, encoded using the restricted dialect's constructor
encoding (nullary = bare Nat tag, unary = App(tag, field), binary = App(App(tag,
field1), field2)).

### 3.1 Token

```gallowglass
type Token =
  -- Atoms
  | TkNat   Nat          -- nat literal
  | TkText  Text         -- text literal
  | TkIdent Text         -- identifier or keyword
  -- Punctuation
  | TkLParen             -- (
  | TkRParen             -- )
  | TkLBrace             -- {
  | TkRBrace             -- }
  | TkBar                -- |
  | TkArrow              -- →
  | TkBackArrow          -- ←
  | TkEqual              -- =
  | TkColon              -- :
  | TkDot                -- .
  | TkComma              -- ,
  | TkAt                 -- @
  | TkBacktick           -- `
  -- Keywords (lexed from TkIdent by the parser)
  | TkLet | TkType | TkMatch | TkIf | TkThen | TkElse
  | TkExternal | TkMod | TkPin | TkForall
  -- Structural
  | TkEof
  | TkErr Text Nat Nat   -- error: message, line, col
```

### 3.2 AST (Expr)

```gallowglass
type Expr =
  | EVar  Text                     -- variable reference
  | EApp  Expr Expr                -- application
  | ELam  Text Expr                -- lambda
  | ELet  Text Expr Expr           -- local let
  | ENat  Nat                      -- nat literal
  | EText Text                     -- text literal
  | EIf   Expr Expr Expr           -- if/then/else
  | EMatch Expr (List MatchArm)    -- pattern match
  | EPin  Expr                     -- programmer pin

type MatchArm =
  | ArmNat  Nat Expr               -- | 0 → e
  | ArmVar  Text Expr              -- | k → e (wildcard, binds pred/scrutinee)
  | ArmCon  Text (List Text) Expr  -- | Con x y → e

type Decl =
  | DLet  Text Expr                -- top-level let
  | DType Text (List ConDef)       -- type declaration
  | DExt  Text (List ExtItem)      -- external mod declaration

type ConDef  = { name : Text, arity : Nat }
type ExtItem = { name : Text }
```

### 3.3 PLAN Value (output of codegen)

```gallowglass
type PlanVal =
  | PNat Nat
  | PApp PlanVal PlanVal
  | PLaw Nat Nat PlanVal    -- arity, name-as-nat, body
  | PPin PlanVal
```

### 3.4 Compilation Environment

```gallowglass
-- Maps: represented as sorted association lists (Text, PlanVal)
-- Linear search is acceptable for the bootstrap's module sizes.
type Env = { globals : List (Text, PlanVal), locals : List (Text, Nat), arity : Nat }
```

---

## 4. External Mods Required

The self-hosting compiler needs BPLAN jets for I/O and byte operations. These
are declared with `external mod` and compile to real planvm opcode pins (for
`Core.PLAN`) or opaque sentinels resolved by planvm's jet registry.

```gallowglass
external mod Core.PLAN {
  inc     : Nat → Nat               -- opcode 2
  force   : ∀ a. a → a              -- opcode 4
}

external mod Core.Nat {
  sub     : Nat → Nat → Nat         -- saturating subtraction
  div     : Nat → Nat → Nat
  mod_    : Nat → Nat → Nat
  eq      : Nat → Nat → Bool
  lt      : Nat → Nat → Bool
  to_bytes : Nat → Nat → Bytes      -- n bytes, little-endian
}

external mod Core.Bytes {
  length  : Bytes → Nat
  at      : Bytes → Nat → Nat       -- byte at index (0 if OOB)
  slice   : Bytes → Nat → Nat → Bytes   -- start, length
  concat  : Bytes → Bytes → Bytes
  eq      : Bytes → Bytes → Bool
}

external mod Core.Text {
  to_bytes   : Text → Bytes
  from_bytes : Bytes → Text         -- assumes valid UTF-8
  length     : Text → Nat           -- byte length
  concat     : Text → Text → Text
  eq         : Text → Text → Bool
}

external mod Core.IO {
  read_stdin   : Unit → Bytes
  write_stdout : Bytes → Unit
  write_stderr : Text → Unit
}
```

**Opcode mapping for Core.Nat, Core.Bytes, Core.Text, Core.IO:** These map to
planvm opcodes 5–30+ (see `vendor/PLAN/planvm-amd64/plan.s prim.tab`). The
Python bootstrap currently emits opaque sentinels for these; CI validates seed
loading only. The self-hosting compiler will actually invoke them at runtime on
`x/plan`.

When extending the runtime surface, map FQ names to opcode numbers in
`Compiler._CORE_PLAN_OPCODES` (or an analogous table). Verify each mapping
against the runtime's primitive table before coding compiler logic that
depends on it.

---

## 5. Architectural Decision: Single-File Compiler

The restricted dialect has no cross-module imports. All phases are therefore
implemented in a single file: `compiler/src/Compiler.gls`. The "separate files"
layout described in §8 below is aspirational; the actual implementation is one
monolithic file with clearly marked sections.

---

## 6. Pipeline Layout in `Compiler.gls`

The full pipeline lives in a single file. Major sections:

- **Utilities:** Nat arithmetic, bitwise ops, list ops, association list, byte
  ops, name encoding.
- **Lexer:** `lex : Bytes → Nat → Nat → Nat → List Token` — byte classifiers,
  lexpos helpers, whitespace/comment skipping, identifier/nat/text scanning,
  single-token dispatch.
- **Parser:** `parse_program : List Token → List Decl` — recursive descent
  with the pass-self pattern (`pe`) to break pseudo-mutual recursion.
  Produces `List Decl` with `DLet`, `DType`, `DExt` nodes.
- **Scope resolver:** integrated with the codegen section. Pre-passes over
  `List Decl` to collect top-level names; walks each `DLet` body to qualify
  bare-name `EVar` references to `Module.name` nats. Constructors from
  `DType` are already FQ-emitted by the parser; `DExt` items are registered
  as opaque globals.
- **Codegen:** `compile_program : List Decl → Nat → List (Pair Nat PlanVal)`.
  Three-pass compilation: DType (constructor registration), DExt (external
  opcode binding), DLet (function compilation). Key implementation notes:
  - **pred_env bootstrap limitation:** when a `match` has multiple
    App-bearing constructor arms and outer lambda params are referenced in
    arm bodies, arms[1+] are compiled in a fresh `pred_env` with no locals.
    Workaround: use nat_eq chain dispatch with single-arm helper functions.
  - **Pass-self pattern:** `ce` parameter threads `cg_compile_expr` through
    all compile functions to break pseudo-mutual recursion.
  - **`fn` is a reserved word** (ASCII alias for `λ`): use `fname` instead.
  - **Forward reference rule:** every function called by another must be
    defined *before* it in the file (no forward refs in the restricted
    dialect).
- **Plan Assembler emitter:** `emit_program` — `List (Pair Nat PlanVal)` →
  `Bytes` (Plan Assembler text per `spec/07-seed-format.md §13`). Tests do
  byte-level comparison against `bootstrap/emit_pla.py` for the same
  PlanVal inputs.
- **Driver:** `main : Bytes → Bytes` chains lex → parse_program →
  compile_program → emit_program. Module name hardcoded to `"Compiler"`
  (`nn_Compiler = 8243113893085146947`, i.e. `int.from_bytes(b'Compiler',
  'little')`).

The `compile_program` entry point is exported and verified to compile end-to-end.

### Self-hosting validation

Validation runs in two paths:

- **BPLAN harness** (active): the Python bootstrap compiles `Compiler.gls`,
  the `plan2pv` bridge converts each PLAN value to a GLS `PlanVal` ADT value,
  and GLS `emit_program` (BPLAN jets) processes the list to Plan Assembler
  bytes. Assertions verify the output is non-empty, starts with `(#bind "`,
  has the correct bind count (one per definition), contains the
  `Compiler.main` binding, and that all lines are bind forms.
- **RPLAN self-host on Reaver** (forward work, see `ROADMAP.md`): runs the
  compiled compiler under Reaver on its own source and asserts byte-identical
  output vs the BPLAN harness. This closes the self-hosting gate end-to-end.

---

## 7. Evaluation Testing

Codegen tests compare compiled PLAN values against the Python bootstrap output
using the Python harness — functional equivalence at the PLAN value level.
Byte-identical self-hosting output is a stronger gate than seed loading: if
the compiler's output, run on Reaver over its own source, produces the same
bytes as the Python bootstrap over the same source, runtime execution is
semantically correct for the full compiler workload.

---

## 8. Constraints and Limitations

All constraints from the restricted dialect (`bootstrap/BOOTSTRAP.md §2`) apply:

- No cross-module imports (each file compiled independently)
- No mutual recursion (each SCC is a single definition)
- No typeclasses (explicit dictionary passing)
- `handle`/`eff`/`pure`/`run`/`do` expressions supported
- `use` declarations parsed but treated as no-ops
- No tuples (encode as unary/binary constructors)
- Pattern match: Nat literals, wildcard, constructor patterns only

Additional self-hosting constraints:
- **No type checking** (deferred — see §2)
- **Association-list maps only** (no BST until `Core.Map` is available)
- **No text interpolation** (build error messages by concatenation)

---

## 9. File Layout

```
compiler/
  COMPILER.md          ← this file
  CODEGEN_PLAN.md      ← detailed codegen implementation plan
  src/
    Compiler.gls       ← single monolithic file: all phases
                          utilities → lexer → parser → codegen
                          (with scope resolver integrated) → Plan Assembler
                          emitter → main driver

tests/compiler/
  test_utils.py        ← utility tests
  test_lexer.py        ← lexer tests
  test_scope.py        ← scope tests
  test_emit.py         ← Plan Assembler emitter tests
  test_driver.py       ← driver tests
  test_selfhost.py     ← self-hosting validation
  test_m11.py          ← GLS DeclClass/DeclInst (typeclass) tests
  test_m12_effects.py  ← GLS effects + DeclUse tests
```

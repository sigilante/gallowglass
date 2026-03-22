# Self-Hosting Compiler

**Phase:** Milestone 8
**Language:** Restricted Gallowglass dialect (same as prelude)
**Input:** Restricted Gallowglass source text
**Output:** PLAN seed bytes
**Target:** Self-hosting — the compiler compiles itself

This document describes the architecture, sub-milestone breakdown, and
implementation constraints for the Gallowglass self-hosting compiler.

---

## 1. Goal

Write the bootstrap compiler's functionality in restricted Gallowglass, compile
it with the Python bootstrap compiler, run the resulting seed on `x/plan`, and
confirm it produces the same output as the Python compiler. The compiler then
compiles itself: `compiler.seed` accepts its own source and emits an equivalent
`compiler.seed`.

This is the 1.0 milestone. Once achieved, the Python bootstrap is discarded.

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

Type checking is **deferred** from Milestone 8. The restricted dialect's type
checker is straightforward HM, but union-find requires mutable state that is
awkward in pure PLAN. The self-hosted compiler trusts well-typed input for M8;
the type checker is added in M8.1 or M9 after self-hosting is confirmed.

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

**Action required before M8.3:** Extend `Compiler._CORE_PLAN_OPCODES` (or add a
new table) to map the above FQ names to their actual planvm opcode numbers.
Verify each mapping against `plan.s prim.tab` before coding the compiler logic
that depends on it.

---

## 5. Sub-Milestones

### Milestone 8.1: Opcode table + prelude extension

Extend the codegen's opcode mapping to include `Core.Nat.sub`, `Core.Nat.eq`,
`Core.Nat.lt`, `Core.Bytes.*`, `Core.Text.*`, `Core.IO.*`. Verify each maps to
the correct planvm opcode number from `plan.s`. Add harness tests confirming
correctness of each new external in the Python environment.

Also extend `prelude/src/Core/` with any ops needed by the compiler itself:
- `Core.Nat`: sub, div, mod, byte-encoding ops
- `Core.Text`: basic string equality and comparison

### Milestone 8.2: Lexer (`compiler/src/Lexer.gls`)

Input: `Bytes` (raw source). Output: `List Token`.

The lexer is a recursive function over a byte offset. At each step it inspects
the current byte (`Core.Bytes.at src offset`), classifies it, and emits a token:

```gallowglass
-- Skeleton (pseudocode — actual names TBD during implementation)
let lex : Bytes → Nat → Nat → Nat → List Token
  = λ src offset line col →
      if nat_eq offset (Core.Bytes.length src)
      then singleton TkEof
      else
        let byte = Core.Bytes.at src offset
        ...
```

Key lexer concerns:
- Nat literals: accumulate digits until non-digit
- Identifiers: accumulate while alphanumeric or `_`
- Unicode operators (`→`, `λ`, `∀`, `←`): multi-byte UTF-8 sequences; compare
  against known byte prefixes (all are 3-byte sequences in UTF-8)
- Comments: `--` to end of line, discard
- Line/col tracking for error messages

Tests: `tests/compiler/test_lexer.py` — compare output against Python bootstrap
lexer for a suite of source fragments.

### Milestone 8.3: Parser (`compiler/src/Parser.gls`)

Input: `List Token`. Output: `Expr` / `List Decl`.

Recursive descent following the restricted dialect grammar
(`spec/06-surface-syntax.md §restricted`). The parser carries a `List Token`
and returns `(result, List Token)` pairs (no mutable state).

```gallowglass
type ParseResult a = { value : a, rest : List Token }

let parse_expr : List Token → ParseResult Expr
let parse_decl : List Token → ParseResult Decl
let parse_prog : List Token → List Decl
```

Key parser concerns:
- Left-recursive application: parse a sequence of atoms, fold left into `EApp`
- Pattern match arms: `| Pat → Expr` where Pat is NatLit, Wildcard, Ident, or
  `Con x y`
- Type annotations: parsed but discarded (type checking deferred)

Tests: `tests/compiler/test_parser.py`.

### Milestone 8.4: Scope resolver (`compiler/src/Scope.gls`)

Input: `List Decl`. Output: `List Decl` with all names qualified.

The scope resolver:
1. Collects all top-level names into a global env (module-prefixed)
2. For each `DLet`, resolves free variables against globals; reports unbound
3. For `DType`, registers constructors with their tags and arities
4. For `DExt`, registers external items as global names

No cross-module imports in M8 (same constraint as the Python bootstrap). Each
file is compiled independently.

Association list (`List (Text, PlanVal)`) with linear search is adequate.

Tests: `tests/compiler/test_scope.py`.

### Milestone 8.5: Codegen (`compiler/src/Codegen.gls`)

Input: `List Decl`. Output: `List (Text, PlanVal)` (name → compiled PLAN value).

This is a port of `bootstrap/codegen.py` to restricted Gallowglass. The key
compilation rules (from `spec/04-plan-encoding.md`):

- **Variable reference** inside law body: de Bruijn index `N(k)` for locals;
  `P(val)` or quote form for globals
- **Lambda**: compile to `PLaw(arity, name_nat, body)` with Env(arity=1)
- **Application** in law body: `bapp(f, x) = A(A(N(0), f), x)`
- **Self-recursion**: when inside a law and referencing own FQ name → `N(0)`
- **Nat literals** in law body: quote form `A(N(0), N(k))`
- **if/then/else**: Case_ (opcode 3) with 6 args, id for pin/law/app branches
- **match on Nat**: Case_ with zero and succ branches; succ gets predecessor
- **match on constructor**: Case_ App branch extracts fields
- **Constructor application**: nullary = `PNat(tag)`, unary = `PApp(PNat(tag), field)`

Tests: `tests/compiler/test_codegen.py` — compare compiled PLAN values against
Python bootstrap output for each prelude definition.

### Milestone 8.6: Emitter (`compiler/src/Emit.gls`)

Input: `PlanVal`. Output: `Bytes` (seed format per `spec/07-seed-format.md`).

The seed format is a binary encoding of a PLAN value with a header and a pin
table. The emitter traverses the `PlanVal` tree, collects referenced pins, and
serializes in the required order.

Key encoding operations needed:
- `Core.Nat.to_bytes`: encode a nat as little-endian bytes of specified width
- `Core.Bytes.concat`: assemble the seed byte-by-byte

Tests: `tests/compiler/test_emit.py` — compare emitted bytes against Python
`emit.py` output.

### Milestone 8.7: Top-level driver (`compiler/src/Main.gls`)

Wire the pipeline together:

```gallowglass
let main : Unit → Unit
  = λ _ →
      let src     = Core.IO.read_stdin ()
      let tokens  = lex src 0 1 1
      let decls   = parse_prog tokens
      let resolved = resolve decls module_name
      let compiled = codegen resolved module_name
      let seed    = emit compiled target_name
      Core.IO.write_stdout seed
```

### Milestone 8.8: Self-hosting validation

1. Compile `compiler/src/Main.gls` with the Python bootstrap → `compiler.seed`
2. Run `x/plan compiler.seed < compiler/src/Main.gls > compiler2.seed`
3. Assert `compiler.seed == compiler2.seed` (byte-identical output)

Step 3 is the self-hosting test. If it passes, the Gallowglass compiler can
compile itself without the Python bootstrap.

---

## 6. Evaluation Testing Gap

The evaluation testing gap (CI only validates seed loading, not computation)
closes in Milestone 8 without requiring Reaver:

- **M8.5**: Codegen tests compare compiled PLAN values against Python bootstrap
  output using the Python harness (not planvm). This is functional equivalence
  at the PLAN value level.
- **M8.8**: Byte-identical self-hosting output is a stronger test than seed loading.
  If `compiler.seed` compiled by the Python bootstrap produces the same bytes as
  `compiler.seed` run on `x/plan` over the same source, the planvm execution is
  semantically correct for the full compiler workload.

When Reaver provides a CLI eval mode, add `tests/compiler/test_eval_planvm.py`
as a fourth tier: apply compiled seeds to arguments and assert outputs.

---

## 7. Constraints and Limitations

All constraints from the restricted dialect (`bootstrap/BOOTSTRAP.md §2`) apply:

- No cross-module imports (each file compiled independently)
- No mutual recursion (each SCC is a single definition)
- No typeclasses (explicit dictionary passing)
- No `handle` expressions (effects as external mods only)
- No tuples (encode as unary/binary constructors)
- Pattern match: Nat literals, wildcard, constructor patterns only

Additional M8 constraints:
- **No type checking** (deferred to M8.1 or M9)
- **Association-list maps only** (no BST until Core.Map is available)
- **No text interpolation** (build error messages by concatenation)

---

## 8. File Layout

```
compiler/
  COMPILER.md          ← this file
  src/
    Lexer.gls          ← bytes → token list
    Parser.gls         ← token list → AST
    Scope.gls          ← AST → qualified AST
    Codegen.gls        ← AST → PLAN values
    Emit.gls           ← PLAN values → seed bytes
    Main.gls           ← top-level driver

tests/compiler/
  test_lexer.py        ← harness + Python bootstrap comparison
  test_parser.py
  test_scope.py
  test_codegen.py      ← key test: PLAN value equivalence
  test_emit.py         ← byte-level seed equivalence
  test_selfhost.py     ← M8.8 self-hosting validation
```

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

## 5. Architectural Decision: Single-File Compiler

The restricted dialect has no cross-module imports. All phases are therefore
implemented in a single file: `compiler/src/Compiler.gls`. The "separate files"
layout described in §8 below is aspirational; the actual implementation is one
monolithic file with clearly marked sections.

---

## 6. Sub-Milestones

### Status as of 2026-04-06

| Milestone | Status | Location in `Compiler.gls` |
|-----------|--------|---------------------------|
| M8.1: Core utilities | **COMPLETE** | Sections 1–9 |
| M8.2: Lexer | **COMPLETE** | Sections 10–18 |
| M8.3: Parser | **COMPLETE** | Sections 19–23 |
| M8.4: Scope resolver | **COMPLETE** | Section (integrated) |
| M8.5: Codegen | **COMPLETE** | Section 24 |
| M8.6: Seed emitter | **COMPLETE** | Section 25 |
| M8.7: Driver/main | **COMPLETE** | Section 26 |
| M8.8: Self-hosting validation | **COMPLETE (Path B + Path A entry point)** | `tests/compiler/test_selfhost.py` |

M8.8 status:
- Path B (harness) complete — 17 tests pass.
- Path A CLI entry point (`run_main`) implemented in Section 27; 5 planvm-gated tests added.
  `run_main : Nat → Nat` takes argVec (planvm CLI arg → P(src_nat)), unpins to get
  src_nat, constructs Bytes, calls main, writes output to stdout via WriteOp (P(N(9))).
  Full byte-identical comparison runs under Docker CI (`make test-ci`).

The `compile_program : List Decl → Nat → List (Pair Nat PlanVal)` entry point is
exported and verified to compile. It performs three passes over a pre-resolved
`List Decl` (DType → DExt → DLet), emitting PLAN values.

---

### Milestone 8.1: Core utilities ✓ COMPLETE

Nat arithmetic, bitwise ops, list ops, association list, byte ops, name encoding.
All utility functions in `compiler/src/Compiler.gls` sections 1–9.
Tested via Python harness in `tests/compiler/test_utils.py`.

### Milestone 8.2: Lexer ✓ COMPLETE

`lex : Bytes → Nat → Nat → Nat → List Token` — sections 10–18.
Byte classifiers, lexpos helpers, whitespace/comment skipping, identifier/nat/text
scanning, single-token dispatch, main lex loop.
Tested in `tests/compiler/test_lexer.py`.

### Milestone 8.3: Parser ✓ COMPLETE

`parse_program : List Token → List Decl` — sections 19–23.
Recursive descent with pass-self pattern (`pe`) to break pseudo-mutual recursion.
Produces `List Decl` with `DLet`, `DType`, `DExt` nodes.

### Milestone 8.4: Scope resolver ✓ COMPLETE

**Input:** `List Decl` (unresolved names as bare nats).
**Output:** `List Decl` with all variable references qualified to `Module.name` nats.

Algorithm (mirrors `bootstrap/scope.py`):
1. Pre-pass over `List Decl`: collect all top-level names (DLet names, DType
   constructor names, DExt items) into a global name table.
2. For each `DLet` body: walk the `Expr` tree replacing bare-name `EVar` with
   the corresponding fully-qualified name nat.
3. Constructors from `DType` are already emitted as FQ names by the parser
   (they are registered in the global env by the codegen pass 1). The scope
   resolver just needs to resolve `EVar` references in `DLet` bodies.
4. `DExt` items: registered as opaque globals (codegen handles opcode mapping).

**Data structure:** `List (Pair Nat Nat)` — bare name nat → FQ name nat.

**No cross-module imports.** Only names defined in the current file are in scope,
plus the module name prefix itself.

Tests: `tests/compiler/test_scope.py` — 15 passed.

### Milestone 8.5: Codegen ✓ COMPLETE

`compile_program : List Decl → Nat → List (Pair Nat PlanVal)` — section 24.

Full port of `bootstrap/codegen.py`. Three-pass compilation: DType (constructor
registration), DExt (external opcode binding), DLet (function compilation).

Key implementation notes for future reference:
- **pred_env bootstrap limitation**: when a `match` has multiple App-bearing
  constructor arms and outer lambda params are referenced in arm bodies, arms[1+]
  are compiled in a fresh `pred_env` with no locals. Workaround: use nat_eq
  chain dispatch with single-arm helper functions.
- **Pass-self pattern**: `ce` parameter threads `cg_compile_expr` through all
  compile functions to break pseudo-mutual recursion.
- **`fn` is a reserved word** (ASCII alias for `λ`): use `fname` instead.
- **Forward reference rule**: every function that is called by another must be
  defined *before* it in the file (no forward refs in the restricted dialect).

### Milestone 8.6: Seed emitter ✓ COMPLETE

**Input:** `List (Pair Nat PlanVal)` + entry FQ name.
**Output:** `Bytes` (seed format per `spec/07-seed-format.md`).

Port of `bootstrap/emit_seed.py:save_seed()`. Algorithm:
1. Traverse PlanVal DAG; build deduplicated intern table.
2. Classify atoms: byte (0–255), word (256–2^64-1), bignat.
3. Build scope table: holes, bignats, words, bytes, cells.
4. Write 40-byte header (5 × u64 LE).
5. Write atom table: bignat sizes, bignat data, words, bytes.
6. Write fragment bitstream (bit-packed, LSB-first per spec §4.1).

All byte construction uses the `Bytes = MkPair len content_nat` encoding already
in the file; `byte_append`, `u64_le`, and `bits_append` helpers will be needed.

Tests: `tests/compiler/test_emit.py` — byte-level comparison against Python
`bootstrap/emit_seed.py` for the same PlanVal inputs.

### Milestone 8.7: Driver/main ✓ COMPLETE

`main : Bytes → Bytes` in Section 26. Chains lex → parse_program → compile_program →
emit_program. Module name hardcoded to "Compiler" (`nn_Compiler = 8243113893085146947`,
i.e. `int.from_bytes(b'Compiler', 'little')`). Tests: `tests/compiler/test_driver.py`.

### Milestone 8.8: Self-hosting validation — PARTIAL

**Path B (harness, complete):** `tests/compiler/test_selfhost.py` — 17 tests pass.

  1. Python bootstrap compiles `Compiler.gls` → compiled dict of PLAN values.
  2. `plan2pv` bridge converts each PLAN value to a GLS PlanVal ADT value.
  3. GLS `emit_program` (BPLAN jets) processes the list → Plan Assembler bytes.
  4. Assertions: non-empty, starts with `(#bind "`, correct bind count (one per
     definition), contains `Compiler.main` binding, all lines are bind forms.

**Path A (planvm functional):**

  1. Compile `Compiler.run_main` with Python bootstrap → `run_main.seed`
  2. Run `planvm run_main.seed <source_text>` (source text as CLI arg) → Plan Assembler bytes
  3. Assert output equals Path B output (byte-identical)

  `Compiler.run_main` is Section 27 of `Compiler.gls`:
  - Takes argVec from planvm (forces to `P(src_nat)`)
  - Unpins via `Core.PLAN.unpin` → named law "Unpin" (findop.table jet)
  - Computes byte length via `nat_byte_len`
  - Constructs `Bytes = MkPair len src_nat`, calls `main`
  - Writes output via `WriteOp (P(N(9)))` — takes size-4 Closure `(fd, buf, count, 0)`
    created by `_write_pack` (5-arity dummy applied to 4 args)
  Tests in `TestSelfhostPathA` and `test_compiler_run_main_seed_loads` are planvm-gated.

Step 3 (byte-identical planvm output vs Path B) is the definitive self-hosting gate.

---

## 7. Evaluation Testing Gap

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

## 8. Constraints and Limitations

All constraints from the restricted dialect (`bootstrap/BOOTSTRAP.md §2`) apply:

- No cross-module imports (each file compiled independently)
- No mutual recursion (each SCC is a single definition)
- No typeclasses (explicit dictionary passing)
- `handle`/`eff`/`pure`/`run`/`do` expressions supported (M12.2)
- `use` declarations parsed but treated as no-ops (M12.4)
- No tuples (encode as unary/binary constructors)
- Pattern match: Nat literals, wildcard, constructor patterns only

Additional M8 constraints:
- **No type checking** (deferred to M8.1 or M9)
- **Association-list maps only** (no BST until Core.Map is available)
- **No text interpolation** (build error messages by concatenation)

---

## 9. File Layout

```
compiler/
  COMPILER.md          ← this file
  CODEGEN_PLAN.md      ← detailed M8.5 codegen implementation plan (10-layer)
  src/
    Compiler.gls       ← single monolithic file: all phases
                          Section 1–9:   utilities (M8.1)
                          Section 10–18: lexer (M8.2)
                          Section 19–23: parser (M8.3)
                          Section 24:    codegen (M8.5)
                          scope resolver (M8.4) — integrated into codegen section
                          Section 25:    Plan Assembler emitter (M8.6)
                          Section 26:    main driver (M8.7)

tests/compiler/
  test_utils.py        ← M8.1 utility tests (42 passed, 5 skipped)
  test_lexer.py        ← M8.2 lexer tests (10 passed, 3 skipped)
  test_scope.py        ← M8.4 scope tests (15 passed)
  test_emit.py         ← M8.6 emitter tests (38 passed, 1 planvm-gated skip)
  test_driver.py       ← M8.7 driver tests (3 passed, 3 skipped)
  test_selfhost.py     ← M8.8 self-hosting validation (17 passed, 5 planvm-gated: seed loads + Path A)
  test_m11.py          ← M11.5 typeclass tests (20 passed)
  test_m12_effects.py  ← M12.2/M12.4 effects + DeclUse tests (25 passed)
```

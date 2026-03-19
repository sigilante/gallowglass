```
gallowglass/
  SPEC.md                    -- master index, architecture overview, gospel principles
  
  spec/
    00-primitives.md         -- Core.Primitives (Phase 0.1, already done, needs formatting)
    01-glass-ir.md           -- Glass IR grammar (Phase 0.2, already done, needs formatting)
    02-mutual-recursion.md   -- SCC compilation (Phase 0.3, already done, needs formatting)
    03-exhaustiveness.md     -- Exhaustiveness checker (Phase 0.4, already done, needs formatting)
    04-plan-encoding.md      -- How Gallowglass constructs map to PLAN (new — needed for codegen)
    05-type-system.md        -- Types, effects, rows, contracts (new — needed for typechecker)
    06-surface-syntax.md     -- Full surface grammar (new — needed for lexer/parser)
    07-seed-format.md        -- Seed serialization (new — needed for serializer)
  
  bootstrap/
    BOOTSTRAP.md             -- Bootstrap compiler overview, restricted dialect, milestones
    src/
      (empty — Claude Code fills this in)
  
  prelude/
    PRELUDE.md               -- Prelude scope and organization
    src/
      Core/
        Primitives.gls       -- external mod declarations
        Types.gls            -- Bool, Option, Result, List, Pair
        Classes.gls          -- Eq, Ord, Show, Debug, Serialize
        Effects.gls          -- Exn, State, IO, Generator
        Numeric.gls          -- Nat, Int, Rational arithmetic
        Text.gls             -- Text and Bytes
        Combinators.gls      -- id, const, fix, ·, |>
  
  compiler/
    COMPILER.md              -- Self-hosting compiler overview
    src/
      (empty — Claude Code fills this in)
  
  vm/
    VM.md                    -- Rust VM specification (post-1.0)
    src/
      (empty — later)
  
  tests/
    TESTS.md                 -- Test strategy and corpus
    bootstrap/               -- Bootstrap compiler test cases
    prelude/                 -- Prelude test cases
    compiler/                -- Self-hosting compiler test cases
```

```
gallowglass/
  SPEC.md                    -- master index, architecture overview, gospel principles
  
  spec/
    00-primitives.md         -- Core.Primitives
    01-glass-ir.md           -- Glass IR grammar
    02-mutual-recursion.md   -- SCC compilation
    03-exhaustiveness.md     -- Exhaustiveness checker
    04-plan-encoding.md      -- How Gallowglass constructs map to PLAN
    05-type-system.md        -- Types, effects, rows, contracts
    06-surface-syntax.md     -- Full surface grammar
    07-seed-format.md        -- Seed serialization format
  
  bootstrap/
    BOOTSTRAP.md             -- Bootstrap compiler overview, restricted dialect
    *.py                     -- Python bootstrap compiler
  
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
    src/Compiler.gls         -- Self-hosting compiler in Gallowglass
  
  vm/
    VM.md                    -- Rust VM specification (forward work)
    src/                     -- Rust VM implementation
  
  tests/
    TESTS.md                 -- Test strategy and corpus
    bootstrap/               -- Bootstrap compiler test cases
    prelude/                 -- Prelude test cases
    compiler/                -- Self-hosting compiler test cases
```

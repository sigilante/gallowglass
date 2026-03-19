# Gallowglass

Gallowglass is a programming language designed for LLMs to write and reason about, targeting the PLAN virtual machine (xocore-tech/PLAN). It is not yet self-hosting. This repo contains the language specification, bootstrap compiler (Sire), core prelude (Gallowglass), and self-hosting compiler (Gallowglass).

## Design Principles (Gospel)

These are non-negotiable. When in doubt, return to these.

- **A contract is suspicious if it could only be written by someone who had already read the implementation. A contract is valuable if it could be written by someone who only had the mathematical specification.**
- **Contracts derive tests. Tests do not become contracts.**
- **Representation has audience. `Show` is for users. `Debug` is for developers. `Serialize` is for machines. Never conflate them.**
- Effects are always locally visible in type signatures. Nothing is hidden.
- Pure by default. Effect annotation is explicit, not implicit.
- Structural truth over convenient fictions. The type system never lies.

## Repository Structure

```
gallowglass/
  CLAUDE.md              ← you are here
  DECISIONS.md           ← design rationale for non-obvious choices
  SPEC.md                ← full architecture overview (read this first)

  spec/
    00-primitives.md     ← Core.Primitives: ~101 operations, 11 modules
    01-glass-ir.md       ← Glass IR formal grammar (PEG + well-formedness)
    02-mutual-recursion.md ← SCC compilation, shared pins, lambda lifting
    03-exhaustiveness.md ← Pattern match exhaustiveness checker design
    04-plan-encoding.md  ← How Gallowglass constructs map to PLAN
    05-type-system.md    ← Types, effects, rows, contracts
    06-surface-syntax.md ← Full surface grammar
    07-seed-format.md    ← Seed serialization format

  bootstrap/
    BOOTSTRAP.md         ← Bootstrap compiler overview and milestones
    src/                 ← Sire source (bootstrap compiler)

  prelude/
    PRELUDE.md           ← Prelude scope and organization
    src/Core/            ← Gallowglass source (core prelude)

  compiler/
    COMPILER.md          ← Self-hosting compiler overview
    src/                 ← Gallowglass source (self-hosting compiler)

  tests/
    TESTS.md             ← Test strategy
    bootstrap/           ← Bootstrap compiler tests
    prelude/             ← Prelude tests
    compiler/            ← Self-hosting compiler tests
```

## Before Starting Any Task

1. Read `SPEC.md` for architecture context.
2. Read the relevant `spec/` document for the component you are working on.
3. Read the relevant `BOOTSTRAP.md`, `PRELUDE.md`, or `COMPILER.md` for implementation guidance.
4. Check `DECISIONS.md` if something seems surprising or you want to understand why.

## Language Quick Reference

### Naming Conventions (compiler-enforced)
- Functions and values: `snake_case`
- Types and effects: `PascalCase`
- Type variables: single lowercase `a`–`q`
- Row variables: single lowercase `r`–`z`
- Modules: `Dot.Qualified`

### Key Syntax
```gallowglass
-- Function definition: spec above =, impl below
let name : Type
  | pre  Proven (precondition)
  | post Deferred(NoSolver) (postcondition)
  = body

-- Effect row: {Effect1, Effect2 | r} ReturnType
let read_file : Path → {IO, Exn IOError | r} Bytes

-- Handler
handle computation {
  | return x   → x
  | raise e  k → default_value
}

-- Algebraic type
type Result a b =
  | Ok  a
  | Err b

-- Programmer pin (DAG node)
@result = expensive_computation x
```

### Canonical Unicode Operators
`→` `λ` `∀` `∃` `←` `·` `⊕` `⊗` `⊤` `⊥` `∅` `≠` `≤` `≥` `∈` `∉` `⊆`
ASCII alternatives are normalized to Unicode at the lexer — never appear post-lex.

### Effect System
- `Abort` is NOT in any effect row. It is unhandleable, propagates to cog supervisor.
- `External` marks VM boundary crossings.
- `{}` empty row means pure. Absence of annotation also means pure.
- Dictionaries are implicit in source, explicit in Glass IR.

## VM Target

PLAN (xocore-tech/PLAN). Four constructors: Pin `<i>`, Law `{n a b}`, App `(f g)`, Nat `@`. Five opcodes (0–4). Hash algorithm: BLAKE3-256. Serialization: Seed format.

All Gallowglass types are erased at compile time. The PLAN output is untyped. Type errors are purely a Gallowglass-layer concern.

## Current Phase

**Phase 0 complete.** Foundation documents exist in `spec/`.
**Phase 1 in progress.** Bootstrap compiler in `bootstrap/src/` (Sire).

The bootstrap compiler compiles the **restricted dialect** of Gallowglass only.
See `bootstrap/BOOTSTRAP.md` for what the restricted dialect permits.

## Build and Test

```bash
# Run the xocore PLAN reference VM
# (requires xocore-tech/PLAN installed)
planvm <seed-file>

# Run bootstrap compiler (once built)
sire bootstrap/src/main.sire < input.gls > output.seed

# Run tests
make test-bootstrap    # bootstrap compiler tests
make test-prelude      # prelude tests
make test-compiler     # self-hosting compiler tests
```

## Key Invariants to Never Violate

- Glass IR round-trips: a Glass IR fragment must reparse to the same PLAN output.
- Abort never appears in an effect row.
- External effects must be in the row of any function crossing the VM boundary.
- Canonical SCC ordering is lexicographic by name — any deviation changes PinIds.
- BLAKE3-256 is the hash algorithm everywhere. No exceptions.
- `Show` and `Debug` are distinct typeclasses. Never conflate them.
- Contracts must be statable from the mathematical specification alone.
- 

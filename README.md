# Gallowglass

An LLM-first programming language targeting the [PLAN virtual machine](https://github.com/xocore-tech/PLAN).

![](./img/hero.jpeg)

## What is Gallowglass?

Gallowglass is a statically typed functional programming language designed with two equally weighted goals:

1. **LLMs can write it correctly.** High local constraint at every token, effects visible in every signature, canonical naming enforced by the compiler, no implicit state.
2. **LLMs can reason about it accurately.** Pure by default, explicit effects, contracts stated from mathematical specifications, Glass IR makes compiler decisions visible.

It compiles to PLAN --- a minimal graph-reduction VM with four constructors (Pin, Law, App, Nat) and five opcodes. All types are erased at compile time; type errors are purely a compile-time concern.

## Quick taste

```gallowglass
-- Algebraic types
type Result a b =
  | Ok  a
  | Err b

-- Effects are always visible in type signatures
let read_file : Path -> {IO, Exn IOError | r} Bytes

-- Handlers discharge effects locally
handle computation {
  | return x  -> x
  | raise e k -> default_value
}

-- Typeclasses
class Eq a {
  let eq : a -> a -> Bool
  let neq : a -> a -> Bool
  let neq = \ xx yy -> not (eq xx yy)
}

-- Where clauses and operator sections
let hypotenuse = \ aa bb ->
  result
  where { sq_a = aa * aa ; sq_b = bb * bb ; result = sq_a + sq_b }

let incremented = map (+ 1) my_list
```

Naming is compiler-enforced: `snake_case` for values, `PascalCase` for types, single lowercase letters for type variables. Unicode operators (`->`, `\`) are normalized to canonical forms (`->` to `\u2192`, `\` to `\u03bb`) at the lexer.

## Project structure

```
gallowglass/
  SPEC.md                -- Full language specification
  ROADMAP.md             -- Delivery plan with milestones
  DECISIONS.md           -- Design rationale for non-obvious choices

  spec/                  -- Component specifications
  bootstrap/             -- Python bootstrap compiler (lexer -> parser -> scope -> typecheck -> codegen -> emit)
  prelude/src/Core/      -- Core prelude in Gallowglass (8 modules, 112 definitions)
  compiler/src/          -- Self-hosting compiler in Gallowglass
  tests/                 -- 1210 tests (bootstrap, prelude, compiler)
  doc/                   -- Language guide and references
```

## Build and test

```bash
# Run all tests
python3 -m pytest tests/

# Run bootstrap compiler tests only
python3 -m pytest tests/bootstrap/

# Compile a Gallowglass file
python3 -c "
from bootstrap.lexer import lex; from bootstrap.parser import parse
from bootstrap.scope import resolve; from bootstrap.codegen import compile_program
from bootstrap.emit import emit
import sys
src = open(sys.argv[1]).read()
prog = parse(lex(src, sys.argv[1]), sys.argv[1])
resolved, _ = resolve(prog, 'Module', {}, sys.argv[1])
compiled = compile_program(resolved, 'Module')
sys.stdout.buffer.write(emit(compiled, 'Module.main'))
" input.gls > output.seed
```

Tests: 1210 passing, 145 skipped. Skipped tests are planvm-gated (require the PLAN VM binary) or deep-recursion stress tests that exceed Python's stack.

## Design principles

- **Contracts derive tests. Tests do not become contracts.** A contract is valuable if it could be written by someone who only had the mathematical specification.
- **Effects are always locally visible** in type signatures. Nothing is hidden.
- **Pure by default.** Effect annotation is explicit, not implicit.
- **Structural truth over convenient fictions.** The type system never lies.
- **Representation has audience.** `Show` is for users. `Debug` is for developers. `Serialize` is for machines. Never conflate them.

## Status

**Alpha (0.999).** The bootstrap compiler, core prelude, and self-hosting compiler are complete through M20. The language is usable for writing programs that compile to PLAN.

What works:
- Full surface syntax: let bindings, lambdas, pattern matching (with exhaustiveness checking), algebraic types, records, type aliases, list syntax
- Effect system: algebraic effects with CPS handlers, shallow (once) handlers, do-notation
- Typeclasses: single-parameter classes, superclass constraints, default methods, constrained instances, dictionary-passing codegen
- Module system: `use` imports, export lists, pin-based module loading (BLAKE3-256 content addressing)
- Glass IR: type-annotated intermediate representation with round-trip verification
- Self-hosting: compiler written in Gallowglass processes its own source and produces correct Plan Assembler output

What remains for 1.0: deriving, contract solver tiers, nested list patterns. Post-1.0: Rust VM, debugger, jet optimizer.

## Change history

| Date | Milestone | Summary |
|------|-----------|---------|
| 2026-03-19 | M0 | Initial commit: spec, design docs |
| 2026-03-21 | M1--M7 | Bootstrap compiler (lexer, parser, scope, codegen, emit), core prelude (5 modules, 24 defs) |
| 2026-03-22 | M7.5--M8.3 | Predecessor binding, field extraction, self-hosting compiler through parser |
| 2026-03-24 | M8.4--M8.8 | Self-hosting scope, codegen, emitter, driver; Path B validation complete |
| 2026-04-03 | M9 | Fix expressions, tuples, mutual recursion (SCC compilation) |
| 2026-04-04 | M10--M11 | CPS effect handlers, do-notation, typeclasses (DeclClass, DeclInst, dictionary insertion) |
| 2026-04-05 | M12 | Module system: use imports, build driver, cross-module instances |
| 2026-04-06 | M12.2--M12.5 | GLS compiler parity (DEff/EHandle/EDo/DeclUse), Data.Csv integration tests |
| 2026-04-07 | M13--M14 | Default methods, compound instances, shallow handlers, Eq/Ord/Show/Debug, pipe/fixpoint |
| 2026-04-07 | M15 | Records, type aliases, list/cons syntax, or-patterns, guards, string interpolation |
| 2026-04-08 | M16--M18 | Pin-based module loading (110-pin DAG), Glass IR emission, type-annotated Glass IR |
| 2026-04-08 | M19 | Pattern match exhaustiveness checking (Maranget algorithm) |
| 2026-04-08 | M20 | 0.999 syntax: where clauses, operator sections, export list enforcement |

## License

See repository for license terms.

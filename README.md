# Gallowglass

An LLM-first programming language targeting the PLAN virtual machine.

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
  ROADMAP.md             -- Delivery plan and forward work
  DECISIONS.md           -- Design rationale for non-obvious choices

  spec/                  -- Component specifications
  bootstrap/             -- Python bootstrap compiler (lexer -> parser -> scope -> typecheck -> codegen -> emit)
  prelude/src/Core/      -- Core prelude in Gallowglass (8 modules, 112 definitions)
  compiler/src/          -- Self-hosting compiler in Gallowglass
  tests/                 -- Test suite (bootstrap, prelude, compiler)
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
from bootstrap.emit_seed import emit
import sys
src = open(sys.argv[1]).read()
prog = parse(lex(src, sys.argv[1]), sys.argv[1])
resolved, _ = resolve(prog, 'Module', {}, sys.argv[1])
compiled = compile_program(resolved, 'Module')
sys.stdout.buffer.write(emit(compiled, 'Module.main'))
" input.gls > output.seed
```

Skipped tests are planvm-gated (the legacy xocore VM, no longer a deployment target) or deep-recursion stress tests that exceed Python's stack. Run `python3 -m pytest tests/ -q` for current pass/skip totals.

## Design principles

- **Contracts derive tests. Tests do not become contracts.** A contract is valuable if it could be written by someone who only had the mathematical specification.
- **Effects are always locally visible** in type signatures. Nothing is hidden.
- **Pure by default.** Effect annotation is explicit, not implicit.
- **Structural truth over convenient fictions.** The type system never lies.
- **Representation has audience.** `Show` is for users. `Debug` is for developers. `Serialize` is for machines. Never conflate them.

## Status

The bootstrap compiler, core prelude, and self-hosting compiler are in place. The language is usable for writing programs that compile to PLAN.

What works:
- Full surface syntax: let bindings, lambdas, pattern matching (with exhaustiveness checking), algebraic types, records, type aliases, list syntax
- Effect system: algebraic effects with CPS handlers, shallow (once) handlers, do-notation
- Typeclasses: single-parameter classes, superclass constraints, default methods, constrained instances, dictionary-passing codegen
- Module system: `use` imports, export lists, pin-based module loading (BLAKE3-256 content addressing)
- Glass IR: type-annotated intermediate representation with round-trip verification
- Self-hosting: compiler written in Gallowglass processes its own source and produces correct Plan Assembler output

Forward work — including deriving, contract solver tiers, nested list patterns, the Rust VM, the debugger, and the jet optimizer — is tracked in `ROADMAP.md`.

## License

See repository for license terms.

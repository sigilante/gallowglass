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

## Interactive use

### Jupyter kernel

A Jupyter kernel evaluates Gallowglass cells in-process via the Python BPLAN harness — declarations accumulate across cells, expressions render as cell results.

Install the kernelspec once per Python environment:

```bash
python3 -m bootstrap.jupyter_kernel install
```

This registers a `gallowglass` kernel with Jupyter (under `~/Library/Jupyter/kernels/gallowglass/` on macOS, `~/.local/share/jupyter/kernels/gallowglass/` on Linux). Then launch a notebook:

```bash
jupyter lab    # or `jupyter notebook`
```

and choose **Gallowglass** from the kernel selector.

A typical session:

```gallowglass
-- Cell 1: declaration — renders as `twice : Nat → Nat`
let twice : Nat → Nat = λ n → n + n

-- Cell 2: expression — renders as `42`
twice 21

-- Cell 3: text — renders as `"hello"` (quoted)
"hello"

-- Cell 4: more declarations
use Core.Pair unqualified { Pair, MkPair }
use Core.List unqualified { List, Cons, Nil }

-- Cell 5: compound value — renders as `Cons (MkPair 1 10) (Cons (MkPair 2 20) Nil)`
Cons (MkPair 1 10) (Cons (MkPair 2 20) Nil)

-- Cell 6: pattern match — renders as `8`
let snd_plus_one : Pair Nat Nat → Nat
  = λ p → match p { | MkPair _ b → b + 1 }
snd_plus_one (MkPair 3 7)
```

Cell output is type-driven. Declaration cells (`let`, `type`, `use`) display a one-line summary per declaration so the user can see what was just added to the notebook (`twice : Nat → Nat`). Expression cells render the result value:

* **Primitives** (`Nat`, `Bool`, `Text`) render in their canonical literal forms — `42`, `True`, `"hello"`.
* **Constructors** (user-defined types, `Pair`, `Option`, `List`, `Result`) render with their constructor names recovered from the compile-time `con_info` table — `MkPair 3 7`, `Cons 1 (Cons 2 Nil)`, `Some 42`. Field types are derived by matching each constructor's scheme against the cell's instantiated type and applying the substitution.
* **Functions** render as `<λ : Nat → Nat>`, surfacing the type rather than the underlying law structure.

Output is emitted as both `text/plain` and `text/html` — JupyterLab and notebooks render the colourised HTML form (constructor names in bold blue, types in muted italic, numbers in cyan, strings in green, keywords in orange italic). Terminals and JSON exports fall back to the plain text rendering, which carries the same content without colour.

If the cell isn't an expression at all, it's parsed as one or more top-level declarations and accumulated into the notebook's module. A failing cell does not corrupt the accumulated state — the next cell still sees whatever the last successful cell defined.

The kernel runs entirely in Python and does not require Reaver or Nix.

To uninstall the kernel:

```bash
jupyter kernelspec remove gallowglass
```

### MCP server

For LLM-driven workflows, an MCP (Model Context Protocol) server exposes four tools — `compile_snippet`, `infer_type`, `explain_effect_row`, `render_fragment` — over stdio:

```bash
python3 -m bootstrap.mcp_server
```

The server loads the Core prelude once at startup and threads it as priors into every per-call snippet build. See `bootstrap/mcp_server.py` for the protocol shape.

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

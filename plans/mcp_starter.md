# MCP Starter Server for Gallowglass

**Status:** design proposal — not started.
**Estimated effort:** 1–2 days for the starter; deferred extensions
scoped against later milestones (Rust VM for the live-query path).

## Goal

A small Model Context Protocol server that gives any MCP-speaking
LLM agent a typed interface onto Gallowglass — primarily for two
audiences:

1. **External agents writing Gallowglass programs.** They want to
   compile, typecheck, and emit Glass IR without learning the Python
   bootstrap's shell entry points or carrying a worktree.
2. **Future internal agents that need structured queries over
   compiled programs.** Glass IR is already designed as a
   machine-readable surface; an MCP resource is the natural way to
   expose it.

The starter is deliberately small.  Anything that just shells out to
`tools/selfcompile.py` from inside Claude Code does not need MCP.
The value is making the interface *typed*, *portable*, and
*resource-shaped* — not in re-implementing the CLI.

## In scope (v0)

### Tools

| Name | Args | Returns | Backing |
|---|---|---|---|
| `compile_gls` | `source: string, module_name?: string` | `plan_asm: string` | `bootstrap.{lexer,parser,scope,codegen,emit_pla}` pipeline |
| `typecheck_gls` | `source: string, module_name?: string` | `errors: list[{loc, message}]` | `bootstrap.typecheck` |
| `emit_glass_ir` | `source: string, module_name?: string` | `glass_ir: string` | `bootstrap.glass_ir_debug` |

Each tool wraps the existing Python pipeline with a JSONSchema
contract.  Errors propagate as MCP error responses with the original
`Loc` (`file:line:col`) preserved.

### Resources

URI-addressable read-only documents.  All served verbatim from disk;
the MCP server doesn't transform them.

- `gallowglass://spec/SPEC.md` — top-level architecture
- `gallowglass://spec/{00-primitives, 01-glass-ir, 02-mutual-recursion,
  03-exhaustiveness, 04-plan-encoding, 05-type-system,
  06-surface-syntax, 07-seed-format}.md`
- `gallowglass://DECISIONS.md` — design rationale
- `gallowglass://prelude/Core/{Bool,Bytes,List,Nat,Option,Pair,Result,Text}.gls`
  — the eight core prelude modules

### Prompts

Skipped for v0.  The interface is small enough that prompt templates
add noise; the agent can compose calls directly.

## Out of scope (v0)

- **Live snapshot queries.**  Requires the Rust VM (see ROADMAP.md
  §"Debugger and Glass IR").  The VM's snapshot ABI needs to settle
  before we commit to an MCP shape.
- **Effect injection / breakpoint controls.**  Same — VM-blocked.
- **Pin store browsing.**  Possible but low-value pre-1.0; the pin
  store is mostly an internal codegen artifact today.
- **Self-host execution under Reaver.**  `run_plan_under_reaver`
  would be a fourth tool, but it requires `nix`/`cabal` on the
  server host, which complicates packaging.  Defer until external
  agents ask for it.
- **Project-aware tools.**  No `find_definition` /
  `list_symbols` / etc.  Those duplicate file-system access an
  agent already has; the MCP server isn't a search index.

## Implementation sketch

```
mcp/
  pyproject.toml       — declares the `gallowglass-mcp` package
  README.md
  src/
    gallowglass_mcp/
      __init__.py
      server.py        — entry point; FastMCP wiring
      tools.py         — compile_gls / typecheck_gls / emit_glass_ir
      resources.py     — spec/decision-doc URI handlers
  tests/
    test_compile.py
    test_resources.py
```

Dependencies: `mcp` (Anthropic's reference Python SDK), `gallowglass`
(installed from repo root).  Server runs over stdio; clients connect
via local subprocess.

### Versioning

The MCP package version tracks the compiler version it bundles —
`gallowglass-mcp v1.0.0` ships with `Compiler.plan` from
`compiler/dist/`, BLAKE3 verified at load time against
`compiler/dist/MANIFEST.json`.

### Testing

A `tests/test_mcp_smoke.py` in the main repo runs the server in a
subprocess, sends `compile_gls("let main = 42")`, asserts the
returned Plan Asm parses cleanly and the `Compiler_main` binding is
`42`.  CI gates on this.

## Extension paths (deferred)

| Extension | Blocker | Notes |
|---|---|---|
| `run_plan_under_reaver` | nix/cabal in server env | Or ship the Reaver Haskell binary as a vendored artifact. |
| `query_glass_ir(source, query)` | Define a small query language | DFS over JSON Glass IR — "list mutually-recursive functions", "what effects does X reach?". |
| `snapshot_query(snapshot_id, …)` | Rust VM | Live debugger surface. |
| `effect_inject(snapshot_id, eff, value)` | Rust VM + spec stability | Step debugger inject. |
| Prompts for Gallowglass authoring | No technical blocker | Add once we have telemetry on actual usage patterns. |

## Why this isn't urgent

Two reasons to not start this before 1.0:

1. The 1.0 surface is still moving — rc4-2 just landed.  Locking an
   MCP contract over an in-flux compiler interface is wasted churn.
2. We have no concrete external consumer yet.  Building MCP for a
   hypothetical agent is the same anti-pattern as building the Rust
   VM against speculative usage (see DECISIONS.md §"Why a
   purpose-built Rust VM?" for the analogous rejected path).

The right trigger is either:

- A specific external agent (or another team's tool) asking for
  programmatic Gallowglass access, *or*
- 1.0 stabilizes and the Rust VM lands, opening the live-query path
  that MCP genuinely uniquely enables.

Until one of those fires, this doc captures intent and design
constraints so the eventual implementation doesn't re-derive them.

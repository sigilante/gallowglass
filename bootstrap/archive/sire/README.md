# Archived: Sire Bootstrap Compiler Stubs

These files are the original outline for a Gallowglass bootstrap compiler written in
Sire (PLAN's macro/assembly language). They were superseded before implementation began.

## Why archived

The bootstrap compiler was implemented in Python instead (see `bootstrap/*.py`). The Python
compiler produces valid PLAN seed bytes directly, making the Sire intermediate unnecessary.
The original rationale for Sire was to avoid a foreign toolchain dependency — but Python is
equally available and the compiler is discarded after Phase 3 anyway.

## What these files contain

High-level outlines of each compiler phase with comments describing the PLAN encoding
strategy. They are useful as reference for the encoding logic (de Bruijn indices,
constructor encoding, opcode usage) but are not functional code.

| File | Phase |
|------|-------|
| `lexer.sire` | Bytes → token list |
| `token.sire` | Token type definitions |
| `parser.sire` | Token list → AST |
| `ast.sire` | AST type definitions |
| `scope.sire` | Scope resolution, qualified names |
| `typecheck.sire` | Restricted HM unification |
| `lower.sire` | AST → Glass IR (restricted) |
| `codegen.sire` | Glass IR → PLAN laws/pins |
| `emit.sire` | PLAN value → seed bytes |
| `prelude.sire` | Compiler's own list/option utilities |
| `main.sire` | Top-level driver |

## Current status

Superseded. Do not implement. See `bootstrap/BOOTSTRAP.md` for the current plan.

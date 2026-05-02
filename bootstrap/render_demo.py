#!/usr/bin/env python3
"""
Render a demo .gls file as Glass IR text on stdout.

Usage:
    python3 -m bootstrap.render_demo demos/csv_table.gls [Module.Name]

If the module name is omitted, it is derived from the basename (e.g.
``csv_table.gls`` → ``CsvTable``, snake_case → CamelCase).

Compiles the demo with the full Core prelude available (so ``use`` imports
resolve), type-checks against the cumulative prelude type environment, then
emits Glass IR for the demo's own module via ``bootstrap.glass_ir.render_module``.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from bootstrap.build import PRELUDE_MODULES, build_modules
from bootstrap.glass_ir import render_module
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import resolve
from bootstrap.typecheck import typecheck


def _camel_case_from_path(path: str) -> str:
    """`demos/csv_table.gls` → `CsvTable`."""
    base = os.path.splitext(os.path.basename(path))[0]
    return ''.join(part.capitalize() for part in base.split('_'))


def render_demo_glass_ir(demo_path: str, demo_module: str | None = None,
                         prelude_dir: str | None = None) -> str:
    """Render the demo's Glass IR as a string."""
    if prelude_dir is None:
        prelude_dir = os.path.join(
            os.path.dirname(__file__), '..', 'prelude', 'src'
        )
    if demo_module is None:
        demo_module = _camel_case_from_path(demo_path)

    # Load all prelude sources plus the demo
    sources: list[tuple[str, str]] = []
    for mod in PRELUDE_MODULES:
        short = mod.split('.')[-1]
        path = os.path.join(prelude_dir, 'Core', f'{short}.gls')
        with open(path) as f:
            sources.append((mod, f.read()))
    with open(demo_path) as f:
        demo_src = f.read()
    sources.append((demo_module, demo_src))

    # Compile to populate cross-module class metadata, etc.  Result discarded;
    # we only need the side effect of validating the build.
    build_modules(sources)

    # Resolve the demo (and the prelude before it) to get the demo's
    # resolved AST.  We re-walk the prelude in dependency order to populate
    # `module_envs` so the demo's `use` declarations resolve.
    module_envs: dict = {}
    type_env: dict = {}
    demo_resolved = None
    demo_typecheck_ok = False
    for mod, source_text in sources:
        filename = f'<{mod}>'
        prog = parse(lex(source_text, filename), filename)
        resolved, env = resolve(prog, mod, module_envs, filename)
        module_envs[mod] = env
        try:
            mod_types = typecheck(resolved, env, mod, filename,
                                  prior_type_env=type_env)
            type_env.update(mod_types)
            if mod == demo_module:
                demo_typecheck_ok = True
        except Exception:
            # Type-checking is best-effort for Glass IR rendering; if a demo
            # fails to typecheck we still want to emit the IR.
            pass
        if mod == demo_module:
            demo_resolved = resolved

    if demo_resolved is None:
        raise RuntimeError(f'demo module {demo_module!r} not found in build')

    # Honor the renderer's contract: only pass type_env if it actually has
    # entries for this module. Otherwise the renderer raises (Pre-1 guard).
    return render_module(demo_resolved, demo_module, manifest=None,
                         type_env=type_env if demo_typecheck_ok else None)


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__, file=sys.stderr)
        return 1
    demo_path = sys.argv[1]
    demo_module = sys.argv[2] if len(sys.argv) > 2 else None
    if not os.path.isfile(demo_path):
        print(f'render_demo: file not found: {demo_path}', file=sys.stderr)
        return 1
    out = render_demo_glass_ir(demo_path, demo_module=demo_module)
    sys.stdout.write(out)
    return 0


if __name__ == '__main__':
    sys.exit(main())

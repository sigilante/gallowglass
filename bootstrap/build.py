"""
Gallowglass multi-module build system.

Compiles a collection of Gallowglass source files in dependency order,
threading resolved module environments and compiled PLAN values forward
so that cross-module references work correctly.

Usage:

    from bootstrap.build import build_modules

    compiled = build_modules([
        ('Core.Nat',  open('prelude/src/Core/Nat.gls').read()),
        ('Core.List', open('prelude/src/Core/List.gls').read()),
        ('App.Main',  open('src/Main.gls').read()),
    ])
    seed = emit(compiled, 'App.Main.main')

Dependencies are declared in source via `use` declarations:

    use Core.Nat                       -- qualified access only
    use Core.Nat { add, nat_eq }       -- bring names into scope (still need Nat. prefix)
    use Core.Nat unqualified { add }   -- bring add into unqualified scope

Cycles raise BuildError.  Unknown module references (that are not
external mod declarations) also raise BuildError.
"""

from __future__ import annotations

from typing import Any

from bootstrap.ast import DeclUse, Program
from bootstrap.codegen import compile_program
from bootstrap.lexer import lex
from bootstrap.parser import parse
from bootstrap.scope import Env, resolve
from dev.harness.plan import P


class BuildError(Exception):
    """Raised when the build system cannot resolve module dependencies."""


# ---------------------------------------------------------------------------
# Dependency scanning
# ---------------------------------------------------------------------------

def _scan_use_deps(program: Program) -> list[str]:
    """Return the module paths of all `use` declarations in program."""
    deps = []
    for decl in program.decls:
        if isinstance(decl, DeclUse):
            deps.append('.'.join(decl.module_path))
    return deps


# ---------------------------------------------------------------------------
# Topological sort (Kahn's algorithm)
# ---------------------------------------------------------------------------

def _topo_sort(
    module_names: list[str],
    dep_map: dict[str, list[str]],
) -> list[str]:
    """
    Return module_names in topological order (dependencies before dependents).

    dep_map: module → list of modules it depends on.
    Only edges between known modules are considered; edges to unknown modules
    (external mods, stdlib stubs) are ignored.

    Raises BuildError if a cycle is detected.
    """
    known = set(module_names)
    in_degree: dict[str, int] = {m: 0 for m in module_names}
    # adjacency: dep → [modules that depend on dep]
    graph: dict[str, list[str]] = {m: [] for m in module_names}

    for m in module_names:
        for dep in dep_map.get(m, []):
            if dep not in known:
                continue   # external mod or out-of-build — ignore
            graph[dep].append(m)
            in_degree[m] += 1

    # Start with all modules that have no in-build dependencies
    queue = sorted(m for m in module_names if in_degree[m] == 0)
    result: list[str] = []

    while queue:
        node = queue.pop(0)
        result.append(node)
        for neighbor in sorted(graph[node]):
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(result) != len(module_names):
        cycle_members = [m for m in module_names if m not in set(result)]
        raise BuildError(
            f"circular module dependency detected among: {cycle_members}"
        )

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_modules(
    sources: list[tuple[str, str]],
    pin_wrap: bool = False,
) -> dict[str, Any]:
    """
    Compile multiple Gallowglass source files in dependency order.

    Parameters:
        sources: Ordered list of (module_name, source_text) pairs.
                 The order is used as a tiebreaker when the dependency
                 graph has multiple valid topological orderings.

    Returns:
        Merged dict of all FQ names from all modules: fq_name → PLAN value.

    Raises:
        BuildError   on circular dependency or unknown module reference.
        ScopeError   on name resolution failure.
        ParseError   on syntax error.
        CodegenError on codegen failure.
    """
    # ------------------------------------------------------------------
    # Step 1: Parse all sources
    # ------------------------------------------------------------------
    module_names = [name for name, _ in sources]
    source_map: dict[str, str] = dict(sources)

    parsed: dict[str, Program] = {}
    for module_name, source_text in sources:
        filename = f'<{module_name}>'
        prog = parse(lex(source_text, filename), filename)
        parsed[module_name] = prog

    # ------------------------------------------------------------------
    # Step 2: Build dependency graph from `use` declarations
    # ------------------------------------------------------------------
    dep_map: dict[str, list[str]] = {}
    for module_name, prog in parsed.items():
        dep_map[module_name] = _scan_use_deps(prog)

    # Check for references to unknown modules (not in the build and not
    # declared as external mod inside the file).
    for module_name, deps in dep_map.items():
        known = set(module_names)
        for dep in deps:
            if dep not in known:
                raise BuildError(
                    f"module '{module_name}' uses unknown module '{dep}'. "
                    f"Add it to the build or declare it as 'external mod'."
                )

    # ------------------------------------------------------------------
    # Step 3: Topological sort
    # ------------------------------------------------------------------
    order = _topo_sort(module_names, dep_map)

    # ------------------------------------------------------------------
    # Step 4: Compile in topological order, threading state forward
    # ------------------------------------------------------------------
    module_envs: dict[str, Env] = {}   # module_name → resolved Env
    all_compiled: dict[str, Any] = {}  # accumulated FQ → PLAN value
    module_compilers: dict = {}        # module_name → Compiler (for class metadata)

    for module_name in order:
        prog = parsed[module_name]
        filename = f'<{module_name}>'

        # Resolve names — imports from already-compiled modules are in module_envs
        resolved, env = resolve(prog, module_name, module_envs, filename)
        module_envs[module_name] = env

        # Collect class metadata from all already-resolved modules so the
        # codegen can look up classes and instances defined upstream.
        pre_class_methods: dict = {}
        for mod_env in module_envs.values():
            pre_class_methods.update(mod_env.class_methods)

        # Collect class defaults and constraints from prior compilations.
        pre_class_defaults: dict = {}
        pre_class_constraints: dict = {}
        pre_con_info: dict = {}
        for prev_compiler in module_compilers.values():
            pre_class_defaults.update(prev_compiler._class_defaults)
            pre_class_constraints.update(prev_compiler._class_constraints)
            pre_con_info.update(prev_compiler.con_info)

        # Compile — cross-module globals from all_compiled; class metadata from
        # pre_class_methods enables cross-module instance and constraint resolution;
        # pre_con_info enables cross-module pattern matches on imported ADTs.
        from bootstrap.codegen import Compiler
        compiler = Compiler(module=module_name,
                            pre_compiled=all_compiled,
                            pre_class_methods=pre_class_methods,
                            pre_class_defaults=pre_class_defaults,
                            pre_class_constraints=pre_class_constraints,
                            pre_con_info=pre_con_info)
        compiled = compiler.compile(resolved)
        module_compilers[module_name] = compiler

        # Optionally wrap each value in a Pin for content-addressing
        if pin_wrap:
            compiled = {k: P(v) for k, v in compiled.items()}

        # Merge this module's output into the accumulator
        all_compiled.update(compiled)

    return all_compiled


# ---------------------------------------------------------------------------
# Demo helper: compile a single source file with the full Core prelude available
# ---------------------------------------------------------------------------

# Order matches bootstrap.build_prelude.MODULES.  Lexicographic-by-name ordering
# would also work since use-deps drive the topo sort, but we keep this explicit
# to avoid having to re-derive it elsewhere.
PRELUDE_MODULES = [
    'Core.Combinators',
    'Core.Nat',
    'Core.Bool',
    'Core.Text',
    'Core.Pair',
    'Core.Option',
    'Core.List',
    'Core.Result',
]


def build_with_prelude(
    demo_name: str,
    demo_source: str,
    prelude_dir: str | None = None,
) -> dict[str, Any]:
    """
    Compile `demo_source` (named `demo_name`, e.g. 'UrbWatcher') with the full
    Core prelude available for `use` declarations.

    Reads each module under `prelude_dir/Core/<Name>.gls`, prepends them to the
    build, and returns the merged compiled dict.  The demo can `use Core.List`
    etc. just like any other module.

    `prelude_dir` defaults to the repo's `prelude/src/` directory.
    """
    import os
    if prelude_dir is None:
        prelude_dir = os.path.join(
            os.path.dirname(__file__), '..', 'prelude', 'src'
        )

    sources: list[tuple[str, str]] = []
    for mod in PRELUDE_MODULES:
        short = mod.split('.')[-1]
        path = os.path.join(prelude_dir, 'Core', f'{short}.gls')
        with open(path) as f:
            sources.append((mod, f.read()))
    sources.append((demo_name, demo_source))
    return build_modules(sources)

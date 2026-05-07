"""
Gallowglass Jupyter kernel — A1 architecture (Python harness backend).

A Jupyter kernel that evaluates Gallowglass cells in-process via the
Python BPLAN harness (``dev.harness.bplan.bevaluate``). Sits next to
``bootstrap/mcp_server.py`` and shares the same prelude-loading and
snippet-compile machinery.

Architecture
------------

    Jupyter client (notebook / lab)
            │  ZeroMQ + Jupyter messaging protocol
            ▼
    GallowglassKernel (ipykernel.kernelbase.Kernel subclass)
            │
            ▼
    GallowglassEvaluator  ← pure Python, testable in isolation
            │
            ▼
    bootstrap.{lexer, parser, scope, codegen}
            │
            ▼
    dev.harness.bplan.bevaluate
            │
            ▼
    PLAN values rendered as Jupyter display data

The evaluator is intentionally separated from the Kernel class so it
can be unit-tested without spinning up an ipykernel instance.

Cell mode
---------

Each cell is interpreted as either:

* an **expression**, in which case the cell's value is the displayed
  result; or
* a **program fragment** (one or more top-level declarations), in
  which case the declarations are accumulated into the notebook's
  module source and no value is displayed.

Detection: try wrapping the cell as ``let _cell_N = <code>`` and
parsing. If that parses, the cell is an expression. Otherwise the
cell is parsed as a program fragment and appended to the accumulated
source on success.

State
-----

The notebook acts as a single module (default name ``Notebook``).
Accumulated declarations from prior cells stay in scope; each new
cell sees them. Recompile happens on every cell — the compile cost
is dominated by the prelude (cached in a ``PreludeSnapshot`` after
the first cell) plus the cumulative cell source, which stays small
in practice.

Limitations (Phase G+ open work)
--------------------------------

* Result formatting is text/plain only. ``Nat`` renders as decimal;
  other PLAN values render as a structural debug-ish form. A real
  ``Show`` typeclass-driven renderer is M14.5 on the roadmap.
* The Python BPLAN harness has a ~100K recursion ceiling; cells
  whose evaluation exceeds that surface as ``RecursionError``.
* No tab completion or doc inspection yet — the MCP server's
  ``infer_type`` / ``render_fragment`` would be the natural
  backends for those.

Running
-------

Install the kernelspec::

    python -m bootstrap.jupyter_kernel install

Launch a notebook with the ``Gallowglass`` kernel selected. The
kernel binary is invoked as::

    python -m bootstrap.jupyter_kernel
"""

from __future__ import annotations

import os
import sys
import traceback
from dataclasses import dataclass
from typing import Any

from bootstrap.lexer import lex
from bootstrap.parser import parse, ParseError
from bootstrap.scope import resolve, ScopeError
from bootstrap.codegen import Compiler, CodegenError
from bootstrap.typecheck import TypecheckError

from dev.harness.plan import P, is_nat, is_pin, is_law, is_app
from dev.harness.bplan import bevaluate, register_prelude_jets

# Prelude loading reuses the MCP server's snapshot machinery — both
# tools want the same "compile prelude once, thread through every
# subsequent build" shape. Importing the function (not the module
# state) means we do not share the MCP server's process-global cache.
from bootstrap.mcp_server import load_prelude, PreludeSnapshot


DEFAULT_MODULE = 'Notebook'


# ---------------------------------------------------------------------------
# Result envelope
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    """Result of evaluating one cell.

    Exactly one of ``value_text`` or ``error`` is populated for non-
    silent cells; ``decls_only`` cells (program fragments that
    contributed declarations but had no expression to evaluate) have
    both fields ``None``.
    """
    value_text: str | None = None
    error: dict | None = None
    decls_only: bool = False


# ---------------------------------------------------------------------------
# Evaluator — Jupyter-protocol-free, fully testable
# ---------------------------------------------------------------------------

class GallowglassEvaluator:
    """Stateful Gallowglass evaluator.

    Owns the prelude snapshot and the notebook's accumulated
    declarations. ``eval_cell(source)`` is the single entry point;
    it returns a ``CellResult`` describing what to display.
    """

    def __init__(self, *, module: str = DEFAULT_MODULE,
                 prelude: PreludeSnapshot | None = None):
        self.module = module
        self.prelude = prelude if prelude is not None else load_prelude()
        # Register Core.Nat / Core.Text / Core.List jets in the BPLAN
        # harness's identity-keyed registry. Without this, calls to
        # `Nat.mul`/etc. recurse through bevaluate's kal walker (~7K
        # Python frames per user-level recursion); with jets they
        # dispatch to native Python in one frame. Idempotent: the
        # registry de-duplicates on Python id of the underlying Law.
        register_prelude_jets(self.prelude.compiled)
        # Accumulator of source from prior cells. Declarations only —
        # expression-mode cells are wrapped on the fly and never enter
        # this string.
        self._accumulated_source: str = ''
        # Counter for synthesised expression-cell names. Monotonic;
        # never decremented, so each cell has a unique identifier even
        # across re-evaluations.
        self._cell_counter: int = 0

    # ------------------------------------------------------------------
    # Public entry
    # ------------------------------------------------------------------

    def eval_cell(self, source: str) -> CellResult:
        """Lex, parse, compile, and evaluate one cell.

        Returns a ``CellResult``. Exceptions raised by the bootstrap
        pipeline are caught and converted to structured errors;
        unexpected exceptions propagate (they're bugs).
        """
        if not source.strip():
            return CellResult(decls_only=True)

        # Strategy: try expression mode first (cheaper to detect parse
        # failure than to compile a full program and discover at codegen
        # time that we wanted an expression). If expression-mode parse
        # fails, fall back to program-mode.
        self._cell_counter += 1
        cell_name = f'_cell_{self._cell_counter}'
        wrapped = self._wrap_as_expression(source, cell_name)

        # Attempt 1: expression mode.
        try:
            value = self._eval_expression(wrapped, cell_name)
        except _NotAnExpression:
            # Fall through to program mode.
            pass
        except _CellError as e:
            return CellResult(error=e.envelope)
        else:
            return CellResult(value_text=self._format_value(value))

        # Attempt 2: program mode. The cell contributes declarations
        # to the accumulator, no value displayed.
        try:
            self._eval_program_fragment(source)
        except _CellError as e:
            return CellResult(error=e.envelope)

        return CellResult(decls_only=True)

    def reset(self) -> None:
        """Clear accumulated declarations; keep the prelude snapshot.

        The cell counter is not reset — synthesised names stay unique
        across the kernel's whole lifetime so log output and any
        external reference remains stable.
        """
        self._accumulated_source = ''

    # ------------------------------------------------------------------
    # Expression mode
    # ------------------------------------------------------------------

    def _wrap_as_expression(self, source: str, cell_name: str) -> str:
        """Wrap the cell as ``<accumulated>\\nlet <cell_name> = <source>``.

        The wrap is consumed by attempt-1 only; the accumulator is
        not modified unless attempt-2 succeeds.
        """
        prefix = self._accumulated_source
        if prefix and not prefix.endswith('\n'):
            prefix = prefix + '\n'
        return f'{prefix}let {cell_name} = {source}\n'

    def _eval_expression(self, wrapped_source: str, cell_name: str) -> Any:
        """Compile ``wrapped_source`` and evaluate the cell binding.

        Raises ``_NotAnExpression`` if the wrap fails to parse — that's
        the signal to fall back to program mode. Other pipeline errors
        raise ``_CellError`` with a structured envelope.
        """
        filename = f'<cell {cell_name}>'
        try:
            tokens = lex(wrapped_source, filename)
            program = parse(tokens, filename)
        except ParseError as e:
            # Could be either "the cell isn't an expression" (legitimate)
            # or "the cell is malformed" (real error). We can't tell
            # without trying program-mode parse, so raise the
            # not-an-expression sentinel and let the caller decide.
            raise _NotAnExpression() from e

        # From here on, errors are real — the wrap parsed, so we
        # genuinely tried to compile and something went wrong.
        try:
            resolved, _ = resolve(program, self.module,
                                  self.prelude.module_envs, filename)
            compiled = self._compile_with_prelude(resolved)
        except (ScopeError, TypecheckError, CodegenError) as e:
            raise _CellError(_error_envelope(_stage_for(e), e)) from e

        fq = f'{self.module}.{cell_name}'
        if fq not in compiled:
            # Wrap parsed but produced no binding for the synthesised
            # name. Treat as not-an-expression so program mode can try.
            raise _NotAnExpression()

        return self._force(compiled[fq])

    # ------------------------------------------------------------------
    # Program mode
    # ------------------------------------------------------------------

    def _eval_program_fragment(self, source: str) -> None:
        """Append ``source`` to the accumulator and recompile.

        On success, the accumulator commits the new source. On
        failure, the accumulator stays as it was — partial states
        don't leak into subsequent cells.
        """
        candidate = self._accumulated_source
        if candidate and not candidate.endswith('\n'):
            candidate = candidate + '\n'
        candidate = candidate + source
        if not candidate.endswith('\n'):
            candidate = candidate + '\n'

        filename = f'<cell {self._cell_counter}>'
        try:
            tokens = lex(candidate, filename)
            program = parse(tokens, filename)
            resolved, _ = resolve(program, self.module,
                                  self.prelude.module_envs, filename)
            self._compile_with_prelude(resolved)
        except (ParseError, ScopeError, TypecheckError, CodegenError) as e:
            raise _CellError(_error_envelope(_stage_for(e), e)) from e

        # All phases succeeded — commit the new source.
        self._accumulated_source = candidate

    # ------------------------------------------------------------------
    # Compile + evaluate helpers
    # ------------------------------------------------------------------

    def _compile_with_prelude(self, resolved) -> dict:
        """Run codegen against the prelude priors. Returns the
        compiled FQ → PVal dict for the notebook module.

        Mirrors ``bootstrap.mcp_server._compile_snippet``'s codegen
        block but skips the pin-wrapping and pin-id collection — the
        Jupyter kernel only needs runtime values, not pin manifests.
        """
        compiler = Compiler(
            module=self.module,
            pre_compiled=self.prelude.compiled,
            pre_class_methods=self.prelude.class_methods,
            pre_class_defaults=self.prelude.class_defaults,
            pre_class_constraints=self.prelude.class_constraints,
            pre_con_info=self.prelude.con_info,
        )
        return compiler.compile(resolved)

    def _force(self, val: Any) -> Any:
        """Reduce a PLAN value to head normal form via the BPLAN harness.

        Bumps Python's recursion limit for the duration of the call
        so deeper user-defined recursion gets a reasonable shot
        before tripping ``RecursionError``. The harness's own depth
        guard (``BEVALUATE_DEPTH_LIMIT``, 100K) is the actual upper
        bound; this is just to keep Python's smaller default from
        being the binding constraint first.
        """
        # Each user-level recursion in PLAN unrolls into many Python
        # frames inside bevaluate (the kal walker descends through
        # several layers per reduction). Empirically a tail-recursive
        # factorial uses ~7K Python frames per recursion level — so
        # we bump high enough that ~30 levels of user recursion stay
        # below the ceiling. Beyond that, the BPLAN harness's own
        # depth guard (BEVALUATE_DEPTH_LIMIT, 100K) is the next wall.
        old_limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old_limit, 200_000))
        try:
            return bevaluate(val)
        except RecursionError as e:
            raise _CellError(_error_envelope('runtime', e)) from e
        finally:
            sys.setrecursionlimit(old_limit)

    # ------------------------------------------------------------------
    # Result formatting
    # ------------------------------------------------------------------

    def _format_value(self, val: Any) -> str:
        """Render a PLAN value as text/plain.

        Phase G+ work: replace this with a Show-typeclass-driven
        renderer once M14.5 lands. Until then:

        * ``Nat`` renders as a decimal literal.
        * ``Pin`` renders as ``<pin {inner}>``.
        * ``Law`` renders as ``<law arity=N name="...">``.
        * ``App`` renders as ``(fun arg)``, structural — useful for
          inspecting constructor results before Show is wired up.
        """
        return _render(val, depth=0)


# ---------------------------------------------------------------------------
# Internal sentinels
# ---------------------------------------------------------------------------

class _NotAnExpression(Exception):
    """The cell doesn't parse as an expression. Try program mode."""


class _CellError(Exception):
    """A pipeline phase raised an error. Carries the structured
    envelope the kernel surfaces to Jupyter."""

    def __init__(self, envelope: dict):
        self.envelope = envelope
        super().__init__(envelope.get('message') or 'cell error')


# ---------------------------------------------------------------------------
# Error helpers — mirror MCP's shape for cross-tool consistency
# ---------------------------------------------------------------------------

def _stage_for(err: Exception) -> str:
    if isinstance(err, ParseError):
        return 'parse'
    if isinstance(err, ScopeError):
        return 'scope'
    if isinstance(err, TypecheckError):
        return 'typecheck'
    if isinstance(err, CodegenError):
        return 'codegen'
    if isinstance(err, RecursionError):
        return 'runtime'
    return 'internal'


def _error_envelope(stage: str, err: Exception) -> dict:
    loc = getattr(err, 'loc', None)
    return {
        'stage': stage,
        'message': str(err),
        'type': type(err).__name__,
        'loc': (
            {'file': loc.file, 'line': loc.line, 'col': loc.col}
            if loc is not None else None
        ),
    }


# ---------------------------------------------------------------------------
# Value rendering (text/plain)
# ---------------------------------------------------------------------------

_RENDER_MAX_DEPTH = 32


def _render(val: Any, depth: int) -> str:
    """Best-effort structural renderer.

    Bounded recursion depth keeps a malformed App tree from blowing
    out the stack while we're trying to *show* an error to the user.
    """
    if depth >= _RENDER_MAX_DEPTH:
        return '...'
    if is_nat(val):
        return str(val)
    if is_pin(val):
        return f'<pin {_render(val.val, depth + 1)}>'
    if is_law(val):
        name_repr = _decode_name_safe(val.name)
        return f'<law arity={val.arity} name={name_repr!r}>'
    if is_app(val):
        return f'({_render(val.fun, depth + 1)} {_render(val.arg, depth + 1)})'
    return repr(val)


def _decode_name_safe(name_nat: Any) -> str:
    """Best-effort: decode a strNat back to its UTF-8 form."""
    if not is_nat(name_nat) or name_nat == 0:
        return ''
    try:
        n = int(name_nat)
        raw = n.to_bytes((n.bit_length() + 7) // 8, 'little')
        return raw.decode('utf-8')
    except (UnicodeDecodeError, OverflowError):
        return f'<{name_nat}>'


# ---------------------------------------------------------------------------
# Jupyter kernel
# ---------------------------------------------------------------------------

# `ipykernel` is imported lazily inside ``_kernel_main`` so importing this
# module for tests doesn't require the Jupyter stack on the path.

def _kernel_main() -> None:
    """Launch the kernel via ``ipykernel.kernelapp.IPKernelApp``.

    Invoked when the user runs ``python -m bootstrap.jupyter_kernel``.
    """
    from ipykernel.kernelapp import IPKernelApp
    from ipykernel.kernelbase import Kernel

    class GallowglassKernel(Kernel):
        implementation = 'Gallowglass'
        implementation_version = '0.1.0'
        language_info = {
            'name': 'gallowglass',
            'mimetype': 'text/x-gallowglass',
            'file_extension': '.gls',
            'pygments_lexer': 'haskell',  # close enough for syntax highlighting
        }
        banner = (
            'Gallowglass kernel (Python BPLAN backend). '
            'Declarations accumulate; final expressions render as cell results.'
        )

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self._evaluator = GallowglassEvaluator()

        def do_execute(self, code, silent, store_history=True,
                       user_expressions=None, allow_stdin=False, *,
                       cell_id=None):
            try:
                result = self._evaluator.eval_cell(code)
            except Exception as e:  # noqa: BLE001
                # Truly unexpected — surface as a kernel error rather than
                # silently dropping. Anything we can name structurally is
                # caught inside the evaluator.
                tb = traceback.format_exception(type(e), e, e.__traceback__)
                if not silent:
                    self.send_response(self.iopub_socket, 'stream', {
                        'name': 'stderr',
                        'text': ''.join(tb),
                    })
                return {
                    'status': 'error',
                    'execution_count': self.execution_count,
                    'ename': type(e).__name__,
                    'evalue': str(e),
                    'traceback': tb,
                }

            if result.error is not None:
                if not silent:
                    self.send_response(self.iopub_socket, 'stream', {
                        'name': 'stderr',
                        'text': _format_error_for_stream(result.error),
                    })
                return {
                    'status': 'error',
                    'execution_count': self.execution_count,
                    'ename': result.error.get('type', 'Error'),
                    'evalue': result.error.get('message', ''),
                    'traceback': [_format_error_for_stream(result.error)],
                }

            if result.value_text is not None and not silent:
                self.send_response(self.iopub_socket, 'execute_result', {
                    'execution_count': self.execution_count,
                    'data': {'text/plain': result.value_text},
                    'metadata': {},
                })

            return {
                'status': 'ok',
                'execution_count': self.execution_count,
                'payload': [],
                'user_expressions': {},
            }

    IPKernelApp.launch_instance(kernel_class=GallowglassKernel)


def _format_error_for_stream(envelope: dict) -> str:
    """Render an error envelope as a single string suitable for the
    Jupyter ``stream`` channel.

    The shape mirrors the existing diagnostic format:
    ``<file>:<line>:<col>: error: <message>`` with a stage prefix.
    """
    stage = envelope.get('stage', 'error')
    msg = envelope.get('message', '')
    loc = envelope.get('loc')
    if loc is not None:
        prefix = f'{loc["file"]}:{loc["line"]}:{loc["col"]}: '
    else:
        prefix = ''
    return f'{prefix}{stage} error: {msg}\n'


# ---------------------------------------------------------------------------
# Kernelspec installation
# ---------------------------------------------------------------------------

def _install_kernelspec(user: bool = True, prefix: str | None = None) -> str:
    """Register the Gallowglass kernelspec with Jupyter.

    Returns the install path. Uses ``ipykernel``'s ``install`` helper
    in turn, since the kernel.json is tiny and synthesised here.
    """
    import json
    import tempfile
    from jupyter_client.kernelspec import KernelSpecManager

    spec = {
        'argv': [sys.executable, '-m', 'bootstrap.jupyter_kernel', '-f', '{connection_file}'],
        'display_name': 'Gallowglass',
        'language': 'gallowglass',
        'metadata': {
            'description': 'Gallowglass — LLM-first language compiling to PLAN',
        },
    }

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, 'kernel.json')
        with open(path, 'w') as f:
            json.dump(spec, f, indent=2)
        ksm = KernelSpecManager()
        installed = ksm.install_kernel_spec(td, kernel_name='gallowglass',
                                             user=user, prefix=prefix)
    return installed


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------

def _cli_main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == 'install':
        installed = _install_kernelspec(user=True)
        print(f'Installed Gallowglass kernelspec at {installed}')
        return 0
    # Default: launch the kernel.
    _kernel_main()
    return 0


if __name__ == '__main__':
    sys.exit(_cli_main(sys.argv))

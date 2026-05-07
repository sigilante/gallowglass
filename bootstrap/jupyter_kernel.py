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

# Prelude loading + typecheck-capture both reuse the MCP server's
# snapshot machinery — both tools want the same "compile prelude once,
# thread through every subsequent build" shape. Importing functions
# (not module state) means we do not share MCP's process-global cache.
from bootstrap.mcp_server import (
    load_prelude, PreludeSnapshot, _typecheck_capture,
)


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

        Three attempts in order:

        1. **Show-aware expression mode.** Wrap as
           ``let _cell_N = <code>; let _show_N : Text = display _cell_N``
           where ``display`` is a constrained ``Show a => a → Text``
           function. If typecheck succeeds, the cell's value has a
           Show instance and we evaluate ``_show_N`` (the rendered
           Text). This is the path the user wants — pretty output.

        2. **Plain expression mode.** If (1) fails to typecheck (no
           Show instance for the cell's type, e.g. functions), fall
           back to evaluating ``_cell_N`` directly and rendering
           structurally.

        3. **Program-fragment mode.** If both expression-mode parses
           fail, treat the cell as one or more top-level decls and
           append to the accumulator.
        """
        if not source.strip():
            return CellResult(decls_only=True)

        self._cell_counter += 1
        cell_name = f'_cell_{self._cell_counter}'

        # Attempt 1: Show-aware expression mode.
        try:
            text_value = self._eval_show_expression(source, cell_name)
        except _NotAnExpression:
            pass
        except _CellError:
            # Show-mode-specific failures (no Show instance, etc.) —
            # fall through to plain expression mode for the same wrap
            # without the display call.
            pass
        else:
            return CellResult(value_text=self._render_text_value(text_value))

        # Attempt 2: plain expression mode (structural render).
        wrapped = self._wrap_as_expression(source, cell_name)
        try:
            value = self._eval_expression(wrapped, cell_name)
        except _NotAnExpression:
            pass
        except _CellError as e:
            return CellResult(error=e.envelope)
        else:
            return CellResult(value_text=self._format_value(value))

        # Attempt 3: program mode. The cell contributes declarations
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

    def _wrap_as_show_expression(self, source: str, cell_name: str) -> str:
        """Wrap the cell with a Show-aware reducer.

        The bootstrap codegen dispatches typeclass methods only at
        constrained-let call sites, so we need a per-cell ``display``
        wrapper to call ``show``. The wrapper is locally scoped — it
        doesn't pollute the user's accumulator namespace.

        The synthesised source looks like::

            <accumulated>
            use Core.Text { Show, show }
            let _show_<N>_display : ∀ a. Show a => a → Text = λ x → show x
            let _cell_<N> = <source>
            let _show_<N> : Text = _show_<N>_display _cell_<N>
        """
        prefix = self._accumulated_source
        if prefix and not prefix.endswith('\n'):
            prefix = prefix + '\n'
        display = f'_show_{self._cell_counter}_display'
        show_name = f'_show_{self._cell_counter}'
        return (
            f'{prefix}'
            f'use Core.Text {{ Show, show }}\n'
            f'let {display} : ∀ a. Show a => a → Text = λ x → show x\n'
            f'let {cell_name} = {source}\n'
            f'let {show_name} : Text = {display} {cell_name}\n'
        )

    def _eval_show_expression(self, source: str, cell_name: str) -> Any:
        """Compile a Show-aware wrap and force the rendered Text.

        Returns the forced PLAN value of ``_show_N`` (a Text =
        ``A(byte_length, content_nat)``) when Show resolves cleanly.
        Raises ``_NotAnExpression`` if even the wrap doesn't parse,
        or ``_CellError`` if any pipeline phase fails — including
        the typecheck failure that signals "this cell's type has no
        Show instance," which is the common reason to fall through
        to plain expression mode.

        When the cell's value forces to something that *isn't* a Text
        pair (the nested-dictionary insertion path the bootstrap
        codegen doesn't fully implement for constrained instances —
        e.g. ``Show a => Show Pair`` leaves the inner ``show`` in a
        partially-applied state), this also raises
        ``_NotAnExpression`` so the caller falls back to structural
        render. That keeps user-visible output honest: either Show
        gave us a clean Text, or we surface the underlying tree.
        """
        wrapped = self._wrap_as_show_expression(source, cell_name)
        filename = f'<cell {cell_name} (show)>'
        try:
            tokens = lex(wrapped, filename)
            program = parse(tokens, filename)
        except ParseError:
            raise _NotAnExpression()

        try:
            resolved, env = resolve(program, self.module,
                                    self.prelude.module_envs, filename)
            expr_types = self._typecheck(resolved, env, filename)
            compiled = self._compile_with_prelude(resolved, expr_types)
        except (ScopeError, TypecheckError, CodegenError) as e:
            raise _CellError(_error_envelope(_stage_for(e), e)) from e

        show_fq = f'{self.module}._show_{self._cell_counter}'
        if show_fq not in compiled:
            raise _NotAnExpression()

        forced = self._force(compiled[show_fq])

        # Sanity-check the forced shape. A clean Text is
        # ``A(N(byte_length), N(content_nat))``. Anything else
        # (typically a half-applied ``show`` from the constrained-
        # instance codegen gap) means the Show path didn't fully
        # reduce — fall back to structural render instead of showing
        # a confusing ``<pin show>`` tree to the user.
        if not (is_app(forced) and is_nat(forced.fun) and is_nat(forced.arg)):
            raise _NotAnExpression()
        return forced

    def _eval_expression(self, wrapped_source: str, cell_name: str) -> Any:
        """Compile ``wrapped_source`` and evaluate the cell binding.

        Raises ``_NotAnExpression`` if the wrap fails to parse — that's
        the signal to fall back to program mode. Other pipeline errors
        raise ``_CellError`` with a structured envelope.

        Pipeline includes typechecking so class-method calls (``show``,
        ``eq``, ``compare``, …) get their dictionary args inserted at
        codegen time. Without the typecheck, ``show 42`` would surface
        as a codegen ``unbound variable`` error.
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
            resolved, env = resolve(program, self.module,
                                    self.prelude.module_envs, filename)
            expr_types = self._typecheck(resolved, env, filename)
            compiled = self._compile_with_prelude(resolved, expr_types)
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
            resolved, env = resolve(program, self.module,
                                    self.prelude.module_envs, filename)
            expr_types = self._typecheck(resolved, env, filename)
            self._compile_with_prelude(resolved, expr_types)
        except (ParseError, ScopeError, TypecheckError, CodegenError) as e:
            raise _CellError(_error_envelope(_stage_for(e), e)) from e

        # All phases succeeded — commit the new source.
        self._accumulated_source = candidate

    # ------------------------------------------------------------------
    # Compile + evaluate helpers
    # ------------------------------------------------------------------

    def _typecheck(self, resolved, env, filename: str) -> dict:
        """Typecheck against the prelude priors and return the
        per-expression type map.

        ``expr_types`` is what codegen needs to insert dictionary
        arguments at constrained call sites — without it, calls to
        class methods (``show``, ``eq``, …) surface as ``unbound
        variable`` codegen errors.
        """
        _te, _tc, expr_types = _typecheck_capture(
            resolved, env, self.module, filename,
            prior_te=self.prelude.type_env,
            prior_tc=self.prelude.type_constructors,
            record_expr_types=True,
        )
        return expr_types or {}

    def _compile_with_prelude(self, resolved, expr_types: dict) -> dict:
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
            expr_types=expr_types,
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
        """Render a PLAN value as text/plain (structural fallback).

        Used when the Show-aware path doesn't fire (cell evaluates to
        a value with no Show instance, such as a function). Renders:

        * ``Nat`` as a decimal literal.
        * ``Pin`` as ``<pin {inner}>``.
        * ``Law`` as ``<law arity=N name="...">``.
        * ``App`` as ``(fun arg)``, structural.

        Cells whose result type has a Show instance render via
        ``_render_text_value`` instead — that path produces the
        user-friendly representation the Show typeclass defines.
        """
        return _render(val, depth=0)

    def _render_text_value(self, text_val: Any) -> str:
        """Decode a Gallowglass Text value to a Python str.

        Text is encoded as ``A(byte_length, content_nat)`` where
        ``content_nat`` is the UTF-8 bytes packed little-endian.
        Phase G #2's BPLAN-backed pack/unpack means the value here
        is always already in this canonical pair shape after
        ``bevaluate``. Decoding errors fall back to the structural
        renderer rather than dropping the result.
        """
        if is_app(text_val) and is_nat(text_val.fun) and is_nat(text_val.arg):
            byte_length = int(text_val.fun)
            content_nat = int(text_val.arg)
            if byte_length == 0:
                return ''
            try:
                return content_nat.to_bytes(byte_length, 'little').decode('utf-8')
            except (OverflowError, UnicodeDecodeError):
                pass
        # Unexpected shape — surface structurally rather than silently
        # dropping the cell result.
        return self._format_value(text_val)


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

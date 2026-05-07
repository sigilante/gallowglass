#!/usr/bin/env python3
"""
Build script for tutorials/04-effects-and-handlers.ipynb. See
tutorials/_build_lesson_01.py for the pattern.

Effects are a real Gallowglass feature but running effectful
computations interactively from kernel cells requires a `run`
helper the typechecker doesn't currently expose. This lesson
covers the *syntax* (effect declarations, type annotations with
effect rows, handler shape, the `External` effect for VM
boundaries, the `Abort` invariant) and stops short of executing
handler results — that's noted explicitly so a reader doesn't try
to run cells that won't.
"""

from __future__ import annotations

import os
import sys
from typing import Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import nbformat
from nbformat import v4

from bootstrap.jupyter_kernel import GallowglassEvaluator


CELLS: list[tuple[str, str]] = [
    ('md', '''# Effects and handlers

Gallowglass tracks effects in type signatures. A pure function has \
an empty effect row; an I/O-performing function carries `IO` in its \
row; an exception-raising function carries `Exn`. The compiler \
discharges effects through `handle` expressions.

This lesson covers the surface syntax — declarations, signatures, \
the `handle` shape — plus the design rule that makes the system \
honest. The runtime story has one wrinkle the kernel doesn\'t \
expose interactively yet (running a CPS-encoded handler result \
needs a helper the typechecker doesn\'t know about); that\'s noted \
explicitly below.

This lesson assumes you\'ve worked through the previous three.'''),

    ('md', '## Pure functions: the empty row\n\n'
           'A function with no annotated effect row is pure. The '
           'kernel\'s decl-summary shows the inferred type without '
           'a row when the row is empty:'),
    ('code', 'let double : Nat → Nat = λ n → n + n'),

    ('md', '## Effectful signatures\n\n'
           'Effect rows appear in curly braces between the arrow '
           'and the result type: `Nat → {Counter} Nat` is a '
           'function from `Nat` to `Nat` that performs `Counter` '
           'effects on the way.\n\n'
           'Define an effect with `eff`:'),
    ('code', 'eff Counter {\n'
            '  inc : Nat → Nat\n'
            '}'),

    ('md', 'Now functions that perform `Counter.inc` carry it in '
           'their row. The decl summary shows the row as part of '
           'the type:'),
    ('code', 'let bumped : Nat → {Counter} Nat = λ n → inc n'),

    ('md', '## The `handle` form\n\n'
           '`handle` discharges an effect locally. The shape is:\n\n'
           '```\n'
           'handle <expression> {\n'
           '  | return xx       → <pure-result branch>\n'
           '  | <op_name> arg k → <continuation branch>\n'
           '}\n'
           '```\n\n'
           '`return xx` is the arm that fires when the inner '
           'expression evaluates to a pure value `xx`. Each effect-op '
           'arm receives the operation\'s argument plus a '
           '`continuation` `k` — calling `k value` resumes the '
           'computation with `value` as the op\'s result.\n\n'
           'Define a handler that intercepts `inc`:'),
    ('code', 'let result = handle (inc 0) {\n'
            '  | return xx → xx\n'
            '  | inc _ kk  → kk 42\n'
            '}'),

    ('md', 'Notice the inferred type — `result` has shape '
           '`{∅ | r} Nat`, a CPS-encoded computation that, when '
           'run, would discharge the `Counter` effect and produce '
           '`42`.\n\n'
           '**Wrinkle:** the kernel doesn\'t currently expose the '
           '`run` helper that takes a CPS computation and produces '
           'its underlying value. So evaluating `result` directly '
           'in a cell shows the unrun computation as a Law, not '
           'the integer it would compute to. The `tests/bootstrap/'
           'test_codegen.py` suite exercises `run` via a Python '
           'helper; making it accessible from cells is forward '
           'work.\n\n'
           'You can still see the result\'s structure:'),
    ('code', 'result'),

    ('md', '## The `External` effect — VM boundaries\n\n'
           'Operations that cross the VM boundary (filesystem, '
           'process I/O, foreign calls) carry the `External` '
           'effect. `external mod` declarations register the type '
           'and effect of each operation; the bootstrap registers '
           'them as Pin\'d Laws that delegate to the underlying '
           'BPLAN or RPLAN op.\n\n'
           'The pre-existing `Reaver.RPLAN` declarations look like '
           'this (no need to re-declare; this is illustrative):\n\n'
           '```gallowglass\n'
           'external mod Reaver.RPLAN {\n'
           '  output : Nat → Nat\n'
           '  input  : Nat → Nat\n'
           '}\n'
           '```\n\n'
           'A function calling these operations would carry the '
           'effect in its row — typically `{External | r}` so the '
           'caller can extend with their own effects.'),

    ('md', '## The `Abort` invariant\n\n'
           '`Abort` is **not** in any effect row. It\'s the unhandle-'
           'able effect that propagates straight to the VM\'s '
           'virtualization supervisor — the runtime equivalent of '
           '"this computation cannot continue." Because it\'s never '
           'in a row, it can\'t be handled by user code, and the '
           'compiler enforces this at the row-typing level.\n\n'
           'You won\'t encounter `Abort` directly when writing '
           'Gallowglass — it surfaces only when the runtime has to '
           'give up (out-of-memory, unrecoverable invariant '
           'violation, etc.). It exists in the design as a hard '
           'floor so handler logic stays focused on '
           'recoverable effects.'),

    ('md', '## What you\'ve seen and where it goes from here\n\n'
           'Effect rows give the type signature *visible* — anyone '
           'reading `read_file : Path → {IO, Exn IOError | r} '
           'Bytes` knows what side-effects the call performs '
           'without checking the implementation. Handlers '
           'discharge those effects locally with explicit '
           'continuation control.\n\n'
           'For the full reference, see `doc/language-guide.md` '
           'sections on the effect system and `spec/05-type-system.md` '
           'for the formal row-typing rules. The `tests/bootstrap/'
           'test_codegen.py::test_eff_*` tests are the most '
           'compact illustration of the CPS encoding the bootstrap '
           'uses, including the `run` helper that\'s missing from '
           'the kernel today.\n\n'
           'You\'ve now worked through the four core notebooks: '
           'declarations and pattern matching (lesson 1), '
           'typeclasses (lesson 2), Glass IR (lesson 3), and '
           'effects (this one). Together they cover everything '
           'you need to read most Gallowglass source. For the '
           'LLM-shaped condensed reference, see '
           '`doc/phrasebook.md`.'),
]


def _render_outputs(text: str | None, html: str | None,
                    execution_count: int) -> list[Any]:
    if text is None and html is None:
        return []
    data: dict[str, Any] = {}
    if text is not None:
        data['text/plain'] = text
    if html is not None:
        data['text/html'] = html
    return [v4.new_output('execute_result', data=data,
                          execution_count=execution_count, metadata={})]


def main() -> None:
    nb = v4.new_notebook()
    nb.metadata['kernelspec'] = {
        'name': 'gallowglass',
        'display_name': 'Gallowglass',
        'language': 'gallowglass',
    }
    nb.metadata['language_info'] = {
        'name': 'gallowglass',
        'mimetype': 'text/x-gallowglass',
        'file_extension': '.gls',
        'pygments_lexer': 'haskell',
    }

    evaluator = GallowglassEvaluator()
    exec_count = 0

    for kind, body in CELLS:
        if kind == 'md':
            nb.cells.append(v4.new_markdown_cell(body, id=f"md-{len(nb.cells):02d}"))
            continue
        exec_count += 1
        result = evaluator.eval_cell(body)
        if result.error is not None:
            print(f'WARN: cell {exec_count} errored: {result.error}',
                  file=sys.stderr)
        outputs = _render_outputs(result.value_text, result.value_html,
                                  execution_count=exec_count)
        cell = v4.new_code_cell(source=body, outputs=outputs, id=f"code-{exec_count:02d}")
        cell['execution_count'] = exec_count
        nb.cells.append(cell)

    out_path = os.path.join(os.path.dirname(__file__),
                            '04-effects-and-handlers.ipynb')
    with open(out_path, 'w') as f:
        nbformat.write(nb, f)
    print(f'wrote {out_path}')


if __name__ == '__main__':
    main()

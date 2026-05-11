#!/usr/bin/env python3
"""
Gallowglass interactive REPL.

Input rules
-----------
* Lines starting with ``let``, ``type``, ``instance``, ``class``, or ``use``
  are declarations — input continues until a blank line is entered.
* Everything else is treated as an expression and submitted on the first Enter.
* A blank line always flushes whatever has been collected.
* Ctrl-D (EOF) exits cleanly.

Meta-commands (prefix ``:``)
----------------------------
    :type <name>   — print the type of a name in scope
    :load <path>   — load and evaluate a .gls source file
    :reset         — discard all accumulated declarations (keep prelude)
    :quit          — exit
    :help          — print this message
"""

from __future__ import annotations

import os
import re
import readline
import sys

from bootstrap.jupyter_kernel import GallowglassEvaluator, CellResult

_BANNER = (
    'Gallowglass  (:help for commands, Ctrl-D to exit)\n'
    'Declarations (let/type/…) collect until a blank line; expressions submit on Enter.'
)

_HELP = """\
Gallowglass REPL

  :type <name>   print the type of a name currently in scope
  :load <path>   load and evaluate a .gls source file
  :reset         discard accumulated declarations (prelude stays)
  :quit          exit  (also Ctrl-D)
  :help          this message

Input:
  Expressions submit on Enter:
    gg> add 1 2
    3

  Declarations collect until a blank line:
    gg> let double : Nat → Nat
    ..    = λ n → add n n
    ..
    double : Nat → Nat

  Multi-line expressions — wrap in a let, evaluate the name:
    gg> let result =
    ..      match opt { | None → 0 | Some x → x }
    ..
    gg> result
"""

_DECL_PREFIXES = (
    'let ', 'let\t',
    'type ', 'type\t',
    'instance ', 'instance\t',
    'class ', 'class\t',
    'use ', 'use\t',
)

_META_COMMANDS = [
    ':type', ':load', ':reset', ':quit', ':help',
    ':r', ':q', ':h',
]

_ANSI_RE = re.compile(r'\x1b\[[0-9;]*m')


def _is_decl_start(line: str) -> bool:
    stripped = line.lstrip()
    return any(stripped.startswith(p) for p in _DECL_PREFIXES)


def _make_completer(ev: GallowglassEvaluator):
    """Return a readline completer bound to the evaluator's live scope."""
    def completer(text: str, state: int) -> str | None:
        if text.startswith(':'):
            matches = [c for c in _META_COMMANDS if c.startswith(text)]
        else:
            matches = [n for n in ev.names_in_scope() if n.startswith(text)]
        try:
            return matches[state]
        except IndexError:
            return None
    return completer


def _read_cell() -> str | None:
    """Read one logical cell from stdin.

    Returns the source string, or None on EOF with no pending input.
    """
    lines: list[str] = []
    prompt = 'gg> '

    while True:
        try:
            line = input(prompt)
        except EOFError:
            print()
            return '\n'.join(lines) if lines else None

        # Leading blank lines before any input: ignore.
        if not line and not lines:
            continue

        # Blank line: submit whatever we have.
        if not line:
            break

        lines.append(line)

        # Single-line expression: submit immediately.
        if len(lines) == 1 and not _is_decl_start(line):
            break

        # Declaration (or already multi-line): keep collecting.
        prompt = '.. '

    return '\n'.join(lines)


def _format_error(err: dict) -> str:
    tb = err.get('traceback', [])
    if tb:
        return '\n'.join(_ANSI_RE.sub('', ln) for ln in tb)
    etype = err.get('etype', 'Error')
    val = err.get('evalue', '')
    return f'{etype}: {val}' if val else etype


def _cmd_type(ev: GallowglassEvaluator, args: str) -> None:
    name = args.strip()
    if not name:
        print(':type requires a name', file=sys.stderr)
        return
    scheme = ev.query_type(name)
    if scheme is None:
        print(f'{name} is not in scope', file=sys.stderr)
    else:
        print(f'{name} : {scheme}')


def _cmd_load(ev: GallowglassEvaluator, args: str) -> None:
    path = args.strip()
    if not path:
        print(':load requires a file path', file=sys.stderr)
        return
    path = os.path.expanduser(path)
    try:
        src = open(path).read()
    except OSError as e:
        print(f'cannot read {path}: {e}', file=sys.stderr)
        return
    result: CellResult = ev.eval_cell(src)
    if result.error:
        print(_format_error(result.error), file=sys.stderr)
    elif result.value_text:
        print(result.value_text)


def main() -> None:
    print(_BANNER)
    ev = GallowglassEvaluator()

    readline.set_completer(_make_completer(ev))
    readline.parse_and_bind('tab: complete')

    while True:
        src = _read_cell()
        if src is None:
            break

        src = src.strip()
        if not src:
            continue

        # Meta-commands.
        if src.startswith(':'):
            parts = src.split(None, 1)
            cmd = parts[0]
            rest = parts[1] if len(parts) > 1 else ''
            if cmd in (':quit', ':q'):
                break
            elif cmd in (':reset', ':r'):
                ev.reset()
                print('(declarations cleared)')
            elif cmd in (':help', ':h', ':?'):
                print(_HELP)
            elif cmd == ':type':
                _cmd_type(ev, rest)
            elif cmd == ':load':
                _cmd_load(ev, rest)
            else:
                print(f"unknown command {cmd!r}  (:help for commands)")
            continue

        result: CellResult = ev.eval_cell(src)

        if result.error:
            print(_format_error(result.error), file=sys.stderr)
        elif result.value_text:
            print(result.value_text)
        # decls_only + no value_text → silent


if __name__ == '__main__':
    main()

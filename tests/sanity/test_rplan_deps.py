#!/usr/bin/env python3
"""
Sanity test: every RPLAN named-op gallowglass codegen depends on must exist
in the pinned `vendor/reaver/src/hs/Plan.hs:rplan` at the right arity.

Per Sol (2026-04-30), RPLAN is **tentative, not frozen** — names, arities,
and the calling shape may change in future Reaver versions.  This canary
fires when `vendor.lock` is bumped to a Reaver SHA where an RPLAN op has
been renamed, removed, or had its arity changed.  Failure here means
either:
  (a) we need to update bootstrap/rplan_deps.py (and codegen + the
      Reaver.RPLAN prelude module) to match the new upstream, or
  (b) the SHA bump should be reverted.

Run: python3 -m pytest tests/sanity/test_rplan_deps.py -v
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.rplan_deps import RPLAN_OPS


REPO_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
PLAN_HS = os.path.join(REPO_ROOT, 'vendor', 'reaver', 'src', 'hs', 'Plan.hs')


def parse_rplan(plan_hs_text: str) -> dict[str, int]:
    """Extract `["Name", arg1, ..., argN] -> ...` cases from `rplan` in Plan.hs.

    The dispatch lives inside `rplan args = do ... case args of` and uses
    bracketed list patterns. We scan from the `rplan` definition forward
    until the catch-all `_ -> error ...` line and parse every
    `["Name", ...]` pattern in between.

    Returns {name: arity} where arity = number of args after the name.
    Pattern names and constructor wrappers (`N p`, `!a`) are normalized
    away — only comma count matters for our compatibility check.
    """
    # Find the `rplan` function block. It begins with a top-level
    # `rplan :: ...` signature and ends at the catch-all in its case-of.
    start_m = re.search(r'^rplan\s*::', plan_hs_text, re.MULTILINE)
    if start_m is None:
        return {}
    # The block's catch-all is `_ -> error ("unknown actor/net op:` —
    # use that as the end anchor.
    block_start = start_m.start()
    end_m = re.search(r'^\s*_\s*->\s*error\s*\("unknown actor/net op:',
                      plan_hs_text[block_start:], re.MULTILINE)
    if end_m is None:
        # No anchor found — be conservative and parse the rest of the file.
        block = plan_hs_text[block_start:]
    else:
        block = plan_hs_text[block_start:block_start + end_m.start()]

    found: dict[str, int] = {}
    pat = re.compile(r'^\s*\[\s*"([^"]+)"\s*((?:,\s*[^]]*)?)\]')
    for raw in block.splitlines():
        m = pat.match(raw)
        if not m:
            continue
        name = m.group(1)
        rest = m.group(2)
        # Count commas in the args section; n commas == n args after the name.
        # `,` only — top-level. (RPLAN patterns don't nest list brackets.)
        if rest.strip():
            arity = rest.count(',')
        else:
            arity = 0
        if name not in found:
            found[name] = arity
    return found


class TestRplanDeps(unittest.TestCase):
    """Every dep in bootstrap/rplan_deps.py must exist in Plan.hs:rplan."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(PLAN_HS):
            raise unittest.SkipTest(
                f'{PLAN_HS} not found — run tools/vendor.sh to populate vendor/'
            )
        with open(PLAN_HS, 'r', encoding='utf-8') as f:
            cls.plan_hs_text = f.read()
        cls.upstream = parse_rplan(cls.plan_hs_text)

    def test_rplan_parser_finds_at_least_seven_ops(self):
        """Sanity check on the parser itself: at minimum, the I/O subset
        we depend on must parse out.  Plan.hs's rplan has more ops
        (Spawn/Send/Recv/...); we don't bind those but the parser should
        still reach them."""
        self.assertGreaterEqual(len(self.upstream), 7,
            f'parse_rplan only found {len(self.upstream)} ops — '
            f'parser likely broken. Plan.hs path: {PLAN_HS}')

    def test_all_deps_present(self):
        """Every rplan_deps entry must exist in Plan.hs:rplan at the right arity."""
        missing = []
        wrong_arity = []
        for name, expected_arity in RPLAN_OPS.items():
            if name not in self.upstream:
                missing.append(name)
                continue
            got = self.upstream[name]
            if got != expected_arity:
                wrong_arity.append((name, expected_arity, got))

        msg_parts = []
        if missing:
            msg_parts.append(f'missing from Plan.hs:rplan: {missing}')
        if wrong_arity:
            msg_parts.append(
                'arity mismatch: ' +
                ', '.join(f'{n} (declared={d}, upstream={u})'
                          for n, d, u in wrong_arity)
            )
        if msg_parts:
            self.fail(
                'RPLAN dep drift detected.\n'
                + '\n'.join(msg_parts)
                + f'\nPlan.hs path: {PLAN_HS}\n'
                + 'Per Sol, RPLAN is tentative — bump '
                + 'bootstrap/rplan_deps.py + Reaver.RPLAN.gls + '
                + 'codegen._REAVER_RPLAN_PRIMS to match upstream, or '
                + 'revert the vendor.lock SHA bump.'
            )


if __name__ == '__main__':
    unittest.main()

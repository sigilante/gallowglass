#!/usr/bin/env python3
"""
Sanity test: every BPLAN named-op gallowglass codegen depends on must exist
in the pinned `vendor/reaver/src/hs/Plan.hs` at the right arity.

This is the canary that fires when `vendor.lock` is bumped to a Reaver SHA
where an op has been renamed, removed, or had its arity changed. Failure
here means either:
  (a) we need to update bootstrap/bplan_deps.py to match the new upstream
      (and likely follow up with codegen changes), or
  (b) the SHA bump should be reverted.

Run: python3 -m pytest tests/sanity/test_bplan_deps.py -v
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from bootstrap.bplan_deps import ALL_DEPS


REPO_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
PLAN_HS = os.path.join(REPO_ROOT, 'vendor', 'reaver', 'src', 'hs', 'Plan.hs')


def parse_op66(plan_hs_text: str) -> dict[str, int]:
    """Extract `op 66 ["Name", arg1, ..., argN]` cases from Plan.hs.

    Returns {name: arity} where arity = number of args after the name.
    Bang-patterns (!a) and pattern names are normalized away — only the
    count of args matters for our compatibility check.
    """
    found: dict[str, int] = {}
    pat = re.compile(r'^op\s+66\s+\[\s*"([^"]+)"\s*((?:,\s*[!a-zA-Z0-9_]+\s*)*)\]')
    for raw in plan_hs_text.splitlines():
        line = raw.strip()
        m = pat.match(line)
        if not m:
            continue
        name = m.group(1)
        rest = m.group(2)
        # Count commas in the args section; n commas == n args after the name.
        if rest.strip():
            arity = rest.count(',')
        else:
            arity = 0
        # Don't overwrite a previous definition; the first match wins.
        # (Plan.hs has duplicate Last/Init definitions for different shapes.)
        if name not in found:
            found[name] = arity
    return found


class TestBplanDeps(unittest.TestCase):
    """Every dep in bootstrap/bplan_deps.py must exist in the pinned Plan.hs."""

    @classmethod
    def setUpClass(cls):
        if not os.path.isfile(PLAN_HS):
            raise unittest.SkipTest(
                f'{PLAN_HS} not found — run tools/vendor.sh to populate vendor/'
            )
        with open(PLAN_HS, 'r', encoding='utf-8') as f:
            cls.plan_hs_text = f.read()
        cls.upstream = parse_op66(cls.plan_hs_text)

    def test_op66_parser_finds_at_least_30_ops(self):
        """Sanity check on the parser itself."""
        self.assertGreater(len(self.upstream), 30,
            f'parse_op66 only found {len(self.upstream)} ops — '
            f'parser likely broken. Plan.hs path: {PLAN_HS}')

    def test_all_deps_present(self):
        """Every bplan_deps entry must exist in Plan.hs at the right arity."""
        missing = []
        wrong_arity = []
        for name, expected_arity in ALL_DEPS.items():
            if name not in self.upstream:
                missing.append(name)
                continue
            got = self.upstream[name]
            if got != expected_arity:
                wrong_arity.append((name, expected_arity, got))

        msg_parts = []
        if missing:
            msg_parts.append(f'missing from Plan.hs: {missing}')
        if wrong_arity:
            msg_parts.append(
                'arity mismatch: ' +
                ', '.join(f'{n} (declared={d}, upstream={u})'
                          for n, d, u in wrong_arity)
            )
        if msg_parts:
            self.fail(
                'BPLAN dep drift detected.\n'
                + '\n'.join(msg_parts)
                + f'\nPlan.hs path: {PLAN_HS}'
            )


if __name__ == '__main__':
    unittest.main()

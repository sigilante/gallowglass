#!/usr/bin/env python3
"""
Bootstrap compiler tests.

These tests grow with the compiler milestones defined in bootstrap/BOOTSTRAP.md.
Currently: structural checks only (Milestone 0 placeholder).

Run: python3 tests/bootstrap/test_bootstrap.py
"""

import sys
import os

REPO_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
BOOTSTRAP_SRC = os.path.join(REPO_ROOT, 'bootstrap', 'src')

EXPECTED_SOURCES = [
    'main.sire',
    'prelude.sire',
    'token.sire',
    'lexer.sire',
    'ast.sire',
    'parser.sire',
    'scope.sire',
    'typecheck.sire',
    'lower.sire',
    'codegen.sire',
    'emit.sire',
]


# ============================================================
# Milestone 0: Structural checks
# ============================================================

def test_bootstrap_src_exists():
    """bootstrap/src/ directory exists."""
    assert os.path.isdir(BOOTSTRAP_SRC), \
        f"bootstrap/src/ not found at {BOOTSTRAP_SRC}"


def test_all_source_stubs_present():
    """All expected Sire source files are present."""
    missing = []
    for name in EXPECTED_SOURCES:
        path = os.path.join(BOOTSTRAP_SRC, name)
        if not os.path.isfile(path):
            missing.append(name)
    assert not missing, f"Missing bootstrap sources: {missing}"


def test_bootstrap_md_exists():
    """bootstrap/BOOTSTRAP.md exists."""
    path = os.path.join(REPO_ROOT, 'bootstrap', 'BOOTSTRAP.md')
    assert os.path.isfile(path), "bootstrap/BOOTSTRAP.md not found"


def test_bootstrap_md_has_milestones():
    """BOOTSTRAP.md documents milestones."""
    path = os.path.join(REPO_ROOT, 'bootstrap', 'BOOTSTRAP.md')
    with open(path) as f:
        content = f.read()
    assert 'Milestone' in content, "BOOTSTRAP.md missing Milestone section"
    assert 'restricted dialect' in content.lower() or 'Restricted Dialect' in content, \
        "BOOTSTRAP.md missing restricted dialect section"


# ============================================================
# Run as script
# ============================================================

if __name__ == '__main__':
    tests = [(name, obj) for name, obj in globals().items()
             if name.startswith('test_') and callable(obj)]
    passed = failed = 0
    for name, fn in sorted(tests):
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except (AssertionError, Exception) as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(0 if failed == 0 else 1)

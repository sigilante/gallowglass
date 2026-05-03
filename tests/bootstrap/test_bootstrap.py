#!/usr/bin/env python3
"""
Bootstrap compiler structural tests.

Verifies that the Python bootstrap compiler modules are present and that
no archive/Sire stubs have been re-introduced.

Run: python3 tests/bootstrap/test_bootstrap.py
"""

import sys
import os

REPO_ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
BOOTSTRAP_DIR = os.path.join(REPO_ROOT, 'bootstrap')

# The Python bootstrap compiler modules (bootstrap/*.py)
EXPECTED_PYTHON_MODULES = [
    'lexer.py',
    'parser.py',
    'scope.py',
    'typecheck.py',
    'codegen.py',
    'emit_seed.py',
    'glass_ir.py',
    'ast.py',
]

# ============================================================
# Structural checks
# ============================================================

def test_bootstrap_python_modules_present():
    """All Python bootstrap compiler modules exist in bootstrap/."""
    missing = []
    for name in EXPECTED_PYTHON_MODULES:
        path = os.path.join(BOOTSTRAP_DIR, name)
        if not os.path.isfile(path):
            missing.append(name)
    assert not missing, f"Missing bootstrap Python modules: {missing}"


def test_no_sire_stubs_in_tree():
    """Sire stubs have no consumers and git history preserves them.
    This test guards against accidental re-introduction."""
    archive_dir = os.path.join(BOOTSTRAP_DIR, 'archive')
    assert not os.path.exists(archive_dir), \
        f"bootstrap/archive/ resurfaced at {archive_dir}"


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

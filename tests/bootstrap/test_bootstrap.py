#!/usr/bin/env python3
"""
Bootstrap compiler structural tests.

Verifies that the Python bootstrap compiler modules are present and that
BOOTSTRAP.md documents the current milestone status.

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
    'emit.py',
    'glass_ir.py',
    'ast.py',
]

# The archived Sire stubs (moved to bootstrap/archive/sire/)
ARCHIVED_SIRE_STUBS = os.path.join(BOOTSTRAP_DIR, 'archive', 'sire')


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


def test_sire_stubs_archived():
    """Sire stubs are in bootstrap/archive/sire/ (not in bootstrap/src/)."""
    archive_readme = os.path.join(ARCHIVED_SIRE_STUBS, 'README.md')
    assert os.path.isfile(archive_readme), \
        "bootstrap/archive/sire/README.md not found — Sire stubs should be archived there"


def test_bootstrap_md_exists():
    """bootstrap/BOOTSTRAP.md exists."""
    path = os.path.join(BOOTSTRAP_DIR, 'BOOTSTRAP.md')
    assert os.path.isfile(path), "bootstrap/BOOTSTRAP.md not found"


def test_bootstrap_md_has_milestones():
    """BOOTSTRAP.md documents milestones and the Python-first approach."""
    path = os.path.join(BOOTSTRAP_DIR, 'BOOTSTRAP.md')
    with open(path) as f:
        content = f.read()
    assert 'Milestone' in content, "BOOTSTRAP.md missing Milestone section"
    assert 'Restricted Dialect' in content or 'restricted dialect' in content.lower(), \
        "BOOTSTRAP.md missing restricted dialect section"
    assert 'Python' in content, \
        "BOOTSTRAP.md should document the Python-first bootstrap approach"


def test_bootstrap_md_milestones_complete():
    """BOOTSTRAP.md marks Milestones 1–5 as complete."""
    path = os.path.join(BOOTSTRAP_DIR, 'BOOTSTRAP.md')
    with open(path) as f:
        content = f.read()
    for i in range(1, 6):
        assert f'Milestone {i}' in content, \
            f"BOOTSTRAP.md missing Milestone {i}"
    # Milestones 1–5 should be marked complete
    assert '✅' in content, \
        "BOOTSTRAP.md should mark completed milestones with ✅"


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

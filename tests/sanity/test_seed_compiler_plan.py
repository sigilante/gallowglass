#!/usr/bin/env python3
"""Seed Compiler.plan integrity check.

The vendored `compiler/dist/Compiler.plan` is the seed for Python-less
self-host builds (`tools/build-self-host.sh`).  Its BLAKE3 is pinned in
`compiler/dist/MANIFEST.json` so any drift between the on-disk artifact
and the recorded hash is caught immediately — at the unit-test level,
without needing Reaver.

When `compiler/src/Compiler.gls` legitimately changes:
  1. Re-bootstrap the seed (Python: `bootstrap.codegen.compile_program`).
  2. Overwrite `compiler/dist/Compiler.plan`.
  3. Update `compiler/dist/MANIFEST.json` with the new BLAKE3.
  4. Verify byte-identity via `tools/selfcompile.py compiler/src/Compiler.gls`.

This test catches step (3) being forgotten.
"""

import json
import os

import blake3
import pytest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
SEED_PATH = os.path.join(REPO_ROOT, 'compiler/dist/Compiler.plan')
MANIFEST_PATH = os.path.join(REPO_ROOT, 'compiler/dist/MANIFEST.json')


@pytest.fixture(scope='module')
def manifest():
    with open(MANIFEST_PATH) as f:
        return json.load(f)


def test_seed_compiler_plan_exists():
    assert os.path.isfile(SEED_PATH), (
        f'seed Compiler.plan not at {SEED_PATH} — run the Python bootstrap '
        f'to regenerate, then update compiler/dist/MANIFEST.json.'
    )


def test_seed_compiler_plan_blake3_matches_manifest(manifest):
    with open(SEED_PATH, 'rb') as f:
        data = f.read()
    actual = blake3.blake3(data).hexdigest()
    expected = manifest['compiler_plan']['blake3']
    assert actual == expected, (
        f'compiler/dist/Compiler.plan BLAKE3 drifted from MANIFEST:\n'
        f'  on-disk:  {actual}\n'
        f'  manifest: {expected}\n'
        f'If Compiler.plan was regenerated, update '
        f'compiler/dist/MANIFEST.json["compiler_plan"]["blake3"] to match.'
    )


def test_seed_compiler_plan_size_matches_manifest(manifest):
    actual = os.path.getsize(SEED_PATH)
    expected = manifest['compiler_plan']['size_bytes']
    assert actual == expected, (
        f'compiler/dist/Compiler.plan size drifted from MANIFEST:\n'
        f'  on-disk:  {actual} bytes\n'
        f'  manifest: {expected} bytes'
    )


def test_compiler_gls_source_blake3_matches_manifest(manifest):
    """If the Compiler.gls source has changed since the seed was built,
    the seed is stale — flag explicitly so the next maintainer
    re-bootstraps before relying on the Python-less build path."""
    src_path = os.path.join(REPO_ROOT, 'compiler/src/Compiler.gls')
    with open(src_path, 'rb') as f:
        data = f.read()
    actual = blake3.blake3(data).hexdigest()
    expected = manifest['compiler_source']['blake3']
    assert actual == expected, (
        f'compiler/src/Compiler.gls has changed since the seed was built:\n'
        f'  source:   {actual}\n'
        f'  manifest: {expected}\n'
        f'Re-bootstrap compiler/dist/Compiler.plan via the Python compiler, '
        f'verify byte-identity with tools/selfcompile.py, and update the '
        f'manifest accordingly.'
    )


def test_compiler_gls_source_size_matches_manifest(manifest):
    """Mirror of ``test_seed_compiler_plan_size_matches_manifest`` for
    the source.  The BLAKE3 check above guarantees content match, but
    a missing size field can hide a stale manifest from a casual
    reader.  Cheap; catches the maintenance gap surfaced in the
    Phase I angel review."""
    src_path = os.path.join(REPO_ROOT, 'compiler/src/Compiler.gls')
    actual = os.path.getsize(src_path)
    expected = manifest['compiler_source']['size_bytes']
    assert actual == expected, (
        f'compiler/src/Compiler.gls size drifted from MANIFEST:\n'
        f'  on-disk:  {actual} bytes\n'
        f'  manifest: {expected} bytes'
    )

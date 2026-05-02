#!/usr/bin/env python3
"""
Stdio smoke test for the Gallowglass MCP server.

Spawns ``python3 -m bootstrap.mcp_server`` as a subprocess, drives the
JSON-RPC handshake, calls ``compile_snippet``, and checks the result. This
test confirms that the stdio transport is wired correctly end-to-end.

Skipped if the ``mcp`` package is not importable (CI installs it; local
runs may not). Skipped on slow setups via the gallowglass repo's normal
pytest config — there's no runtime gate beyond the import.

Run: python3 -m pytest tests/bootstrap/test_mcp_stdio.py -v
"""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))


def _have_mcp() -> bool:
    try:
        import mcp  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(_have_mcp(), "mcp package not installed")
class TestMcpStdio(unittest.TestCase):
    """End-to-end stdio handshake + tool call."""

    def _send(self, proc, payload: dict) -> None:
        line = json.dumps(payload) + '\n'
        proc.stdin.write(line.encode())
        proc.stdin.flush()

    def _recv(self, proc) -> dict:
        line = proc.stdout.readline()
        if not line:
            stderr = proc.stderr.read().decode(errors='replace')
            raise AssertionError(
                f"server closed stdout unexpectedly. stderr:\n{stderr}"
            )
        return json.loads(line.decode())

    def test_initialize_and_call_compile_snippet(self):
        repo_root = os.path.normpath(
            os.path.join(os.path.dirname(__file__), '..', '..'))
        env = os.environ.copy()
        env['PYTHONPATH'] = repo_root + os.pathsep + env.get('PYTHONPATH', '')

        proc = subprocess.Popen(
            [sys.executable, '-m', 'bootstrap.mcp_server'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=repo_root,
            env=env,
        )
        try:
            # 1. initialize
            self._send(proc, {
                'jsonrpc': '2.0',
                'id': 1,
                'method': 'initialize',
                'params': {
                    'protocolVersion': '2025-06-18',
                    'capabilities': {},
                    'clientInfo': {'name': 'test', 'version': '0'},
                },
            })
            resp = self._recv(proc)
            self.assertEqual(resp.get('id'), 1)
            self.assertIn('result', resp)
            self.assertEqual(resp['result']['serverInfo']['name'],
                             'gallowglass')

            # initialized notification
            self._send(proc, {
                'jsonrpc': '2.0',
                'method': 'notifications/initialized',
            })

            # 2. tools/list
            self._send(proc, {
                'jsonrpc': '2.0',
                'id': 2,
                'method': 'tools/list',
                'params': {},
            })
            resp = self._recv(proc)
            self.assertEqual(resp.get('id'), 2)
            tool_names = {t['name'] for t in resp['result']['tools']}
            self.assertEqual(tool_names, {
                'compile_snippet', 'infer_type',
                'explain_effect_row', 'render_fragment',
            })

            # 3. tools/call compile_snippet
            self._send(proc, {
                'jsonrpc': '2.0',
                'id': 3,
                'method': 'tools/call',
                'params': {
                    'name': 'compile_snippet',
                    'arguments': {'source': 'let xx = 42'},
                },
            })
            resp = self._recv(proc)
            self.assertEqual(resp.get('id'), 3)
            content = resp['result']['content']
            self.assertEqual(len(content), 1)
            self.assertEqual(content[0]['type'], 'text')
            payload = json.loads(content[0]['text'])
            self.assertNotIn('error', payload)
            self.assertIn('let Snippet.xx', payload['ir'])
            self.assertIn('Snippet.xx', payload['pins'])
        finally:
            proc.stdin.close()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


if __name__ == '__main__':
    unittest.main()

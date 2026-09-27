"""Structural and protocol tests for the restricted read-only MCP entrypoint."""

from __future__ import annotations

import json
import re
import selectors
import subprocess
import unittest
from pathlib import Path

HERE = Path(__file__).parent
SOURCE = HERE / "src" / "readonly_index.ts"
BUILD_ENTRY = HERE / "build" / "readonly_index.js"
FULL_ENTRY = HERE / "build" / "index.js"


class ReadonlyEntrypointTests(unittest.TestCase):
    def test_build_artifact_exists(self) -> None:
        self.assertTrue(BUILD_ENTRY.is_file())

    def test_readonly_source_registers_only_analysis_tools_and_reuses_scripts(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        registered = re.findall(r'server\.registerTool\(\s*"([^"]+)"', source)
        self.assertEqual(registered, ["analyze_change", "explain_regression"])
        for forbidden in ("apply_fix", "verify_fix", "undo_fix"):
            self.assertNotIn(forbidden, source)
        self.assertIn('path.join(__dirname, "analyze.py")', source)
        self.assertIn('path.join(__dirname, "regression_tools.py")', source)
        self.assertNotIn("from \"./index", source)
        self.assertNotIn("subprocess", source)
        self.assertTrue((HERE / "src" / "analyze.py").is_file())
        self.assertTrue((HERE / "src" / "regression_tools.py").is_file())

    def _list_tools(self, entry: Path) -> set[str]:
        process = subprocess.Popen(
            ["node", str(entry)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=HERE.parent, bufsize=1,
        )
        try:
            assert process.stdin is not None and process.stdout is not None
            process.stdin.write(json.dumps({
                "jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "readonly-entrypoint-test", "version": "1.0"},
                },
            }) + "\n")
            process.stdin.flush()
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                if not selector.select(timeout=10):
                    self.fail("MCP server did not answer initialize")
                initialized = json.loads(process.stdout.readline())
            self.assertEqual(initialized.get("id"), 1)

            process.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
            process.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
            process.stdin.flush()
            tools_result = None
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                for _ in range(2):
                    if not selector.select(timeout=10):
                        self.fail("MCP server did not answer tools/list")
                    response = json.loads(process.stdout.readline())
                    if response.get("id") == 2:
                        tools_result = response["result"]
                        break
            self.assertIsNotNone(tools_result)
            return {tool["name"] for tool in tools_result["tools"]}
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()

    def test_readonly_server_exposes_exactly_two_tools(self) -> None:
        self.assertEqual(self._list_tools(BUILD_ENTRY), {"analyze_change", "explain_regression"})

    def test_full_server_still_exposes_its_original_tools(self) -> None:
        self.assertEqual(
            self._list_tools(FULL_ENTRY),
            {"impactproof_status", "analyze_change", "explain_regression", "apply_fix", "verify_fix", "undo_fix"},
        )


if __name__ == "__main__":
    unittest.main()

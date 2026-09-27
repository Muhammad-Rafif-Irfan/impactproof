"""Exercise the repository-side Bob Agent contract through the MCP server."""

from __future__ import annotations

import json
import selectors
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).parent
ROOT = HERE.parent
BUILD_ENTRY = HERE / "build" / "index.js"
sys.path.insert(0, str(HERE))
import test_regression_tools as regression_fixtures


class AgentContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = subprocess.Popen(
            ["node", str(BUILD_ENTRY)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=ROOT,
            bufsize=1,
        )
        self.next_id = 1
        initialized = self.request(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "impactproof-agent-contract-test", "version": "1.0"},
            },
        )
        self.assertIn("protocolVersion", initialized)
        self.notify("notifications/initialized")

    def tearDown(self) -> None:
        if self.server.poll() is None:
            self.server.terminate()
            try:
                self.server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.server.kill()
                self.server.wait(timeout=5)
        for stream in (self.server.stdin, self.server.stdout, self.server.stderr):
            if stream is not None:
                stream.close()

    def request(self, method: str, params: dict | None = None) -> dict:
        request_id = self.next_id
        self.next_id += 1
        message = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        assert self.server.stdin is not None and self.server.stdout is not None
        self.server.stdin.write(json.dumps(message) + "\n")
        self.server.stdin.flush()

        with selectors.DefaultSelector() as selector:
            selector.register(self.server.stdout, selectors.EVENT_READ)
            ready = selector.select(timeout=15)
        self.assertTrue(ready, f"MCP server did not answer {method}")
        response = json.loads(self.server.stdout.readline())
        self.assertEqual(response.get("id"), request_id)
        self.assertNotIn("error", response, response.get("error"))
        return response["result"]

    def notify(self, method: str, params: dict | None = None) -> None:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        assert self.server.stdin is not None
        self.server.stdin.write(json.dumps(message) + "\n")
        self.server.stdin.flush()

    def call_tool(self, name: str, arguments: dict) -> dict:
        return self.request("tools/call", {"name": name, "arguments": arguments})

    def test_structured_agent_workflow_over_mcp(self) -> None:
        listed = self.request("tools/list")
        tools = {tool["name"]: tool for tool in listed["tools"]}
        for name in ("analyze_change", "explain_regression", "apply_fix", "verify_fix", "undo_fix"):
            self.assertIn(name, tools)
            self.assertEqual(tools[name]["outputSchema"]["type"], "object")

        status = self.call_tool("impactproof_status", {})
        self.assertEqual(status["structuredContent"]["status"], "ready")
        self.assertEqual(json.loads(status["content"][0]["text"]), status["structuredContent"])

        with tempfile.TemporaryDirectory(prefix="impactproof_agent_mcp_") as tmp:
            repo = regression_fixtures._make_regression_repo(tmp)

            analysis_result = self.call_tool("analyze_change", {"repo_path": repo})
            analysis = analysis_result["structuredContent"]
            self.assertIn("discount.py", {item["file"] for item in analysis["changed_files"]})
            self.assertEqual(json.loads(analysis_result["content"][0]["text"]), analysis)

            evidence = self.call_tool(
                "explain_regression",
                {"repo_path": repo, "scenario": regression_fixtures.SCENARIO_NAME},
            )
            explanation = evidence["structuredContent"]
            self.assertEqual(explanation["proof_status"], "REGRESSION")
            self.assertEqual(explanation["expected"]["refund_amount"], 90.0)
            self.assertEqual(explanation["actual"]["refund_amount"], 100)
            self.assertEqual(explanation["regression_nodes"][0]["node_id"], "refund.py::refund")
            self.assertEqual(json.loads(evidence["content"][0]["text"]), explanation)

            applied = self.call_tool(
                "apply_fix",
                {
                    "repo_path": repo,
                    "file_rel": "refund.py",
                    "new_source": (
                        "def refund(transaction: dict) -> float:\n"
                        "    return transaction[\"final_amount\"]\n"
                    ),
                },
            )
            self.assertEqual(applied["structuredContent"]["status"], "applied")

            verified = self.call_tool(
                "verify_fix",
                {"repo_path": repo, "scenario": regression_fixtures.SCENARIO_NAME},
            )
            self.assertEqual(verified["structuredContent"]["status"], "PASS")
            self.assertEqual(verified["structuredContent"]["actual"]["refund_amount"], 90.0)

            undone = self.call_tool("undo_fix", {"repo_path": repo})
            self.assertEqual(undone["structuredContent"]["status"], "undone")
            self.assertEqual(json.loads(evidence["content"][0]["text"]), explanation)
            after_undo = self.call_tool(
                "verify_fix",
                {"repo_path": repo, "scenario": regression_fixtures.SCENARIO_NAME},
            )
            self.assertEqual(after_undo["structuredContent"]["status"], "REGRESSION")

    def test_documented_workflow_and_bob_entrypoint(self) -> None:
        contract = (ROOT / "docs" / "impactproof-agent-workflow.md").read_text(encoding="utf-8")
        for phrase in (
            "explain_regression",
            "apply_fix",
            "Immediately after",
            "verify_fix",
            "do not claim success",
            "guessed dependencies",
            "not an IBM Bob Custom Mode definition",
        ):
            self.assertIn(phrase.lower(), contract.lower())

        bob_config = json.loads((ROOT / ".bob" / "mcp.json").read_text(encoding="utf-8"))
        impactproof_server = bob_config["mcpServers"]["impactproof"]
        self.assertEqual(impactproof_server["command"], "node")
        self.assertEqual(Path(impactproof_server["args"][0]), BUILD_ENTRY)


if __name__ == "__main__":
    unittest.main()

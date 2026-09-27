"""Unit tests for the Bob Shell wrapper; all process execution is mocked."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import bob_runner


class BobRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name)
        self.runtime_dir = self.workspace / "runtime"
        self.runtime_dir.mkdir()
        self.bob_executable = self.runtime_dir / "bob"
        self.node_executable = self.runtime_dir / "node"
        self.bob_executable.write_text("mock executable", encoding="utf-8")
        self.node_executable.write_text("mock node", encoding="utf-8")
        self.bob_executable.chmod(0o755)
        self.node_executable.chmod(0o755)
        self.allowed_workspaces = patch.object(
            bob_runner, "ALLOWED_WORKSPACES", (self.workspace,)
        )
        self.bob_path = patch.object(bob_runner, "BOB_EXECUTABLE", self.bob_executable)
        self.runtime_path = patch.object(bob_runner, "BOB_RUNTIME_DIR", self.runtime_dir)
        self.allowed_workspaces.start()
        self.bob_path.start()
        self.runtime_path.start()
        self.addCleanup(self.allowed_workspaces.stop)
        self.addCleanup(self.bob_path.stop)
        self.addCleanup(self.runtime_path.stop)
        self.addCleanup(self.temp.cleanup)

    def run_task(self, **kwargs):
        args = {
            "workspace": self.workspace,
            "prompt": "Inspect this change read-only.",
        }
        args.update(kwargs)
        return bob_runner.run_bob_task(**args)

    def test_command_and_environment_are_fixed_and_shell_is_disabled(self):
        completed = subprocess.CompletedProcess([], 0, '{"message":"done"}', "")
        with patch.object(bob_runner.subprocess, "run", return_value=completed) as run:
            result = self.run_task()

        call = run.call_args
        command = call.args[0]
        self.assertEqual(command[:2], [str(self.bob_executable), "run"])
        self.assertEqual(command[2:4], ["--workspace", str(self.workspace)])
        self.assertEqual(command[4:6], ["--mode", "impactproof-agent"])
        self.assertEqual(command[6:8], ["--format", "json"])
        self.assertIn(["--max-cost", "1.0"], [command[i:i + 2] for i in range(len(command) - 1)])
        self.assertIn(["--max-turns", "3"], [command[i:i + 2] for i in range(len(command) - 1)])
        self.assertEqual(command[-2:], ["--", "Inspect this change read-only."])
        self.assertFalse(call.kwargs["shell"])
        self.assertEqual(call.kwargs["cwd"], str(self.workspace))
        self.assertTrue(call.kwargs["env"]["PATH"].startswith(str(self.runtime_dir)))
        self.assertEqual(result.status, "success")
        self.assertEqual(result.last_message, "done")

    def test_analysis_mode_uses_temporary_workspace_with_only_readonly_server(self):
        readonly_entrypoint = self.workspace / "readonly_index.js"
        readonly_entrypoint.write_text("server", encoding="utf-8")
        captured = {}

        def fake_run(command, **kwargs):
            captured["workspace"] = Path(kwargs["cwd"])
            captured["command"] = command
            config = json.loads((captured["workspace"] / ".bob" / "mcp.json").read_text())
            captured["config"] = config
            mode_text = (captured["workspace"] / ".bob" / "custom_modes.yaml").read_text()
            captured["mode_text"] = mode_text
            return subprocess.CompletedProcess(command, 0, '{"message":"done"}', "")

        with patch.object(bob_runner, "GLOBAL_MCP_CONFIG_PATHS", ()), patch.object(
            bob_runner, "NODE_BIN", self.runtime_dir
        ), patch.object(bob_runner, "READONLY_MCP_ENTRYPOINT", readonly_entrypoint), patch.object(
            bob_runner.subprocess, "run", side_effect=fake_run
        ):
            result = self.run_task(mode=bob_runner.ANALYSIS_MODE)

        self.assertEqual(result.status, "success")
        self.assertNotEqual(captured["workspace"], self.workspace)
        self.assertEqual(
            set(captured["config"]["mcpServers"]), {"impactproof-analysis"}
        )
        server = captured["config"]["mcpServers"]["impactproof-analysis"]
        self.assertEqual(server["command"], str(self.runtime_dir / "node"))
        self.assertEqual(server["args"], [str(readonly_entrypoint)])
        self.assertEqual(captured["command"][captured["command"].index("--mode") + 1],
                         "impactproof-analysis")
        self.assertIn("slug: impactproof-analysis", captured["mode_text"])
        self.assertIn("      - read\n      - mcp", captured["mode_text"])
        self.assertNotIn("execute", captured["mode_text"])
        self.assertFalse(captured["workspace"].exists())

    def test_analysis_mode_fails_closed_if_global_mcp_servers_exist(self):
        global_config = self.workspace / "global-mcp.json"
        global_config.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}}))
        readonly_entrypoint = self.workspace / "readonly_index.js"
        readonly_entrypoint.write_text("server", encoding="utf-8")
        with patch.object(bob_runner, "GLOBAL_MCP_CONFIG_PATHS", (global_config,)), patch.object(
            bob_runner, "READONLY_MCP_ENTRYPOINT", readonly_entrypoint
        ), patch.object(bob_runner.subprocess, "run") as run:
            result = self.run_task(mode=bob_runner.ANALYSIS_MODE)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("Global Bob MCP servers", result.error)
        run.assert_not_called()

    def test_analysis_mode_fails_closed_on_unreadable_global_config(self):
        global_config = self.workspace / "global-mcp.json"
        global_config.write_text("not json", encoding="utf-8")
        with patch.object(bob_runner, "GLOBAL_MCP_CONFIG_PATHS", (global_config,)), patch.object(
            bob_runner.subprocess, "run"
        ) as run:
            result = self.run_task(mode=bob_runner.ANALYSIS_MODE)
        self.assertEqual(result.status, "unavailable")
        self.assertIn("cannot be verified", result.error)
        run.assert_not_called()

    def test_timeout_returns_partial_output_without_success(self):
        expired = subprocess.TimeoutExpired("bob", 1, output='{"message":"partial"}', stderr="waiting")
        with patch.object(bob_runner.subprocess, "run", side_effect=expired):
            result = self.run_task(timeout_seconds=1)
        self.assertEqual(result.status, "timeout")
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.exit_code)
        self.assertIn("partial", result.stdout)

    def test_nonzero_exit_is_failure_even_with_valid_json(self):
        completed = subprocess.CompletedProcess([], 4, '{"message":"partial"}', "failed")
        with patch.object(bob_runner.subprocess, "run", return_value=completed):
            result = self.run_task()
        self.assertEqual(result.status, "error")
        self.assertEqual(result.exit_code, 4)
        self.assertEqual(result.diagnostic.category, "process_exit")
        self.assertEqual(result.diagnostic.exit_code, 4)
        self.assertTrue(result.diagnostic.stdout_produced)
        self.assertTrue(result.diagnostic.stderr_produced)

    def test_startup_failure_is_classified_without_claiming_authentication(self):
        completed = subprocess.CompletedProcess(
            [], 1, "", "Fatal error: EROFS read-only file system while loading settings"
        )
        with patch.object(bob_runner.subprocess, "run", return_value=completed):
            result = self.run_task()
        self.assertEqual(result.diagnostic.category, "authentication_or_startup")
        self.assertEqual(result.diagnostic.exit_code, 1)
        self.assertIn("EROF", result.diagnostic.startup_error)

    def test_process_start_and_preflight_categories(self):
        with patch.object(bob_runner, "BOB_EXECUTABLE", self.runtime_dir / "missing"):
            missing_bob = self.run_task()
        self.assertEqual(missing_bob.diagnostic.category, "executable_preflight")

        with patch.object(bob_runner, "BOB_RUNTIME_DIR", self.workspace / "missing"):
            missing_node = self.run_task()
        self.assertEqual(missing_node.diagnostic.category, "node_preflight")

        with patch.object(bob_runner.subprocess, "run", side_effect=OSError("spawn failed")):
            start_error = self.run_task()
        self.assertEqual(start_error.diagnostic.category, "process_start")
        self.assertEqual(start_error.diagnostic.startup_error, "spawn failed")

    def test_diagnostic_excerpt_is_redacted_and_strictly_bounded(self):
        secret = "secret-value-987"
        stderr = (
            f"BOB_API_KEY={secret}\nAuthorization: Bearer {secret}\n"
            f"https://user:{secret}@example.invalid/path\n" + "x" * 5_000
        )
        completed = subprocess.CompletedProcess([], 1, "output " + "y" * 5_000, stderr)
        with patch.dict("os.environ", {"BOB_API_KEY": secret}), patch.object(
            bob_runner.subprocess, "run", return_value=completed
        ):
            result = self.run_task()
        diagnostic_text = result.diagnostic.startup_error
        self.assertLessEqual(len(diagnostic_text), bob_runner.MAX_DIAGNOSTIC_TEXT_CHARS)
        self.assertNotIn(secret, diagnostic_text)
        self.assertIn("[REDACTED]", diagnostic_text)
        self.assertLessEqual(len(result.stdout), bob_runner.MAX_DIAGNOSTIC_TEXT_CHARS)
        self.assertLessEqual(len(result.stderr), bob_runner.MAX_DIAGNOSTIC_TEXT_CHARS)

    def test_secret_patterns_are_redacted_without_environment_values(self):
        text = (
            "export SERVICE_SECRET=non-env-secret\n"
            "Authorization: Basic non-env-authorization\n"
            "https://user:non-env-password@example.invalid/path"
        )
        sanitized = bob_runner._redact_text(text, [])
        for secret in ("non-env-secret", "non-env-authorization", "non-env-password"):
            self.assertNotIn(secret, sanitized)
        self.assertIn("SERVICE_SECRET=\"[REDACTED]\"", sanitized)
        self.assertIn("Authorization: [REDACTED]", sanitized)

    def test_timeout_diagnostic_records_output_presence(self):
        expired = subprocess.TimeoutExpired("bob", 1, output="partial", stderr="still starting")
        with patch.object(bob_runner.subprocess, "run", side_effect=expired):
            result = self.run_task(timeout_seconds=1)
        self.assertEqual(result.diagnostic.category, "timeout")
        self.assertIsNone(result.diagnostic.exit_code)
        self.assertTrue(result.diagnostic.stdout_produced)
        self.assertTrue(result.diagnostic.stderr_produced)

    def test_malformed_json_is_explicit_error(self):
        completed = subprocess.CompletedProcess([], 0, "not json", "")
        with patch.object(bob_runner.subprocess, "run", return_value=completed):
            result = self.run_task()
        self.assertEqual(result.status, "invalid_json")
        self.assertIsNone(result.parsed_json)
        self.assertEqual(result.diagnostic.category, "output_parse")

    def test_valid_json_is_returned(self):
        completed = subprocess.CompletedProcess([], 0, '{"message":"analysis","items":[1]}', "")
        with patch.object(bob_runner.subprocess, "run", return_value=completed):
            result = self.run_task()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.parsed_json, {"message": "analysis", "items": [1]})

    def test_json_envelope_and_assistant_message_are_distinct(self):
        assistant_message = "```json\n{\"answer\":\"evidence\"}\n```"
        envelope = {
            "type": "result",
            "status": "success",
            "stats": {"tool_calls": 2},
            "last_message": assistant_message,
        }
        with patch.object(
            bob_runner.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, json.dumps(envelope), ""),
        ):
            result = self.run_task()
        self.assertEqual(result.status, "success")
        self.assertEqual(result.parsed_json["type"], "result")
        self.assertEqual(result.last_message, assistant_message)

    def test_stream_json_preserves_named_tool_events(self):
        events = [
            {"type": "tool_use", "tool_name": "mcp__impactproof__apply_fix", "tool_id": "t1", "parameters": {"file_rel": "refund.py"}},
            {"type": "tool_result", "tool_id": "t1", "status": "success", "output": {"status": "applied"}},
            {"type": "result", "status": "success", "stats": {"tool_calls": 1}, "last_message": "Applied."},
        ]
        with patch.object(
            bob_runner.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), ""),
        ) as run:
            result = self.run_task(output_format="stream-json")
        self.assertEqual(result.status, "success")
        self.assertEqual(result.last_message, "Applied.")
        self.assertEqual([event.get("tool_name") for event in result.tool_events if event["type"] == "tool_use"], ["mcp__impactproof__apply_fix"])
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--format") + 1], "stream-json")

    def test_malformed_stream_json_is_rejected(self):
        with patch.object(
            bob_runner.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, '{"type":"result"}\nnot-json', ""),
        ):
            result = self.run_task(output_format="stream-json")
        self.assertEqual(result.status, "invalid_json")
        self.assertIn("stream-json", result.error)

    def test_unknown_output_format_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "output_format"):
            self.run_task(output_format="yaml")

    def test_secrets_are_redacted_from_every_returned_text_field(self):
        secret = "test-bob-secret-value"
        completed = subprocess.CompletedProcess(
            [], 0,
            '{"message":"credential ' + secret + '","apiKey":"' + secret + '"}',
            "Bearer " + secret,
        )
        with patch.dict("os.environ", {"BOB_API_KEY": secret}), patch.object(
            bob_runner.subprocess, "run", return_value=completed
        ):
            result = self.run_task()
        fields = [result.last_message, result.stdout, result.stderr, str(result.parsed_json), str(result.error)]
        self.assertTrue(all(secret not in field for field in fields))
        self.assertIn("[REDACTED]", result.stdout)

    def test_rejects_workspace_outside_allowlist(self):
        outside = self.workspace.parent
        with self.assertRaisesRegex(ValueError, "outside the allowed"):
            self.run_task(workspace=outside)

    def test_rejects_unknown_mode_and_invalid_prompt(self):
        with self.assertRaisesRegex(ValueError, "mode"):
            self.run_task(mode="agent")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.run_task(prompt="  ")
        with self.assertRaisesRegex(ValueError, "at most"):
            self.run_task(prompt="x" * (bob_runner.MAX_PROMPT_CHARS + 1))

    def test_rejects_unbounded_cost_turns_and_timeout(self):
        for kwargs in (
            {"max_cost": 1.01}, {"max_cost": 0}, {"max_cost": float("nan")},
            {"max_turns": 0}, {"max_turns": bob_runner.MAX_TURNS + 1},
            {"timeout_seconds": 0},
            {"timeout_seconds": bob_runner.MAX_TIMEOUT_SECONDS + 1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.run_task(**kwargs)

    def test_missing_runtime_does_not_invoke_process(self):
        with patch.object(bob_runner, "BOB_RUNTIME_DIR", self.workspace / "missing"), patch.object(
            bob_runner.subprocess, "run"
        ) as run:
            result = self.run_task()
        self.assertEqual(result.status, "unavailable")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import os
import subprocess
from importlib.machinery import SourceFileLoader
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).parents[1] / "scripts" / "impactproof-dashboard"
SPEC = importlib.util.spec_from_loader(
    "impactproof_dashboard_launcher", SourceFileLoader("impactproof_dashboard_launcher", str(SCRIPT))
)
launcher = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(launcher)


class LocalDashboardLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.secret = self.root / "bob.env"
        self.repo = self.root / "demo repo"
        self.repo.mkdir()

    def write_secret(self, content: str, mode: int = 0o600):
        self.secret.write_text(content, encoding="utf-8")
        self.secret.chmod(mode)

    def test_missing_secret_file_fails_safely(self):
        with self.assertRaisesRegex(launcher.LauncherError, "unavailable") as caught:
            launcher.read_api_key(self.secret)
        self.assertNotIn("TEST_DUMMY_KEY", str(caught.exception))

    def test_malformed_secret_file_fails_safely_without_echoing_value(self):
        self.write_secret("not a valid assignment TEST_DUMMY_KEY\n")
        with self.assertRaises(launcher.LauncherError) as caught:
            launcher.read_api_key(self.secret)
        self.assertNotIn("TEST_DUMMY_KEY", str(caught.exception))

    def test_missing_or_empty_key_fails_safely(self):
        for content in ("OTHER=value\n", "BOB_API_KEY=\n"):
            self.write_secret(content)
            with self.subTest(content=content), self.assertRaises(launcher.LauncherError) as caught:
                launcher.read_api_key(self.secret)
            self.assertNotIn("TEST_DUMMY_KEY", str(caught.exception))

    def test_rejects_permissive_secret_permissions(self):
        self.write_secret("BOB_API_KEY=TEST_DUMMY_KEY\n", 0o644)
        with self.assertRaisesRegex(launcher.LauncherError, "user-only"):
            launcher.read_api_key(self.secret)

    def test_valid_dummy_key_is_forwarded_and_arguments_are_preserved(self):
        self.write_secret("# local credentials\nBOB_API_KEY=TEST_DUMMY_KEY\n", 0o600)
        with patch.object(launcher, "SECRET_FILE", self.secret), \
                patch.object(launcher, "LAUNCHER", self.root / "launch_graph.py"), \
                patch("tempfile.mkstemp", return_value=(41, str(self.root / "out.html"))), \
                patch("os.close") as close, patch("os.execve") as execve, \
                patch.dict(os.environ, {"EXISTING_TEST_VAR": "preserved"}, clear=True):
            (self.root / "launch_graph.py").touch()
            result = launcher.main([str(self.repo), "--scenario", "premium_checkout_refund"])

        self.assertEqual(result, 0)
        close.assert_any_call(41)
        executable, argv, env = execve.call_args.args
        self.assertEqual(executable, launcher.sys.executable)
        self.assertEqual(argv[2:5], [str(self.repo.resolve()), "--scenario", "premium_checkout_refund"])
        self.assertEqual(argv[-2:], ["--out", str(self.root / "out.html")])
        self.assertEqual(env["BOB_API_KEY"], "TEST_DUMMY_KEY")
        self.assertEqual(env["EXISTING_TEST_VAR"], "preserved")

    def test_key_is_not_written_to_output_or_exceptions(self):
        self.write_secret("BOB_API_KEY=TEST_DUMMY_KEY\n")
        with patch.object(launcher, "SECRET_FILE", self.secret), \
                patch.object(launcher, "LAUNCHER", self.root / "missing.py"), \
                patch("builtins.print") as output:
            result = launcher.main([str(self.repo), "--scenario", "premium_checkout_refund"])
        self.assertEqual(result, 2)
        rendered = " ".join(str(call) for call in output.call_args_list)
        self.assertNotIn("TEST_DUMMY_KEY", rendered)

    def test_target_and_scenario_validation(self):
        with self.assertRaises(launcher.LauncherError):
            launcher._arguments(["relative", "--scenario", "demo"])
        with self.assertRaises(launcher.LauncherError):
            launcher._arguments([str(self.repo), "--scenario", "bad;command"])
        repo, scenario = launcher._arguments([str(self.repo), "--scenario", "premium_checkout_refund"])
        self.assertEqual(repo, str(self.repo.resolve()))
        self.assertEqual(scenario, "premium_checkout_refund")

    def test_impact_commands_use_absolute_launcher_interpreter_and_targets(self):
        command_paths = (
            Path(__file__).parents[1] / ".bob" / "commands" / "impact.md",
            Path("/home/mri/Documents/impactproof_demo/.bob/commands/impact.md"),
        )
        expected_invocations = (
            'nohup /usr/bin/python3 /home/mri/.local/bin/impactproof-dashboard "<absolute-workspace-root>" --scenario "<scenario-name>"',
            'nohup /usr/bin/python3 /home/mri/.local/bin/impactproof-dashboard \\\n  "/home/mri/Documents/impactproof_demo" --scenario premium_checkout_refund',
        )
        for path, invocation in zip(command_paths, expected_invocations):
            text = path.read_text(encoding="utf-8")
            self.assertIn("MUST call Bob's Execute tool", text)
            self.assertNotIn('$HOME/.local/bin/impactproof-dashboard', text)
            code = text.split("```sh", 1)[1].split("```", 1)[0]
            self.assertIn(invocation, code)
            syntax = subprocess.run(["sh", "-n"], input=code, text=True, capture_output=True)
            self.assertEqual(syntax.returncode, 0, syntax.stderr)


if __name__ == "__main__":
    unittest.main()

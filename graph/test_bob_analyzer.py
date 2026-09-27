"""Mock-only tests for ImpactProof's Bob analysis orchestration layer."""

from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import bob_analyzer
from bob_runner import BobDiagnostic, BobRunResult


class BobAnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.allowed = patch.object(
            bob_analyzer, "PROJECT_ROOT", self.repo
        )
        self.allowed.start()
        self.addCleanup(self.allowed.stop)
        self.addCleanup(self.temp.cleanup)

    def payload(self):
        return {
            "analysis": {
                "repo_path": str(self.repo.resolve()),
                "changed_files": [{"file": "discount.py", "symbols": ["calculate_total"]}],
                "directly_affected": ["checkout.py"],
                "indirectly_affected": ["refund.py"],
            },
            "explanation": {
                "scenario": "premium_checkout_refund",
                "proof_status": "REGRESSION",
                "regression_nodes": [{"file": "refund.py", "symbol": "refund"}],
                "expected": {"refund_amount": 90},
                "actual": {"refund_amount": 100},
                "mismatches": [{"key": "refund_amount", "expected": 90, "actual": 100}],
            },
            "root_cause": "refund uses the pre-discount amount.",
            "bob_message": "The changed payment amount is not used by refund.",
        }

    def run_result(self, payload=None, *, status="success", exit_code=0, tool_calls=2, message=None):
        if message is None:
            message = json.dumps(self.payload() if payload is None else payload)
        envelope = {
            "type": "result",
            "status": "success",
            "stats": {"tool_calls": tool_calls},
            "last_message": message,
        }
        return BobRunResult(status, exit_code, message, envelope, json.dumps(envelope), "", 0.1, False)

    def test_valid_analysis_result(self):
        result = bob_analyzer._parse_bob_analysis(self.run_result(), self.repo.resolve())
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.changed_symbols, [{"file": "discount.py", "symbol": "calculate_total"}])
        self.assertEqual(result.directly_affected_files, ["checkout.py"])
        self.assertEqual(result.regression_node, {"file": "refund.py", "symbol": "refund"})
        self.assertEqual(result.proof_status, "REGRESSION")
        self.assertEqual(result.expected_values, {"refund_amount": 90})
        self.assertEqual(result.actual_values, {"refund_amount": 100})

    def test_valid_json_code_fence_is_accepted(self):
        message = "```json\n" + json.dumps(self.payload()) + "\n```"
        result = bob_analyzer._parse_bob_analysis(
            self.run_result(message=message), self.repo.resolve()
        )
        self.assertEqual(result.status, "complete")
        self.assertEqual(result.proof_status, "REGRESSION")

    def test_short_explanation_around_json_fence_is_accepted(self):
        message = "Here is the evidence-backed result:\n```json\n"
        message += json.dumps(self.payload()) + "\n```\nThe proof result is from ImpactProof."
        result = bob_analyzer._parse_bob_analysis(
            self.run_result(message=message), self.repo.resolve()
        )
        self.assertEqual(result.status, "complete")

    def test_short_explanation_around_single_json_object_is_accepted(self):
        message = "Requested evidence: " + json.dumps(self.payload()) + " Analysis complete."
        result = bob_analyzer._parse_bob_analysis(
            self.run_result(message=message), self.repo.resolve()
        )
        self.assertEqual(result.status, "complete")

    def test_unrelated_or_multiple_json_objects_are_not_extracted(self):
        message = '{"ok":true} unrelated text ' + json.dumps(self.payload())
        result = bob_analyzer._parse_bob_analysis(
            self.run_result(message=message), self.repo.resolve()
        )
        self.assertEqual(result.status, "error")
        self.assertIn("required JSON", result.error)

    def test_invalid_evidence_inside_json_fence_remains_rejected(self):
        payload = self.payload()
        payload["explanation"]["actual"] = None
        message = "```json\n" + json.dumps(payload) + "\n```"
        result = bob_analyzer._parse_bob_analysis(
            self.run_result(message=message), self.repo.resolve()
        )
        self.assertEqual(result.status, "error")
        self.assertIn("actual proof values", result.error)

    def test_bobrunner_failure_is_not_reported_as_analysis(self):
        result = bob_analyzer._parse_bob_analysis(
            BobRunResult(
                "error", 1, "", None, "", "", 0.2, False, "Bob failed",
                diagnostic=BobDiagnostic("process_exit", 1, False, True, "failed"),
            ), self.repo.resolve()
        )
        self.assertEqual(result.status, "error")
        self.assertEqual(result.error, "Bob failed")
        self.assertIsNone(result.proof_status)
        self.assertEqual(result.diagnostic.category, "process_exit")
        self.assertEqual(result.diagnostic.exit_code, 1)
        self.assertEqual(result.diagnostic.startup_error, "failed")

    def test_bobrunner_timeout_is_not_reported_as_analysis(self):
        result = bob_analyzer._parse_bob_analysis(
            BobRunResult("timeout", None, "", None, "", "", 120.0, True,
                         "Bob Shell exceeded the configured timeout."),
            self.repo.resolve(),
        )
        self.assertEqual(result.status, "error")
        self.assertIn("timeout", result.error)
        self.assertIsNone(result.proof_status)
        self.assertEqual(result.diagnostic.category, "timeout")

    def test_malformed_bob_json_is_rejected(self):
        result = bob_analyzer._parse_bob_analysis(self.run_result(message="not json"), self.repo.resolve())
        self.assertEqual(result.status, "error")
        self.assertIn("required JSON", result.error)
        self.assertEqual(result.diagnostic.category, "output_parse")

    def test_missing_required_evidence_is_rejected(self):
        payload = self.payload()
        del payload["explanation"]["mismatches"]
        result = bob_analyzer._parse_bob_analysis(self.run_result(payload), self.repo.resolve())
        self.assertEqual(result.status, "error")
        self.assertIn("mismatch evidence", result.error)
        self.assertEqual(result.diagnostic.category, "evidence_validation")

    def test_empty_output_is_rejected(self):
        result = bob_analyzer._parse_bob_analysis(self.run_result(message=""), self.repo.resolve())
        self.assertEqual(result.status, "error")
        self.assertIn("empty final", result.error)
        self.assertEqual(result.diagnostic.category, "output_parse")

    def test_zero_tool_calls_cannot_produce_an_analysis(self):
        result = bob_analyzer._parse_bob_analysis(self.run_result(tool_calls=0), self.repo.resolve())
        self.assertEqual(result.status, "error")
        self.assertIn("tool activity", result.error)

    def test_fixed_prompt_names_scenario_and_never_requests_mutation(self):
        prompt = bob_analyzer._build_prompt(self.repo)
        self.assertIn("analyze_change", prompt)
        self.assertIn("explain_regression", prompt)
        self.assertIn(bob_analyzer.SCENARIO, prompt)
        self.assertIn("analysis only", prompt)
        self.assertIn("Do not call apply_fix", prompt)
        self.assertIn("Do not call apply_fix, verify_fix, or undo_fix", prompt)
        self.assertIn("Return evidence only", prompt)
        self.assertNotIn("Please apply_fix", prompt)
        self.assertNotIn("Please verify_fix", prompt)
        self.assertNotIn("Please undo_fix", prompt)

    def test_scenario_is_fixed(self):
        self.assertEqual(bob_analyzer.SCENARIO, "premium_checkout_refund")
        self.assertNotIn("scenario", inspect.signature(bob_analyzer.analyze_with_bob).parameters)

    def test_repo_path_validation(self):
        with self.assertRaisesRegex(ValueError, "outside the allowed"):
            bob_analyzer._validate_repo_path(Path(self.temp.name).parent)
        with self.assertRaisesRegex(ValueError, "exist"):
            bob_analyzer._validate_repo_path(self.repo / "missing")

    def test_valid_full_mcp_analysis_is_accepted_and_uses_existing_runner(self):
        with patch.object(bob_analyzer, "run_bob_task", return_value=self.run_result()) as run:
            result = bob_analyzer.analyze_with_bob(self.repo)
        self.assertEqual(result.status, "complete")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["workspace"], self.repo)
        self.assertEqual(kwargs["mode"], bob_analyzer.BOB_MODE)
        self.assertEqual(kwargs["mode"], "impactproof-analysis")
        self.assertEqual(kwargs["max_cost"], 1)
        self.assertEqual(kwargs["max_turns"], 3)
        self.assertEqual(kwargs["timeout_seconds"], 120)
        self.assertIn("analyze_change", kwargs["prompt"])
        self.assertIn("explain_regression", kwargs["prompt"])

    def test_inconsistent_proof_is_rejected(self):
        payload = self.payload()
        payload["explanation"]["proof_status"] = "PASS"
        result = bob_analyzer._parse_bob_analysis(self.run_result(payload), self.repo.resolve())
        self.assertEqual(result.status, "error")
        self.assertIn("conflicts", result.error)

    def test_analyzer_has_no_direct_subprocess_execution(self):
        source = inspect.getsource(bob_analyzer)
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("subprocess.run", source)

    def test_unexpected_runner_exception_is_classified_without_exposing_details(self):
        with patch.object(bob_analyzer, "run_bob_task", side_effect=RuntimeError("secret prompt contents")):
            result = bob_analyzer.analyze_with_bob(self.repo)
        self.assertEqual(result.status, "error")
        self.assertEqual(result.diagnostic.category, "unexpected_exception")
        self.assertNotIn("secret prompt contents", result.error)


if __name__ == "__main__":
    unittest.main()

"""Focused tests for the dashboard's local, read-only Bob analysis bridge."""

from __future__ import annotations

import json
import io
import os
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import launch_graph
from bob_analyzer import BobAnalysisResult
from bob_bridge import BobAnalysisBridge, BobFixBridge, BridgeResponse
from bob_runner import BobDiagnostic, BobRunResult


def complete_result(**overrides):
    values = {
        "status": "complete",
        "changed_files": [{"file": "discount.py", "symbols": ["calculate_total"]}],
        "changed_symbols": [{"file": "discount.py", "symbol": "calculate_total"}],
        "directly_affected_files": ["checkout.py"],
        "indirectly_affected_files": ["refund.py"],
        "regression_node": {"file": "refund.py", "symbol": "refund"},
        "regression_nodes": [{"file": "refund.py", "symbol": "refund"}],
        "proof_status": "REGRESSION",
        "expected_values": {"refund_amount": 90},
        "actual_values": {"refund_amount": 100},
        "mismatches": [{"key": "refund_amount", "expected": 90, "actual": 100}],
        "root_cause": "refund uses original_amount instead of final_amount",
        "bob_message": "The refund does not use the final paid amount.",
    }
    values.update(overrides)
    return BobAnalysisResult(**values)


class BobBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / "refund.py").write_text('def refund(tx):\n    return tx["original_amount"]\n', encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def call_bridge(self, analyzer, request=None, regression=True):
        bridge = BobAnalysisBridge((self.repo,), analyzer=analyzer)
        return bridge.handle(request or {"repo_path": str(self.repo)}, self.repo, regression)

    def test_success_returns_allowlisted_validated_analysis(self):
        called = []
        result = self.call_bridge(lambda repo: called.append(repo) or complete_result())
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.payload["status"], "success")
        self.assertEqual(result.payload["analysis"]["root_cause"], complete_result().root_cause)
        self.assertEqual(called, [self.repo.resolve()])
        self.assertNotIn("stdout", result.payload)
        self.assertNotIn("bob_tool_call_count", result.payload["analysis"])
        self.assertNotIn("proposed_fix", result.payload["analysis"])

    def test_blocked_timeout_failure_and_malformed_result_fail_closed(self):
        blocked = self.call_bridge(lambda repo: BobAnalysisResult(status="blocked", error="private detail"))
        failed = self.call_bridge(lambda repo: (_ for _ in ()).throw(TimeoutError("secret traceback")))
        malformed = self.call_bridge(lambda repo: {"root_cause": "unvalidated"})
        for response in (blocked, failed, malformed):
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.payload, {"status": "unavailable", "message": "Bob analysis unavailable"})
            self.assertNotIn("root_cause", json.dumps(response.payload))
            self.assertNotIn("proposed_fix", json.dumps(response.payload))

    def test_diagnostics_are_logged_redacted_and_browser_response_stays_generic(self):
        secret = "bridge-secret-456"
        diagnostic = BobDiagnostic(
            "authentication_or_startup", 1, False, True,
            f"BOB_API_KEY={secret}\nFatal error: EROFS " + "x" * 5_000,
        )
        result = BobAnalysisResult(status="error", error="private details", diagnostic=diagnostic)
        log = io.StringIO()
        with patch.dict(os.environ, {"BOB_API_KEY": secret}), patch(
            "bob_bridge.sys.stderr", log
        ):
            response = self.call_bridge(lambda _: result)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.payload,
            {"status": "unavailable", "message": "Bob analysis unavailable"},
        )
        record = log.getvalue()
        self.assertIn("[ImpactProof][BobDiagnostics]", record)
        self.assertIn('"category": "authentication_or_startup"', record)
        self.assertIn('"exit_code": 1', record)
        self.assertIn('"stderr_produced": true', record)
        self.assertIn("EROF", record)
        self.assertNotIn(secret, record)
        log_record = json.loads(record.split(" ", 1)[1])
        self.assertLessEqual(len(log_record["stderr"]), 1200)
        self.assertNotIn("stdout", log_record)

    def test_evidence_validation_failure_has_distinct_logged_category(self):
        log = io.StringIO()
        invalid = complete_result(mismatches=[])
        with patch("bob_bridge.sys.stderr", log):
            response = self.call_bridge(lambda _: invalid)
        self.assertEqual(response.payload, {
            "status": "unavailable", "message": "Bob analysis unavailable",
        })
        self.assertIn('"category": "evidence_validation"', log.getvalue())

    def test_missing_root_cause_or_mismatch_fails_closed(self):
        no_cause = self.call_bridge(lambda repo: complete_result(root_cause=" "))
        no_mismatch = self.call_bridge(lambda repo: complete_result(mismatches=[]))
        for response in (no_cause, no_mismatch):
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("root_cause", response.payload)
            self.assertNotIn("proposed_fix", response.payload)

    def test_secret_is_never_returned(self):
        secret = "test-secret-value-123"
        with patch.dict(os.environ, {"BOB_API_KEY": secret}):
            response = self.call_bridge(lambda repo: complete_result(
                root_cause=f"Evidence note: {secret}",
            ))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(secret, json.dumps(response.payload))
        self.assertIn("[REDACTED]", response.payload["analysis"]["root_cause"])

    def test_invalid_path_and_non_regression_do_not_invoke_analyzer(self):
        called = []
        analyzer = lambda repo: called.append(repo) or complete_result()
        invalid = self.call_bridge(analyzer, {"repo_path": "/etc"})
        no_regression = self.call_bridge(analyzer, regression=False)
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(no_regression.status_code, 409)
        self.assertEqual(called, [])

    def test_only_one_analysis_runs_at_a_time(self):
        started = threading.Event()
        release = threading.Event()
        bridge = BobAnalysisBridge((self.repo,), analyzer=lambda repo: (started.set(), release.wait(2), complete_result())[-1])
        first = []
        worker = threading.Thread(target=lambda: first.append(
            bridge.handle({"repo_path": str(self.repo)}, self.repo, True)
        ))
        worker.start()
        self.assertTrue(started.wait(2))
        second = bridge.handle({"repo_path": str(self.repo)}, self.repo, True)
        release.set()
        worker.join(timeout=3)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(first[0].status_code, 200)


class BobFixBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve()
        (self.repo / "refund.py").write_text(
            'def refund(tx):\n    return tx["original_amount"]\n', encoding="utf-8"
        )
        self.analysis_bridge = BobAnalysisBridge((self.repo,), analyzer=lambda _: complete_result())
        self.analysis_bridge.handle({"repo_path": str(self.repo)}, self.repo, True)

    def tearDown(self):
        self.temp.cleanup()

    def result(self, *, tool_name="mcp__impactproof__apply_fix", file_rel="refund.py", tool_status="success"):
        source = 'def refund(tx):\n    return tx["final_amount"]\n'
        tool_id = "fix-1"
        events = (
            {"type": "tool_use", "tool_name": tool_name, "tool_id": tool_id,
             "parameters": {"repo_path": str(self.repo), "file_rel": file_rel, "new_source": source}},
            {"type": "tool_result", "tool_id": tool_id, "status": tool_status,
             "output": {"structuredContent": {"status": "applied", "file": file_rel, "undo_id": "undo-123"}}},
        )
        envelope = {"type": "result", "status": "success", "stats": {"tool_calls": 1}, "last_message": "Applied."}
        return BobRunResult("success", 0, "Applied.", envelope, "", "", 0.1, False, tool_events=events)

    def bridge(self, runner):
        return BobFixBridge((self.repo,), self.analysis_bridge, runner=runner)

    def request(self):
        return {"repo_path": str(self.repo)}

    def test_success_requires_the_exact_apply_fix_tool_result(self):
        calls = []
        bridge = self.bridge(lambda **kwargs: calls.append(kwargs) or self.result())
        response = bridge.handle(self.request(), self.repo, True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.payload["fix"]["status"], "applied")
        self.assertEqual(response.payload["fix"]["file"], "refund.py")
        self.assertEqual(response.payload["fix"]["undo_id"], "undo-123")
        self.assertTrue(bridge.has_applied(self.repo))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["mode"], "impactproof-fix")
        self.assertEqual(calls[0]["output_format"], "stream-json")
        self.assertIn("Only this file may be changed: refund.py", calls[0]["prompt"])
        normalized_prompt = " ".join(calls[0]["prompt"].split())
        self.assertIn("Verification is a separate explicit dashboard action", normalized_prompt)

    def test_request_validation_and_no_regression_rejection(self):
        called = []
        bridge = self.bridge(lambda **kwargs: called.append(kwargs) or self.result())
        self.assertEqual(bridge.handle({"repo_path": str(self.repo), "extra": 1}, self.repo, True).status_code, 400)
        self.assertEqual(bridge.handle(self.request(), self.repo, False).status_code, 409)
        self.assertEqual(bridge.handle({"repo_path": "/etc"}, self.repo, True).status_code, 400)
        self.assertEqual(called, [])

    def test_fix_is_hidden_from_runner_without_a_validated_analysis(self):
        empty = BobAnalysisBridge((self.repo,), analyzer=lambda _: complete_result())
        runner = unittest.mock.Mock()
        bridge = BobFixBridge((self.repo,), empty, runner=runner)
        self.assertEqual(bridge.handle(self.request(), self.repo, True).status_code, 409)
        runner.assert_not_called()

    def test_unexpected_mutation_or_wrong_target_fails_closed(self):
        unrelated = self.repo / "checkout.py"
        unrelated.write_text("developer-owned content\n", encoding="utf-8")
        unexpected = self.bridge(lambda **kwargs: self.result(tool_name="mcp__impactproof__verify_fix"))
        wrong_target = self.bridge(lambda **kwargs: self.result(file_rel="checkout.py"))
        for bridge in (unexpected, wrong_target):
            response = bridge.handle(self.request(), self.repo, True)
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("fix", response.payload)
        self.assertEqual(unrelated.read_text(encoding="utf-8"), "developer-owned content\n")

    def test_active_fix_request_is_locked(self):
        started = threading.Event()
        release = threading.Event()
        def runner(**kwargs):
            started.set()
            release.wait(3)
            return self.result()
        bridge = self.bridge(runner)
        first = []
        worker = threading.Thread(target=lambda: first.append(bridge.handle(self.request(), self.repo, True)))
        worker.start()
        self.assertTrue(started.wait(2))
        second = bridge.handle(self.request(), self.repo, True)
        release.set()
        worker.join(timeout=4)
        self.assertEqual(second.status_code, 409)
        self.assertEqual(first[0].status_code, 200)
        self.assertTrue(bridge.has_applied(self.repo))


class BobEndpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.viewer = self.repo / "viewer.html"
        self.viewer.write_text("viewer", encoding="utf-8")
        self.server = None
        self.thread = None
        self.patches = []

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=2)
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp.cleanup()

    def start_server(self, result=None, fix_response=None, applied=False, graph_status="REGRESSION"):
        class FakeFixBridge:
            def __init__(self):
                self.applied = applied
                self.fix_calls = []
                self.undo_calls = []

            def handle(self, request, repo, regression):
                self.fix_calls.append((request, repo, regression))
                if not regression:
                    return BridgeResponse(409, {"status": "unavailable"})
                if fix_response is not None:
                    if fix_response.status_code == 200:
                        self.applied = True
                    return fix_response
                return BridgeResponse(409, {"status": "unavailable"})

            def has_applied(self, repo):
                return self.applied

            def mark_undone(self, repo):
                self.applied = False
                self.undo_calls.append(repo)

        self.fix_bridge = FakeFixBridge()
        allowed_patch = patch.object(launch_graph, "ALLOWED_WORKSPACES", (self.repo,))
        analyzer_patch = patch.object(launch_graph, "analyze_with_bob", return_value=result or complete_result())
        fix_patch = patch.object(launch_graph, "BobFixBridge", return_value=self.fix_bridge)
        self.patches.extend([allowed_patch, analyzer_patch, fix_patch])
        allowed_patch.start()
        self.analyzer = analyzer_patch.start()
        fix_patch.start()
        self.server = launch_graph.make_viewer_server(
            self.viewer, str(self.repo), ["premium_checkout_refund"],
            {"investigation": {"status": graph_status}},
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, host=None, origin=None, path="/api/bob/analyze", body=None):
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        headers = {
            "Host": host or f"127.0.0.1:{self.server.server_port}",
            "Content-Type": "application/json",
        }
        if origin is not None:
            headers["Origin"] = origin
        conn.request("POST", path, body=json.dumps(body or {"repo_path": str(self.repo)}), headers=headers)
        response = conn.getresponse()
        payload = response.read()
        conn.close()
        return response.status, json.loads(payload)

    def test_dashboard_post_invokes_existing_analyzer_and_returns_small_result(self):
        self.start_server()
        status, payload = self.request(origin=f"http://127.0.0.1:{self.server.server_port}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "success")
        self.assertIn("root_cause", payload["analysis"])
        self.assertEqual(self.analyzer.call_count, 1)
        self.assertEqual(self.analyzer.call_args.args[0], self.repo.resolve())

    def test_wrong_host_origin_and_method_are_rejected(self):
        self.start_server()
        status, _ = self.request(host="localhost:9999")
        self.assertEqual(status, 403)
        status, _ = self.request(origin="http://evil.example")
        self.assertEqual(status, 403)
        conn = HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)
        conn.request("PUT", "/api/bob/analyze")
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 501)
        self.assertEqual(self.analyzer.call_count, 0)

    def test_endpoint_failure_does_not_return_unvalidated_analysis(self):
        self.start_server(BobAnalysisResult(status="error", error="private details"))
        status, payload = self.request()
        self.assertEqual(status, 503)
        self.assertEqual(payload, {"status": "unavailable", "message": "Bob analysis unavailable"})

    def test_fix_endpoint_validates_request_and_requires_regression(self):
        success = BridgeResponse(200, {
            "status": "success",
            "fix": {"status": "applied", "file": "refund.py", "undo_id": "u1"},
        })
        self.start_server(fix_response=success, graph_status="PASS")
        status, payload = self.request(path="/api/bob/fix")
        self.assertEqual(status, 409)
        self.assertEqual(len(self.fix_bridge.fix_calls), 1)

    def test_fix_endpoint_success_does_not_verify_automatically(self):
        success = BridgeResponse(200, {
            "status": "success",
            "fix": {"status": "applied", "file": "refund.py", "undo_id": "u1"},
        })
        self.start_server(fix_response=success)
        with patch.object(launch_graph, "run_verify_fix") as verify:
            status, payload = self.request(path="/api/bob/fix")
        self.assertEqual(status, 200)
        self.assertEqual(payload["fix"]["status"], "applied")
        self.assertEqual(len(self.fix_bridge.fix_calls), 1)
        verify.assert_not_called()

    def test_fix_endpoint_rejects_bad_origin_and_body(self):
        self.start_server(fix_response=BridgeResponse(200, {"status": "success"}))
        status, _ = self.request(path="/api/bob/fix", origin="http://evil.example")
        self.assertEqual(status, 403)
        status, _ = self.request(path="/api/bob/fix", body={"repo_path": str(self.repo), "extra": True})
        self.assertEqual(status, 400)
        self.assertEqual(self.fix_bridge.fix_calls, [])

    def test_verify_endpoint_runs_only_after_applied_fix_and_is_explicit(self):
        self.start_server(applied=True)
        with patch.object(launch_graph, "run_verify_fix", return_value={
            "status": "PASS", "expected": {"refund_amount": 90},
            "actual": {"refund_amount": 90}, "mismatches": [],
        }) as verify:
            status, payload = self.request(
                path="/api/verify-fix",
                body={"repo_path": str(self.repo), "scenario": "premium_checkout_refund"},
            )
        self.assertEqual(status, 200)
        self.assertEqual(payload["verification"]["status"], "PASS")
        verify.assert_called_once_with(str(self.repo.resolve()), "premium_checkout_refund")

    def test_verify_endpoint_rejects_unapplied_fix_and_unknown_scenario(self):
        self.start_server(applied=False)
        with patch.object(launch_graph, "run_verify_fix") as verify:
            status, _ = self.request(
                path="/api/verify-fix",
                body={"repo_path": str(self.repo), "scenario": "premium_checkout_refund"},
            )
            self.assertEqual(status, 409)
            status, _ = self.request(
                path="/api/verify-fix",
                body={"repo_path": str(self.repo), "scenario": "other"},
            )
            self.assertEqual(status, 400)
        verify.assert_not_called()


if __name__ == "__main__":
    unittest.main()

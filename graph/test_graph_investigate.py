"""Focused tests for the Phase 7A one-click investigation workflow."""

from __future__ import annotations

import json
import contextlib
import io
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).parent))
import launch_graph


class InvestigationTests(unittest.TestCase):
    def test_pipeline_runs_analyzer_graph_and_proof_in_order(self):
        calls = []
        graph_data = {"nodes": [], "edges": [], "summary": {}}
        scenario = {"scenario": "demo", "steps": []}

        with (
            patch.object(launch_graph, "run_analyzer", side_effect=lambda repo: calls.append("analyze") or {"ok": True}),
            patch.object(launch_graph, "build_graph", side_effect=lambda data: calls.append("graph") or graph_data),
            patch.object(launch_graph, "load_scenario", side_effect=lambda name: calls.append("load scenario") or scenario),
            patch.object(launch_graph, "run_scenario", side_effect=lambda repo, spec: calls.append("proof") or {"status": "REGRESSION", "scenario": "demo"}),
            patch.object(launch_graph, "merge_proof_results", side_effect=lambda *args: calls.append("merge")),
        ):
            progress = []
            result = launch_graph.run_investigation("/repo", ["demo"], lambda *event: progress.append(event))

        self.assertEqual(calls, ["analyze", "graph", "load scenario", "proof", "merge"])
        self.assertEqual(
            [(step, state) for step, state, _ in progress],
            [
                ("analyze", "running"), ("analyze", "complete"),
                ("graph", "running"), ("graph", "complete"),
                ("proof", "running"), ("proof", "complete"),
            ],
        )
        self.assertEqual(result["investigation"]["status"], "REGRESSION")

    def test_pass_result_is_reported_as_verified(self):
        graph_data = {"nodes": [], "edges": [], "summary": {}}
        with (
            patch.object(launch_graph, "run_analyzer", return_value={}),
            patch.object(launch_graph, "build_graph", return_value=graph_data),
            patch.object(launch_graph, "load_scenario", return_value={"scenario": "demo"}),
            patch.object(launch_graph, "run_scenario", return_value={"status": "PASS"}),
            patch.object(launch_graph, "merge_proof_results"),
        ):
            result = launch_graph.run_investigation("/repo", ["demo"])
        self.assertEqual(result["investigation"]["status"], "PASS")

    def test_viewer_has_action_and_local_event_stream(self):
        html = launch_graph.render_html({"nodes": [], "edges": [], "summary": {}}, True)
        self.assertIn("🔍 INVESTIGATE CHANGE", html)
        self.assertIn("Prove what your code change could break.", html)
        self.assertIn("Predict → Prove → Explain → Fix → Verify", html)
        self.assertIn('new EventSource("/api/investigate")', html)
        self.assertIn('data-stage="analyze"', html)
        self.assertIn('data-stage="graph"', html)
        self.assertIn('data-stage="proof"', html)
        self.assertIn("REGRESSION FOUND", html)
        self.assertIn("BOB — EXPLAIN &amp; FIX", html)
        self.assertIn('id="bob-analyze-button"', html)
        self.assertIn('fetch("/api/bob/analyze"', html)
        self.assertIn('id="apply-bob-fix"', html)
        self.assertIn('fetch("/api/bob/fix"', html)
        self.assertIn('id="verify-bob-fix"', html)
        self.assertIn('fetch("/api/verify-fix"', html)
        self.assertIn("Verification has not run yet.", html)
        self.assertIn("PROVEN BY EXECUTION", html)
        self.assertIn("PROOF NOT ESTABLISHED", html)
        self.assertIn("Observed behavior from deterministic execution", html)
        self.assertIn("ev.expected?.[key]", html)
        self.assertIn("ev.actual?.[key]", html)
        self.assertIn("Potential blast radius from static analysis", html)
        self.assertIn("renderViewer(graph, false)", html)
        self.assertIn("clearRenderedResults()", html)
        self.assertIn("function isProvenRegressionSelected", html)
        self.assertIn("function isBobFixVerified", html)
        self.assertIn("Bob has not been run from this dashboard.", html)
        self.assertIn("Reverts only the recorded Bob/ImpactProof change.", html)
        self.assertIn('label: "Explain"', html)
        self.assertIn('label: "Apply Fix"', html)
        self.assertIn('label: "Verify"', html)
        self.assertIn("FIX VERIFIED", html)
        self.assertIn("UNDO BOB FIX", html)
        self.assertIn("/api/undo-fix", html)
        self.assertIn("Expected", html)
        self.assertIn("Actual", html)
        self.assertIn("Mismatch", html)
        self.assertIn("symbol dependency", html)
        self.assertIn("file import", html)
        self.assertNotIn("{{GRAPH_DATA}}", html)
        static_html = launch_graph.render_html(
            {"nodes": [], "edges": [], "summary": {}},
            False,
            "Run the launcher without --no-open to use the investigation button.",
        )
        self.assertIn("disabled", static_html)
        self.assertIn("without --no-open", static_html)

    def test_initial_dashboard_is_empty_and_does_not_embed_analysis_payload(self):
        marker = "SHOULD_NOT_BE_IN_INITIAL_VIEWER"
        html = launch_graph.render_html({
            "repo_path": marker,
            "nodes": [{"id": marker, "status": "CHANGED"}],
            "edges": [{"source": marker, "target": marker}],
            "summary": {"changed_files": 1, "regressions": 1},
        }, True, defer_initial_data=True)
        self.assertIn("🔍 INVESTIGATE CHANGE", html)
        self.assertIn("No impact analysis yet.", html)
        self.assertIn('id="dashboard-idle"', html)
        self.assertIn('id="main-content" hidden', html)
        self.assertIn('id="graph-legend" hidden', html)
        self.assertIn('let GRAPH_DATA = null;', html)
        self.assertNotIn(marker, html)
        self.assertIn('class="summary-empty">Not investigated', html)

    def test_interactive_launcher_defers_analysis_until_cta(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "viewer.html"
            server = Mock()
            server.server_port = 42123
            server.serve_forever.side_effect = KeyboardInterrupt
            with (
                patch.object(launch_graph.sys, "argv", ["launch_graph.py", "/repo", "--scenario", "demo", "--out", str(output)]),
                patch.object(launch_graph, "run_analyzer") as analyzer,
                patch.object(launch_graph, "make_viewer_server", return_value=server) as make_server,
                patch.object(launch_graph.webbrowser, "open"),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                launch_graph.main()

        analyzer.assert_not_called()
        initial = make_server.call_args.args[3]
        self.assertEqual(initial["nodes"], [])
        self.assertEqual(initial["edges"], [])
        self.assertNotIn("investigation", initial)

    def test_no_open_cli_keeps_existing_deterministic_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "viewer.html"
            graph = {"repo_path": "/repo", "nodes": [], "edges": [], "summary": {}, "investigation": {"status": "PASS"}}
            with (
                patch.object(launch_graph.sys, "argv", ["launch_graph.py", "/repo", "--no-open", "--scenario", "demo", "--out", str(output)]),
                patch.object(launch_graph, "run_analyzer", return_value={"analyzed": True}) as analyze,
                patch.object(launch_graph, "run_investigation", return_value=graph) as investigate,
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                launch_graph.main()
            analyze.assert_called_once_with("/repo")
            investigate.assert_called_once_with("/repo", ["demo"], analysis={"analyzed": True})
            self.assertTrue(output.exists())
            self.assertIn('let GRAPH_DATA = {', output.read_text(encoding="utf-8"))

    def test_initial_investigation_presentation_is_pending(self):
        state = self._investigation_presentation(
            {"nodes": [], "edges": [], "investigation": {"status": "ERROR"}},
            {"initial": True},
        )
        self.assertEqual(state["message"], "Ready to investigate current changes")
        self.assertEqual(state["result"]["text"], "")
        self.assertEqual(state["stages"], {"analyze": "", "graph": "", "proof": ""})

    def test_successful_investigation_marks_each_stage_complete(self):
        state = self._investigation_presentation(
            {"investigation": {"status": "REGRESSION", "scenarios": [{"status": "REGRESSION"}]}},
            {},
        )
        self.assertEqual(state["stages"], {"analyze": "complete", "graph": "complete", "proof": "complete"})
        self.assertEqual(state["result"]["text"], "🔴 REGRESSION FOUND")
        self.assertEqual(state["message"], "Analysis complete · Proof complete")

    def test_investigation_error_does_not_mark_proof_complete(self):
        state = self._investigation_presentation(
            {"investigation": {"status": "ERROR", "scenarios": [{"status": "ERROR"}], "errors": []}},
            {},
        )
        self.assertEqual(state["stages"], {"analyze": "complete", "graph": "complete", "proof": "error"})
        self.assertEqual(state["result"]["text"], "⚠ INVESTIGATION ERROR")
        self.assertNotIn("Proof complete", state["message"])

    def test_running_state_keeps_stages_pending(self):
        state = self._investigation_presentation(
            {"investigation": {"status": "REGRESSION", "scenarios": [{"status": "REGRESSION"}]}},
            {"running": True},
        )
        self.assertEqual(state["stages"], {"analyze": "", "graph": "", "proof": ""})
        self.assertEqual(state["message"], "Investigating change…")
        self.assertEqual(state["result"]["text"], "")

    def _evaluate_helper(self, name, arguments):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable for viewer state checks")
        template = (Path(__file__).parent / "viewer_template.html").read_text(encoding="utf-8")
        match = re.search(rf"function {re.escape(name)}\([^\n]*\) \{{.*?\n\}}", template, flags=re.DOTALL)
        self.assertIsNotNone(match, f"viewer helper {name} was not found")
        helper = match.group(0)
        args = ", ".join(json.dumps(argument) for argument in arguments)
        script = f"const helper = new Function({json.dumps(helper + f'; return {name};')})(); process.stdout.write(JSON.stringify(helper({args})));"
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_bob_panel_requires_a_selected_proven_regression(self):
        regression_node = {"proof_status": "REGRESSION"}
        self.assertFalse(self._evaluate_helper("isProvenRegressionSelected", [{}, regression_node]))
        self.assertFalse(self._evaluate_helper("isProvenRegressionSelected", [{"investigation": {"status": "PASS"}}, regression_node]))
        self.assertTrue(self._evaluate_helper("isProvenRegressionSelected", [{"investigation": {"status": "REGRESSION"}}, regression_node]))

    def test_verify_pass_requires_successful_apply_and_verification(self):
        self.assertFalse(self._evaluate_helper("isBobFixVerified", [{"status": "applied"}, None]))
        self.assertFalse(self._evaluate_helper("isBobFixVerified", [None, {"status": "PASS"}]))
        self.assertFalse(self._evaluate_helper("isBobFixVerified", [{"status": "applied"}, {"status": "REGRESSION"}]))
        self.assertTrue(self._evaluate_helper("isBobFixVerified", [{"status": "applied"}, {"status": "PASS"}]))

    def test_verify_completion_refreshes_top_level_fix_verified_status(self):
        template = (Path(__file__).parent / "viewer_template.html").read_text(encoding="utf-8")
        start = template.index('const verifyButton = body.querySelector("#verify-bob-fix");')
        end = template.index('const bobButton = body.querySelector("#bob-analyze-button");', start)
        verify_handler = template[start:end]
        self.assertIn("renderPanel(node, edges);\n      renderInvestigationStatus(GRAPH_DATA);", verify_handler)

    def _investigation_presentation(self, data, options):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node.js is unavailable for viewer state checks")
        template = (Path(__file__).parent / "viewer_template.html").read_text(encoding="utf-8")
        match = re.search(
            r"function getInvestigationPresentation\(data, options = \{\}\) \{.*?\n\}\n\nfunction renderInvestigationStatus",
            template,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(match, "viewer presentation state helper was not found")
        helper = match.group(0).rsplit("\n\nfunction renderInvestigationStatus", 1)[0]
        script = (
            f"const getState = new Function({json.dumps(helper + '; return getInvestigationPresentation;')})();"
            f"process.stdout.write(JSON.stringify(getState({json.dumps(data)}, {json.dumps(options)})));"
        )
        result = subprocess.run([node, "-e", script], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_local_endpoint_streams_pipeline_progress_and_graph(self):
        graph_data = {"nodes": [], "edges": [], "summary": {}, "investigation": {"status": "REGRESSION"}}
        with tempfile.TemporaryDirectory() as tmp:
            viewer = Path(tmp) / "viewer.html"
            viewer.write_text("viewer", encoding="utf-8")
            server = launch_graph.make_viewer_server(viewer, "/repo", ["demo"])
            def fake_investigation(repo, scenarios, on_step):
                for step, state, message in [
                    ("analyze", "running", "Analyzing change"),
                    ("analyze", "complete", "Change detected"),
                    ("graph", "running", "Building impact graph"),
                    ("graph", "complete", "Impact graph built"),
                    ("proof", "running", "Running proof scenario"),
                    ("proof", "complete", "Proof scenario executed"),
                ]:
                    on_step(step, state, message)
                return graph_data

            with patch.object(launch_graph, "run_investigation", side_effect=fake_investigation) as investigate:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    conn = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                    conn.request("GET", "/api/investigate")
                    response = conn.getresponse()
                    lines = []
                    while True:
                        line = response.readline().decode()
                        if not line:
                            break
                        lines.append(line.rstrip("\r\n"))
                        if line.startswith("data: ") and json.loads(line[6:]).get("done"):
                            break
                    body = "\n".join(lines)
                    conn.close()
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join(timeout=2)

        self.assertEqual(response.status, 200)
        self.assertEqual(investigate.call_args.args[:2], ("/repo", ["demo"]))
        updates = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
        self.assertEqual(
            [(item["step"], item["state"]) for item in updates[:6]],
            [
                ("analyze", "running"), ("analyze", "complete"),
                ("graph", "running"), ("graph", "complete"),
                ("proof", "running"), ("proof", "complete"),
            ],
        )
        self.assertEqual(updates[-1]["graph"]["investigation"]["status"], "REGRESSION")

    def test_local_undo_endpoint_invokes_targeted_undo_tool(self):
        process = type("Completed", (), {"returncode": 0, "stdout": '{"status":"undone","file":"refund.py"}'})()
        with patch.object(launch_graph.subprocess, "run", return_value=process) as run:
            payload = launch_graph.run_undo_fix("/demo-repo")
        self.assertEqual(payload["status"], "undone")
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["undo_fix", "/demo-repo"])


if __name__ == "__main__":
    unittest.main()

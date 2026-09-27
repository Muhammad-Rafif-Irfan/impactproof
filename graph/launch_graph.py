#!/usr/bin/env python3
"""
ImpactProof Phase 3 / 4B / 7A — Impact Graph launcher.

Usage:
    python launch_graph.py <repo_path> [--out <output.html>] [--no-open]
                           [--scenario <name>]

Runs the analyzer against <repo_path>, transforms the result into graph data,
optionally runs proof scenario(s) and merges results, then writes the HTML
viewer and opens it in the browser.

Arguments:
    repo_path             Absolute or relative path to the Git repository.
    --out <file>          Output HTML file path (default: impact_graph.html)
    --no-open             Write file but do not open the browser.
    --json                Also print the raw analyze_change JSON to stdout.
    --scenario <name>     Run this proof scenario and merge into the graph
                          (may be repeated). e.g. --scenario premium_checkout_refund
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

HERE = Path(__file__).parent
ANALYZER_SCRIPT = HERE.parent / "mcp-server" / "src" / "analyze.py"
REGRESSION_TOOLS_SCRIPT = HERE.parent / "mcp-server" / "src" / "regression_tools.py"
VIEWER_TEMPLATE = HERE / "viewer_template.html"
ROOT = HERE.parent

# Import graph transformer and proof engine from project roots
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
from graph import build_graph, merge_proof_results  # noqa: E402
from proof.engine import run_scenario, load_scenario  # noqa: E402
from bob_analyzer import analyze_with_bob  # noqa: E402
from bob_bridge import BobAnalysisBridge, BobFixBridge  # noqa: E402
from bob_runner import ALLOWED_WORKSPACES  # noqa: E402


def run_analyzer(repo_path: str) -> dict:
    """Run analyze.py and return parsed JSON."""
    result = subprocess.run(
        [sys.executable, str(ANALYZER_SCRIPT), repo_path],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        print(f"[launch_graph] Analyzer process exited {result.returncode}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise RuntimeError(f"Analyzer process exited {result.returncode}: {result.stderr.strip()}")
    if result.stderr.strip():
        print("[launch_graph] Analyzer stderr:", result.stderr.strip(), file=sys.stderr)
    return json.loads(result.stdout)


def render_html(
    graph_data: dict,
    can_investigate: bool = True,
    investigate_hint: str | None = None,
    defer_initial_data: bool = False,
) -> str:
    """Inject graph_data JSON into the viewer template."""
    template = VIEWER_TEMPLATE.read_text(encoding="utf-8")
    injected = "null" if defer_initial_data else json.dumps(graph_data, indent=2)
    return (template.replace("{{GRAPH_DATA}}", injected)
            .replace("{{INVESTIGATE_DISABLED}}", "" if can_investigate else "disabled")
            .replace("{{INVESTIGATE_HINT}}", "" if can_investigate else
                     (investigate_hint or "Launch with --scenario NAME to enable proof investigation.")))


def run_investigation(
    repo_path: str,
    scenario_names: list[str],
    on_step: Callable[[str, str, str], None] | None = None,
    analysis: dict | None = None,
) -> dict:
    """Run the deterministic analyze → graph → proof pipeline."""
    if on_step:
        if analysis is None:
            on_step("analyze", "running", "Analyzing change")
    analysis = analysis if analysis is not None else run_analyzer(repo_path)
    if on_step:
        on_step("analyze", "complete", "Change detected")
        on_step("graph", "running", "Building impact graph")
    graph_data = build_graph(analysis)
    if on_step:
        on_step("graph", "complete", "Impact graph built")
    proof_results: list[dict] = []
    scenario_specs: dict[str, dict] = {}
    errors: list[str] = []
    if scenario_names and on_step:
        on_step("proof", "running", "Running proof scenario")
    for name in scenario_names:
        try:
            spec = load_scenario(name)
            scenario_specs[name] = spec
            proof_results.append(run_scenario(repo_path, spec))
        except FileNotFoundError as exc:
            errors.append(str(exc))
    if on_step:
        if errors:
            on_step("proof", "error", "Proof scenario unavailable")
        elif scenario_names:
            on_step("proof", "complete", "Proof scenario executed")
    if proof_results:
        merge_proof_results(graph_data, proof_results, scenario_specs)

    statuses = [result.get("status", "ERROR") for result in proof_results]
    status = "REGRESSION" if "REGRESSION" in statuses else (
        "ERROR" if errors or "ERROR" in statuses else "PASS" if statuses else "NO_SCENARIO"
    )
    graph_data["investigation"] = {
        "status": status,
        "scenarios": proof_results,
        "errors": errors,
    }
    return graph_data


def run_undo_fix(repo_path: str) -> dict:
    """Invoke the deterministic undo command and normalize its JSON result."""
    try:
        result = subprocess.run(
            [sys.executable, str(REGRESSION_TOOLS_SCRIPT), "undo_fix", repo_path],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            return {"status": "error", "error": "Undo command failed safely."}
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict):
            return {"status": "error", "error": "Undo returned an invalid result."}
        return payload
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return {"status": "error", "error": str(exc)}


def run_verify_fix(repo_path: str, scenario: str) -> dict:
    """Run the existing deterministic proof CLI for an explicit Verify action."""
    try:
        result = subprocess.run(
            [sys.executable, str(REGRESSION_TOOLS_SCRIPT), "verify", repo_path, scenario],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode != 0:
            return {"status": "ERROR", "error": "Verification could not establish correctness."}
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or payload.get("status") not in {"PASS", "REGRESSION", "ERROR"}:
            return {"status": "ERROR", "error": "Verification returned an invalid result."}
        return payload
    except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired):
        return {"status": "ERROR", "error": "Verification could not establish correctness."}


def make_viewer_server(
    out_path: Path,
    repo_path: str,
    scenario_names: list[str],
    initial_graph_data: dict | None = None,
):
    """Serve the viewer with local deterministic investigation and undo endpoints."""
    graph_state_lock = threading.Lock()
    graph_state = {"latest": initial_graph_data}
    bob_bridge = BobAnalysisBridge(ALLOWED_WORKSPACES, analyzer=analyze_with_bob)
    bob_fix_bridge = BobFixBridge(ALLOWED_WORKSPACES, bob_bridge)
    verify_lock = threading.Lock()
    verified_repos: set[Path] = set()
    verified_lock = threading.Lock()

    def request_is_same_origin(handler: BaseHTTPRequestHandler) -> bool:
        expected_host = f"127.0.0.1:{handler.server.server_port}"
        return (
            handler.headers.get("Host", "") == expected_host
            and handler.headers.get("Origin") in (None, f"http://{expected_host}")
        )

    class ViewerHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            print(f"[launch_graph] {fmt % args}", file=sys.stderr)

        def do_GET(self):
            if self.path == "/api/investigate":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()

                def emit(payload: dict) -> None:
                    self.wfile.write(("data: " + json.dumps(payload) + "\n\n").encode())
                    self.wfile.flush()

                try:
                    bob_bridge.invalidate(Path(repo_path))
                    with verified_lock:
                        verified_repos.discard(Path(repo_path).resolve())
                    graph_data = run_investigation(
                        repo_path,
                        scenario_names,
                        on_step=lambda step, state, message: emit({
                            "step": step, "state": state, "message": message,
                        }),
                    )
                    with graph_state_lock:
                        graph_state["latest"] = graph_data
                    emit({"done": True, "graph": graph_data})
                except Exception as exc:
                    emit({"error": str(exc)})
                return

            if self.path != "/":
                self.send_error(404)
                return
            try:
                body = out_path.read_bytes()
            except OSError:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path == "/api/bob/analyze":
                self.handle_bob_analysis()
                return
            if self.path == "/api/bob/fix":
                self.handle_bob_fix()
                return
            if self.path == "/api/verify-fix":
                self.handle_verify_fix()
                return
            if self.path != "/api/undo-fix":
                self.send_error(404)
                return
            if not request_is_same_origin(self):
                self._send_json(403, {"status": "error", "message": "Request rejected"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 1024 or self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                    raise ValueError("Request body is too large.")
                request = json.loads(self.rfile.read(length)) if length else {}
                if request != {}:
                    raise ValueError("Invalid undo request.")
                served_repo = Path(repo_path).resolve(strict=True)
                if not bob_fix_bridge.has_applied(served_repo):
                    self._send_json(409, {"status": "error", "error": "No confirmed Bob fix is available to undo."})
                    return
                with verified_lock:
                    verified = served_repo in verified_repos
                if not verified:
                    self._send_json(409, {"status": "error", "error": "Verify the applied fix before undoing it."})
                    return
                payload = run_undo_fix(repo_path)
                if payload.get("status") == "undone":
                    bob_fix_bridge.mark_undone(served_repo)
                    with verified_lock:
                        verified_repos.discard(served_repo)
            except (ValueError, json.JSONDecodeError) as exc:
                payload = {"status": "error", "error": str(exc)}
            except (OSError, RuntimeError):
                payload = {"status": "error", "error": "Undo could not be performed safely."}
            self._send_json(200, payload)

        def _send_json(self, status_code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def handle_bob_analysis(self) -> None:
            # The page is served only on loopback. Restrict Host and browser Origin
            # to that same origin to reduce DNS-rebinding and cross-origin abuse.
            expected_host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host", "") != expected_host:
                self._send_json(403, {"status": "error", "message": "Request rejected"})
                return
            origin = self.headers.get("Origin")
            if origin is not None and origin != f"http://{expected_host}":
                self._send_json(403, {"status": "error", "message": "Request rejected"})
                return
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._send_json(415, {"status": "error", "message": "Invalid analysis request"})
                return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0 or length > 4096:
                    raise ValueError("invalid content length")
                request = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"status": "error", "message": "Invalid analysis request"})
                return

            try:
                served_repo = Path(repo_path).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                self._send_json(503, {"status": "unavailable", "message": "Bob analysis unavailable"})
                return
            with graph_state_lock:
                latest = graph_state["latest"]
            regression_detected = (
                isinstance(latest, dict)
                and isinstance(latest.get("investigation"), dict)
                and latest["investigation"].get("status") == "REGRESSION"
            )
            result = bob_bridge.handle(request, served_repo, regression_detected)
            self._send_json(result.status_code, result.payload)

        def _read_local_json(self, expected_keys: set[str], max_bytes: int = 4096) -> dict | None:
            if not request_is_same_origin(self):
                self._send_json(403, {"status": "error", "message": "Request rejected"})
                return None
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                self._send_json(415, {"status": "error", "message": "Invalid request"})
                return None
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if length < 0 or length > max_bytes:
                    raise ValueError("invalid content length")
                payload = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                self._send_json(400, {"status": "error", "message": "Invalid request"})
                return None
            if not isinstance(payload, dict) or set(payload) != expected_keys:
                self._send_json(400, {"status": "error", "message": "Invalid request"})
                return None
            return payload

        def _current_repo_and_regression(self):
            try:
                served_repo = Path(repo_path).expanduser().resolve(strict=True)
            except (OSError, RuntimeError):
                self._send_json(503, {"status": "unavailable", "message": "ImpactProof workflow unavailable"})
                return None, False
            with graph_state_lock:
                latest = graph_state["latest"]
            regression_detected = (
                isinstance(latest, dict)
                and isinstance(latest.get("investigation"), dict)
                and latest["investigation"].get("status") == "REGRESSION"
            )
            return served_repo, regression_detected

        def handle_bob_fix(self) -> None:
            request = self._read_local_json({"repo_path"})
            if request is None:
                return
            served_repo, regression_detected = self._current_repo_and_regression()
            if served_repo is None:
                return
            result = bob_fix_bridge.handle(request, served_repo, regression_detected)
            if result.status_code == 200:
                with verified_lock:
                    verified_repos.discard(served_repo)
            self._send_json(result.status_code, result.payload)

        def handle_verify_fix(self) -> None:
            request = self._read_local_json({"repo_path", "scenario"})
            if request is None:
                return
            served_repo, _ = self._current_repo_and_regression()
            if served_repo is None:
                return
            if (
                not isinstance(request.get("repo_path"), str)
                or Path(request["repo_path"]).expanduser().resolve() != served_repo
                or request.get("scenario") not in scenario_names
                or served_repo not in ALLOWED_WORKSPACES
            ):
                self._send_json(400, {"status": "error", "message": "Invalid verification request"})
                return
            if not bob_fix_bridge.has_applied(served_repo):
                self._send_json(409, {"status": "error", "message": "Apply a confirmed Bob fix before verification."})
                return
            if not verify_lock.acquire(blocking=False):
                self._send_json(409, {"status": "error", "message": "Verification is already running."})
                return
            try:
                proof = run_verify_fix(str(served_repo), request["scenario"])
            finally:
                verify_lock.release()
            status = proof.get("status")
            if status not in {"PASS", "REGRESSION", "ERROR"}:
                status = "ERROR"
            safe_proof = {
                "status": status,
                "scenario": request["scenario"],
                "expected": proof.get("expected") if isinstance(proof.get("expected"), dict) else {},
                "actual": proof.get("actual") if isinstance(proof.get("actual"), dict) else {},
                "mismatches": proof.get("mismatches") if isinstance(proof.get("mismatches"), list) else [],
            }
            if status == "ERROR" and isinstance(proof.get("error"), str):
                safe_proof["error"] = proof["error"][:200]
            if status == "PASS":
                with verified_lock:
                    verified_repos.add(served_repo)
            else:
                with verified_lock:
                    verified_repos.discard(served_repo)
            self._send_json(200, {"status": "success", "verification": safe_proof})

    return ThreadingHTTPServer(("127.0.0.1", 0), ViewerHandler)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ImpactProof Impact Graph — visualize change blast radius"
    )
    parser.add_argument("repo_path", help="Path to the Git repository to analyze")
    parser.add_argument(
        "--out", default="impact_graph.html",
        help="Output HTML file (default: impact_graph.html)"
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Write output file but do not open the browser"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Also print raw analyze_change JSON to stdout"
    )
    parser.add_argument(
        "--scenario", action="append", default=[],
        metavar="NAME",
        help="Proof scenario name to run and merge (may be repeated)"
    )
    args = parser.parse_args()

    repo_path = os.path.abspath(args.repo_path)
    out_path = Path(args.out).resolve()

    if args.no_open:
        # Preserve the existing CLI/export behavior. The interactive dashboard
        # starts empty and runs this same deterministic pipeline only when the
        # user activates INVESTIGATE CHANGE.
        print(f"[launch_graph] Analyzing {repo_path} …", file=sys.stderr)
        analysis = run_analyzer(repo_path)
        if args.json:
            print(json.dumps(analysis, indent=2))
        graph_data = run_investigation(repo_path, args.scenario, analysis=analysis)
    else:
        if args.json:
            # The explicit JSON diagnostic remains available without seeding
            # the interactive dashboard/server with an investigation result.
            print(f"[launch_graph] Analyzing {repo_path} …", file=sys.stderr)
            print(json.dumps(run_analyzer(repo_path), indent=2))
        graph_data = {
            "repo_path": repo_path,
            "nodes": [],
            "edges": [],
            "summary": {
                "changed_files": 0,
                "affected_files": 0,
                "symbol_relationships": 0,
                "regressions": 0,
            },
        }
    for scenario_error in graph_data.get("investigation", {}).get("errors", []):
        print(f"[launch_graph] Scenario not found: {scenario_error}", file=sys.stderr)

    html = render_html(
        graph_data,
        can_investigate=bool(args.scenario) and not args.no_open,
        investigate_hint=("Run the launcher without --no-open to use the investigation button."
                          if args.scenario else None),
        defer_initial_data=not args.no_open,
    )
    out_path.write_text(html, encoding="utf-8")

    print(f"[launch_graph] Graph written to: {out_path}", file=sys.stderr)

    if "investigation" in graph_data:
        summary = graph_data.get("summary", {})
        regression_count = summary.get("regressions", 0)
        print(
            f"[launch_graph] {summary.get('changed_files', 0)} changed file(s), "
            f"{summary.get('affected_files', 0)} affected file(s), "
            f"{len(graph_data['nodes'])} nodes, "
            f"{len(graph_data['edges'])} edges"
            + (f", {regression_count} regression(s)" if regression_count else ""),
            file=sys.stderr,
        )
    else:
        print("[launch_graph] Dashboard ready; investigation starts on user action.", file=sys.stderr)

    if not args.no_open:
        if not args.scenario:
            print("[launch_graph] Investigate Change requires --scenario NAME.", file=sys.stderr)
        server = make_viewer_server(out_path, repo_path, args.scenario, graph_data)
        url = f"http://127.0.0.1:{server.server_port}/"
        webbrowser.open(url)
        print(f"[launch_graph] Viewer and investigation bridge running at {url}", file=sys.stderr)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[launch_graph] Viewer stopped.", file=sys.stderr)
        finally:
            server.server_close()


if __name__ == "__main__":
    main()

"""Bob analysis orchestration and strict result validation.

The runner gives this workflow a temporary project MCP configuration that
points to the dedicated analysis entrypoint. Results remain untrusted until
validated below.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from bob_runner import BobDiagnostic, BobRunResult, PROJECT_ROOT, run_bob_task


SCENARIO = "premium_checkout_refund"
BOB_MODE = "impactproof-analysis"
MAX_BOB_MESSAGE_CHARS = 20_000
_JSON_FENCE = re.compile(r"```json\s*\n?(.*?)\n?```", re.IGNORECASE | re.DOTALL)
_REQUIRED_PAYLOAD_KEYS = {"analysis", "explanation", "root_cause", "bob_message"}


@dataclass(frozen=True)
class BobAnalysisResult:
    """Validated, dashboard-oriented view of Bob's reported evidence."""

    status: str
    changed_files: list[dict[str, Any]] = field(default_factory=list)
    changed_symbols: list[dict[str, str]] = field(default_factory=list)
    directly_affected_files: list[str] = field(default_factory=list)
    indirectly_affected_files: list[str] = field(default_factory=list)
    regression_node: dict[str, Any] | None = None
    regression_nodes: list[dict[str, Any]] = field(default_factory=list)
    proof_status: str | None = None
    expected_values: dict[str, Any] | None = None
    actual_values: dict[str, Any] | None = None
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    root_cause: str | None = None
    bob_message: str = ""
    bob_tool_call_count: int | None = None
    error: str | None = None
    diagnostic: BobDiagnostic | None = None


def _validate_repo_path(repo_path: Path) -> Path:
    if not isinstance(repo_path, Path):
        raise ValueError("repo_path must be a pathlib.Path")
    try:
        resolved = repo_path.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("repo_path must exist and resolve to a directory") from exc
    if not resolved.is_dir():
        raise ValueError("repo_path must be a directory")

    allowed: set[Path] = set()
    for root in (PROJECT_ROOT, Path("/home/mri/Documents/impactproof_demo")):
        try:
            allowed.add(root.resolve(strict=True))
        except (OSError, RuntimeError):
            continue
    if resolved not in allowed:
        raise ValueError("repo_path is outside the allowed ImpactProof repositories")
    return resolved


def _build_prompt(repo_path: Path) -> str:
    """Build the fixed analysis-only workflow prompt."""
    repo = json.dumps(str(repo_path))
    return f"""Use only the ImpactProof MCP evidence for repository {repo}.

Workflow:
1. Call the ImpactProof MCP tool analyze_change with repo_path {repo}.
2. Then call explain_regression with repo_path {repo} and scenario {SCENARIO}.
3. Base any root-cause explanation only on the structured results returned by
   those two calls. Do not infer dependencies or invent values.

This task is analysis only. Call only analyze_change and explain_regression.
Do not edit or create files. Do not call apply_fix, verify_fix, or undo_fix.
Do not perform remediation. Return evidence only.

Return one raw JSON object with no Markdown fence and no surrounding prose,
using this exact shape:
{{
  "analysis": <the complete structuredContent returned by analyze_change>,
  "explanation": <the complete structuredContent returned by explain_regression>,
  "root_cause": <evidence-supported string, or null if unsupported>,
  "bob_message": <brief user-facing explanation>
}}

Do not report a proof PASS or REGRESSION unless explain_regression returned that
proof_status. If a tool fails or evidence is missing, preserve that uncertainty
in the returned values and explanation."""


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _balanced_json_object_end(text: str, start: int) -> int | None:
    """Return the end of one balanced JSON object, respecting quoted strings."""
    if start >= len(text) or text[start] != "{":
        return None
    stack: list[str] = []
    in_string = False
    escaped = False
    matching = {"}": "{", "]": "["}
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if not stack or stack[-1] != matching[char]:
                return None
            stack.pop()
            if not stack:
                return index + 1
    return None


def _decode_final_payload(message: str) -> dict[str, Any] | None:
    """Accept raw JSON, one JSON fence, or one isolated object with short prose.

    Extraction is deliberately narrow. Surrounding text cannot contain other
    braces, brackets, or Markdown fences, and an extracted object must have the
    expected top-level contract keys before normal evidence validation runs.
    """
    text = message.strip()
    try:
        direct = json.loads(text)
    except json.JSONDecodeError:
        direct = None
    if isinstance(direct, dict):
        return direct

    fences = list(_JSON_FENCE.finditer(text))
    if len(fences) == 1:
        match = fences[0]
        surrounding = text[:match.start()].strip() + text[match.end():].strip()
        body = match.group(1).strip()
        if (
            len(surrounding) <= 500
            and not any(char in surrounding for char in "{}[]`")
            and not _JSON_FENCE.search(surrounding)
        ):
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                payload = None
            if isinstance(payload, dict):
                return payload

    start = text.find("{")
    if start < 0:
        return None
    end = _balanced_json_object_end(text, start)
    if end is None:
        return None
    prefix, suffix = text[:start].strip(), text[end:].strip()
    if (
        len(prefix) > 500
        or len(suffix) > 500
        or any(char in prefix + suffix for char in "{}[]`")
        or "{" in text[end:]
    ):
        return None
    try:
        payload = json.loads(text[start:end])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict) or not _REQUIRED_PAYLOAD_KEYS.issubset(payload):
        return None
    return payload


def _parse_bob_analysis_payload(run_result: BobRunResult, expected_repo: Path) -> BobAnalysisResult:
    """Validate Bob Shell's documented JSON envelope and requested payload."""
    if run_result.status != "success" or run_result.exit_code != 0:
        return BobAnalysisResult(
            status="error",
            error=run_result.error or f"Bob Shell failed with status {run_result.status}.",
        )

    envelope = run_result.parsed_json
    if not isinstance(envelope, dict) or envelope.get("type") != "result":
        return BobAnalysisResult(status="error", error="Bob returned no valid result envelope.")
    if envelope.get("status") != "success":
        return BobAnalysisResult(status="error", error="Bob Shell reported an unsuccessful task.")
    stats = envelope.get("stats")
    tool_calls = stats.get("tool_calls") if isinstance(stats, dict) else None
    if isinstance(tool_calls, bool) or not isinstance(tool_calls, int) or tool_calls < 2:
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls if isinstance(tool_calls, int) else None,
            error="Bob result does not indicate the required tool activity.",
        )

    last_message = envelope.get("last_message")
    if not isinstance(last_message, str) or not last_message.strip():
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob returned an empty final message.",
        )
    if len(last_message) > MAX_BOB_MESSAGE_CHARS:
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's final message exceeds the accepted size.",
        )
    payload = _decode_final_payload(last_message)
    if payload is None:
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's final message is not the required JSON object.",
        )

    analysis = payload.get("analysis")
    explanation = payload.get("explanation")
    if not isinstance(analysis, dict) or not isinstance(explanation, dict):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result is missing structured analyzer or proof evidence.",
        )
    if analysis.get("error") not in (None, "") or (
        explanation.get("error") not in (None, "")
        and explanation.get("proof_status") != "ERROR"
    ):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="An ImpactProof tool reported an error.",
        )
    if analysis.get("repo_path") != str(expected_repo):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's analyzer evidence does not match the requested repository.",
        )

    changed = analysis.get("changed_files")
    direct = analysis.get("directly_affected")
    indirect = analysis.get("indirectly_affected")
    nodes = explanation.get("regression_nodes")
    expected = explanation.get("expected")
    actual = explanation.get("actual")
    mismatches = explanation.get("mismatches")
    proof_status = explanation.get("proof_status")
    root_cause = payload.get("root_cause")
    bob_message = payload.get("bob_message")

    if not isinstance(changed, list) or not _is_string_list(direct) or not _is_string_list(indirect):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result is missing changed-file or impact evidence.",
        )
    if not isinstance(nodes, list) or not all(isinstance(item, dict) for item in nodes):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result is missing regression-node evidence.",
        )
    if explanation.get("scenario") != SCENARIO:
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result does not match the fixed proof scenario.",
        )
    if not isinstance(proof_status, str) or proof_status not in {"PASS", "REGRESSION", "ERROR"}:
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result has no recognized proof status.",
        )
    if (
        not isinstance(expected, dict)
        or not isinstance(mismatches, list)
        or not all(isinstance(item, dict) for item in mismatches)
    ):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result is missing expected values or mismatch evidence.",
        )
    if proof_status in {"PASS", "REGRESSION"} and not isinstance(actual, dict):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result is missing actual proof values.",
        )
    if proof_status == "REGRESSION" and (
        not mismatches or not nodes or not isinstance(root_cause, str) or not root_cause.strip()
    ):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Regression evidence or an evidence-supported root cause is incomplete.",
        )
    if proof_status == "PASS" and mismatches:
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's PASS status conflicts with non-empty mismatch evidence.",
        )
    if root_cause is not None and not isinstance(root_cause, str):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's root-cause field has an invalid type.",
        )
    if not isinstance(bob_message, str) or not bob_message.strip():
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's result is missing its user-facing explanation.",
        )

    changed_symbols: list[dict[str, str]] = []
    for item in changed:
        if not isinstance(item, dict) or not isinstance(item.get("file"), str):
            return BobAnalysisResult(
                status="error", bob_tool_call_count=tool_calls,
                error="Bob's changed-file evidence has an invalid shape.",
            )
        symbols = item.get("symbols", [])
        if not _is_string_list(symbols):
            return BobAnalysisResult(
                status="error", bob_tool_call_count=tool_calls,
                error="Bob's changed-symbol evidence has an invalid shape.",
            )
        changed_symbols.extend({"file": item["file"], "symbol": symbol} for symbol in symbols)

    regression_node = nodes[0] if len(nodes) == 1 else None
    explanation_error = explanation.get("error")
    if explanation_error is not None and not isinstance(explanation_error, str):
        return BobAnalysisResult(
            status="error", bob_tool_call_count=tool_calls,
            error="Bob's proof error field has an invalid type.",
        )
    return BobAnalysisResult(
        status="complete",
        changed_files=changed,
        changed_symbols=changed_symbols,
        directly_affected_files=direct,
        indirectly_affected_files=indirect,
        regression_node=regression_node,
        regression_nodes=nodes,
        proof_status=proof_status,
        expected_values=expected,
        actual_values=actual if isinstance(actual, dict) else None,
        mismatches=mismatches,
        root_cause=root_cause,
        bob_message=bob_message,
        bob_tool_call_count=tool_calls,
        error=explanation_error if proof_status == "ERROR" else None,
    )


def _runner_failure_category(run_result: BobRunResult) -> str:
    if run_result.diagnostic is not None:
        return run_result.diagnostic.category
    if run_result.status == "timeout" or run_result.timed_out:
        return "timeout"
    if run_result.status == "invalid_json":
        return "output_parse"
    if run_result.status == "unavailable":
        error = (run_result.error or "").lower()
        if "executable" in error:
            return "executable_preflight"
        if "node" in error:
            return "node_preflight"
        return "process_start"
    if run_result.exit_code is not None:
        return "process_exit"
    return "process_start"


def _analysis_error_category(error: str | None) -> str:
    normalized = (error or "").lower()
    if any(marker in normalized for marker in (
        "result envelope", "empty final message", "final message exceeds",
        "final message is not", "malformed", "json object",
    )):
        return "output_parse"
    if "unsuccessful task" in normalized:
        return "process_exit"
    return "evidence_validation"


def _parse_bob_analysis(run_result: BobRunResult, expected_repo: Path) -> BobAnalysisResult:
    """Parse Bob output and attach a safe category to rejected results."""
    try:
        result = _parse_bob_analysis_payload(run_result, expected_repo)
    except Exception:
        return BobAnalysisResult(
            status="error",
            error="Bob analysis result could not be processed.",
            diagnostic=BobDiagnostic("unexpected_exception"),
        )
    if result.status != "error":
        return result

    diagnostic = result.diagnostic
    if diagnostic is None:
        if run_result.status != "success" or run_result.exit_code != 0:
            diagnostic = run_result.diagnostic
            if diagnostic is None:
                category = _runner_failure_category(run_result)
                diagnostic = BobDiagnostic(
                    category,
                    run_result.exit_code,
                    bool(run_result.stdout),
                    bool(run_result.stderr),
                )
        else:
            diagnostic = BobDiagnostic(
                _analysis_error_category(result.error),
                run_result.exit_code,
                bool(run_result.stdout),
                bool(run_result.stderr),
            )
    return replace(result, diagnostic=diagnostic)


def analyze_with_bob(repo_path: Path) -> BobAnalysisResult:
    """Request Bob analysis through the configured full MCP surface.

    The workflow asks Bob not to mutate files, but does not isolate or restrict
    Bob's MCP access. Only validated analysis evidence is returned.
    """
    try:
        repo = _validate_repo_path(repo_path)
    except ValueError as exc:
        return BobAnalysisResult(status="error", error=str(exc))

    prompt = _build_prompt(repo)
    try:
        run_result = run_bob_task(
            workspace=PROJECT_ROOT,
            prompt=prompt,
            mode=BOB_MODE,
            max_cost=1,
            max_turns=3,
            timeout_seconds=120,
        )
    except Exception:
        return BobAnalysisResult(
            status="error",
            error="Bob Shell runner raised an unexpected exception.",
            diagnostic=BobDiagnostic("unexpected_exception"),
        )
    return _parse_bob_analysis(run_result, repo)

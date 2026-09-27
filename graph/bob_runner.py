"""Isolated server-side wrapper for a single IBM Bob Shell task.

This module does not expose an HTTP interface and does not interpret Bob's
answer as proof that an MCP tool ran. Its caller must treat all returned Bob
content as untrusted data.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEMO_REPO = Path("/home/mri/Documents/impactproof_demo")
ALLOWED_WORKSPACES = (PROJECT_ROOT, DEMO_REPO)

NODE_BIN = Path("/home/mri/.config/nvm/versions/node/v22.23.3/bin")
BOB_EXECUTABLE = NODE_BIN / "bob"
BOB_RUNTIME_DIR = NODE_BIN

ANALYSIS_MODE = "impactproof-analysis"
FIX_MODE = "impactproof-fix"
ALLOWED_MODES = frozenset({"impactproof-agent", ANALYSIS_MODE, FIX_MODE})
READONLY_MCP_ENTRYPOINT = PROJECT_ROOT / "mcp-server" / "build" / "readonly_index.js"
GLOBAL_MCP_CONFIG_PATHS = (
    Path.home() / ".bob" / "settings" / "mcp.json",
    Path.home() / ".bob" / "mcp_settings.json",
)
MAX_COST = 1.0
MAX_TURNS = 10
MAX_TIMEOUT_SECONDS = 300
MAX_PROMPT_CHARS = 4_000
MAX_OUTPUT_CHARS = 1_000_000
MAX_DIAGNOSTIC_TEXT_CHARS = 1_200

_SECRET_ENV_NAME = re.compile(r"(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH)", re.I)
_SECRET_ASSIGNMENT = re.compile(
    r"(?im)(\b(?:export\s+)?[A-Za-z_][A-Za-z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|"
    r"PASSWORD|CREDENTIAL|AUTHORIZATION?)[A-Za-z0-9_]*\s*=\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_SECRET_FIELD = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"auth(?:orization)?|password|client[_-]?secret|credential)[\"']?\s*[:=]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;}]+)"
)
_AUTHORIZATION_HEADER = re.compile(r"(?im)(\bauthorization\s*:\s*)[^\r\n]+")
_BEARER_VALUE = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+")
_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/@\s:]+:[^/@\s]+@")


@dataclass(frozen=True)
class BobDiagnostic:
    """Bounded operational metadata; never contains complete process output."""

    category: str
    exit_code: int | None = None
    stdout_produced: bool = False
    stderr_produced: bool = False
    startup_error: str | None = None


@dataclass(frozen=True)
class BobRunResult:
    """Sanitized result of one Bob Shell process invocation."""

    status: str
    exit_code: int | None
    last_message: str
    parsed_json: Any | None
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    error: str | None = None
    tool_events: tuple[dict[str, Any], ...] = ()
    diagnostic: BobDiagnostic | None = None


def _validate_inputs(
    workspace: Path,
    prompt: str,
    mode: str,
    max_cost: float,
    max_turns: int,
    timeout_seconds: int,
) -> Path:
    if not isinstance(workspace, Path):
        raise ValueError("workspace must be a pathlib.Path")
    try:
        resolved = workspace.expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("workspace must exist and resolve to a directory") from exc
    if not resolved.is_dir():
        raise ValueError("workspace must be a directory")

    allowed: set[Path] = set()
    for root in ALLOWED_WORKSPACES:
        try:
            allowed.add(root.expanduser().resolve(strict=True))
        except (OSError, RuntimeError):
            continue
    if resolved not in allowed:
        raise ValueError("workspace is outside the allowed ImpactProof workspaces")

    if mode not in ALLOWED_MODES:
        raise ValueError("mode is not an allowed Bob mode")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt must be at most {MAX_PROMPT_CHARS} characters")
    if isinstance(max_cost, bool) or not isinstance(max_cost, (int, float)):
        raise ValueError("max_cost must be a number")
    if not math.isfinite(max_cost) or not 0 < max_cost <= MAX_COST:
        raise ValueError(f"max_cost must be greater than 0 and at most {MAX_COST}")
    if isinstance(max_turns, bool) or not isinstance(max_turns, int):
        raise ValueError("max_turns must be an integer")
    if not 1 <= max_turns <= MAX_TURNS:
        raise ValueError(f"max_turns must be between 1 and {MAX_TURNS}")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int):
        raise ValueError("timeout_seconds must be an integer")
    if not 1 <= timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be between 1 and {MAX_TIMEOUT_SECONDS}")
    return resolved


def _secret_values(environment: dict[str, str]) -> list[str]:
    return [
        value for key, value in environment.items()
        if _SECRET_ENV_NAME.search(key) and value
    ]


def _redact_text(value: str, secrets: list[str]) -> str:
    redacted = value
    for secret in sorted(set(secrets), key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _SECRET_ASSIGNMENT.sub(r'\1"[REDACTED]"', redacted)
    redacted = _SECRET_FIELD.sub(r'\1"[REDACTED]"', redacted)
    redacted = _AUTHORIZATION_HEADER.sub(r"\1[REDACTED]", redacted)
    redacted = _BEARER_VALUE.sub(r"\1[REDACTED]", redacted)
    return _URL_CREDENTIALS.sub(r"\1[REDACTED]@", redacted)


def _diagnostic_excerpt(value: str | bytes | None, secrets: list[str]) -> str | None:
    text = _redact_text(_decode_output(value), secrets).strip()
    if not text:
        return None
    return text[:MAX_DIAGNOSTIC_TEXT_CHARS]


def _startup_category(stderr: str) -> str:
    if re.search(
        r"(?i)(EROF[S]|read.only file system|settings|authenticat|credential|"
        r"not logged in|failed to load|unable to initialize|MCP.{0,30}(?:fail|error)|"
        r"(?:config|configuration).{0,30}(?:fail|error))",
        stderr,
    ):
        return "authentication_or_startup"
    return "process_exit"


def _redact_value(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_ENV_NAME.search(str(key)):
                clean[key] = "[REDACTED]"
            else:
                clean[key] = _redact_value(item, secrets)
        return clean
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _last_message(parsed: Any, stdout: str) -> str:
    if isinstance(parsed, dict):
        for field in ("last_message", "lastMessage", "final_message", "finalMessage", "message", "output"):
            value = parsed.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return stdout.strip()


def _parse_json(stdout: str) -> tuple[Any | None, str | None]:
    if not stdout.strip():
        return None, "Bob Shell returned no JSON output."
    try:
        return json.loads(stdout), None
    except (json.JSONDecodeError, TypeError):
        return None, "Bob Shell returned malformed JSON output."


def _parse_stream_json(stdout: str) -> tuple[Any | None, tuple[dict[str, Any], ...], str | None]:
    """Parse Bob Shell's documented NDJSON event stream without losing tool names."""
    if not stdout.strip():
        return None, (), "Bob Shell returned no stream-json output."
    events: list[dict[str, Any]] = []
    try:
        for line in stdout.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise ValueError("Invalid stream event")
            events.append(event)
    except (json.JSONDecodeError, ValueError):
        return None, (), "Bob Shell returned malformed stream-json output."
    result = next((event for event in reversed(events) if event.get("type") == "result"), None)
    if result is None:
        return None, tuple(event for event in events if event.get("type") in {"tool_use", "tool_result"}), "Bob Shell stream has no final result event."
    tool_events = tuple(
        event for event in events if event.get("type") in {"tool_use", "tool_result"}
    )
    return result, tool_events, None


def _assert_no_global_mcp_servers() -> None:
    """Fail closed if documented global MCP configs could add other servers."""
    for config_path in GLOBAL_MCP_CONFIG_PATHS:
        if not config_path.exists():
            continue
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("A global Bob MCP configuration cannot be verified.") from exc
        servers = config.get("mcpServers") if isinstance(config, dict) else None
        if not isinstance(servers, dict):
            raise ValueError("A global Bob MCP configuration has an invalid shape.")
        if servers:
            # Project configuration precedence is per server name, so any
            # global entry could add tools to this process.
            raise ValueError("Global Bob MCP servers are configured; isolation cannot be guaranteed.")


def _write_analysis_workspace(workspace: Path) -> None:
    """Write the complete fixed project config for an isolated Bob analysis."""
    if not READONLY_MCP_ENTRYPOINT.is_file():
        raise FileNotFoundError("The ImpactProof analysis MCP entrypoint is not built.")
    bob_dir = workspace / ".bob"
    bob_dir.mkdir(mode=0o700)
    mcp_config = {
        "mcpServers": {
            "impactproof-analysis": {
                "command": str(NODE_BIN / "node"),
                "args": [str(READONLY_MCP_ENTRYPOINT)],
            }
        }
    }
    (bob_dir / "mcp.json").write_text(
        json.dumps(mcp_config, indent=2) + "\n", encoding="utf-8"
    )
    custom_modes = """customModes:
  - slug: impactproof-analysis
    name: ImpactProof Analysis
    description: Analyze change impact and deterministic regression evidence.
    roleDefinition: >-
      You interpret ImpactProof analysis and proof evidence. You do not make
      source changes or perform remediation.
    whenToUse: >-
      Use to analyze a repository change using ImpactProof evidence.
    customInstructions: >-
      Call analyze_change and explain_regression. Base conclusions on their
      returned evidence. Do not modify files or perform remediation.
    groups:
      - read
      - mcp
"""
    (bob_dir / "custom_modes.yaml").write_text(custom_modes, encoding="utf-8")


def run_bob_task(
    workspace: Path,
    prompt: str,
    mode: str = "impactproof-agent",
    max_cost: float = 1,
    max_turns: int = 3,
    timeout_seconds: int = 120,
    output_format: str = "json",
) -> BobRunResult:
    """Run one bounded Bob Shell task in an approved ImpactProof workspace.

    This calls the fixed local executable with JSON output. It never accepts
    caller-supplied executable paths, CLI flags, or environment variables.
    """
    if output_format not in {"json", "stream-json"}:
        raise ValueError("output_format must be json or stream-json")
    resolved_workspace = _validate_inputs(
        workspace, prompt, mode, max_cost, max_turns, timeout_seconds
    )
    if not BOB_EXECUTABLE.is_file() or not os.access(BOB_EXECUTABLE, os.X_OK):
        return BobRunResult(
            "unavailable", None, "", None, "", "", 0.0, False,
            "The configured Bob Shell executable is unavailable.",
            diagnostic=BobDiagnostic("executable_preflight"),
        )
    if not (BOB_RUNTIME_DIR / "node").is_file():
        return BobRunResult(
            "unavailable", None, "", None, "", "", 0.0, False,
            "The configured Node.js runtime is unavailable.",
            diagnostic=BobDiagnostic("node_preflight"),
        )

    environment = os.environ.copy()
    environment["PATH"] = str(BOB_RUNTIME_DIR) + os.pathsep + environment.get("PATH", "")
    secrets = _secret_values(environment)
    started = time.monotonic()
    temporary_workspace: tempfile.TemporaryDirectory[str] | None = None
    bob_workspace = resolved_workspace

    try:
        if mode == ANALYSIS_MODE:
            _assert_no_global_mcp_servers()
            temporary_workspace = tempfile.TemporaryDirectory(prefix="impactproof-bob-analysis-")
            bob_workspace = Path(temporary_workspace.name)
            _write_analysis_workspace(bob_workspace)
        command = [
            str(BOB_EXECUTABLE), "run",
            "--workspace", str(bob_workspace),
            "--mode", mode,
            "--format", output_format,
            "--max-cost", str(float(max_cost)),
            "--max-turns", str(max_turns),
            "--", prompt,
        ]
        completed = subprocess.run(
            command,
            cwd=str(bob_workspace),
            env=environment,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.monotonic() - started
        stdout_raw = _decode_output(exc.stdout)
        stderr_raw = _decode_output(exc.stderr)
        stdout = _diagnostic_excerpt(stdout_raw, secrets) or ""
        stderr = _diagnostic_excerpt(stderr_raw, secrets) or ""
        return BobRunResult(
            "timeout", None, stdout.strip(), None, stdout, stderr,
            duration, True, "Bob Shell exceeded the configured timeout.",
            diagnostic=BobDiagnostic(
                "timeout", None, bool(stdout_raw), bool(stderr_raw), stderr or None,
            ),
        )
    except OSError as exc:
        duration = time.monotonic() - started
        message = _diagnostic_excerpt(str(exc), secrets) or "Bob Shell process could not be started."
        return BobRunResult(
            "error", None, "", None, "", "", duration, False, message,
            diagnostic=BobDiagnostic("process_start", startup_error=message),
        )
    except ValueError as exc:
        duration = time.monotonic() - started
        message = _diagnostic_excerpt(str(exc), secrets) or "Bob Shell startup configuration could not be prepared."
        return BobRunResult(
            "unavailable", None, "", None, "", "", duration, False, message,
            diagnostic=BobDiagnostic("process_start", startup_error=message),
        )
    finally:
        if temporary_workspace is not None:
            temporary_workspace.cleanup()

    duration = time.monotonic() - started
    stdout_raw = _redact_text(_decode_output(completed.stdout), secrets)[:MAX_OUTPUT_CHARS]
    stderr_raw = _redact_text(_decode_output(completed.stderr), secrets)[:MAX_OUTPUT_CHARS]
    if output_format == "stream-json":
        parsed, tool_events, parse_error = _parse_stream_json(stdout_raw)
    else:
        parsed, parse_error = _parse_json(stdout_raw)
        tool_events = ()
    parsed = _redact_value(parsed, secrets)
    tool_events = tuple(_redact_value(event, secrets) for event in tool_events)
    # Never retain the raw process stream as a fallback message. A valid
    # envelope must explicitly provide its assistant message to consumers.
    message = _last_message(parsed, "")
    stdout = _diagnostic_excerpt(stdout_raw, secrets) or ""
    stderr = _diagnostic_excerpt(stderr_raw, secrets) or ""

    if completed.returncode != 0:
        category = _startup_category(stderr_raw)
        return BobRunResult(
            "error", completed.returncode, "", None, stdout, stderr,
            duration, False, tool_events=tool_events,
            error=f"Bob Shell exited with status {completed.returncode}.",
            diagnostic=BobDiagnostic(
                category, completed.returncode, bool(stdout_raw), bool(stderr_raw), stderr or None,
            ),
        )
    if parse_error:
        return BobRunResult(
            "invalid_json", completed.returncode, "", None, stdout, stderr,
            duration, False, error=parse_error, tool_events=tool_events,
            diagnostic=BobDiagnostic(
                "output_parse", completed.returncode, bool(stdout_raw), bool(stderr_raw),
            ),
        )
    return BobRunResult(
        "success", completed.returncode, message, parsed, stdout, stderr,
        duration, False, tool_events=tool_events,
    )

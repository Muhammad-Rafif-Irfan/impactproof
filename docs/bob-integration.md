# IBM Bob integration

ImpactProof uses IBM Bob as its agentic orchestration and reasoning layer. Deterministic analysis and proof remain in ImpactProof's Python engine and MCP tools.

## Bob IDE project configuration

The project contains:

- `.bob/mcp.json`: registers the full ImpactProof MCP server.
- `.bob/custom_modes.yaml`: defines `ImpactProof` (`impactproof-agent`) and `ImpactProof Fix` (`impactproof-fix`).
- `.bob/rules-impactproof-agent/impactproof-workflow.md`: evidence-led workflow rules for the agent mode.
- `.bob/commands/impactproof-investigate.md`: MCP investigation workflow command.
- `.bob/commands/impact.md`: dashboard launch instructions for the local hackathon setup.

Bob IDE connects to the MCP server over STDIO. The full server exposes six tools. A separate read-only entrypoint is used by the dashboard's Bob analysis task; it registers only `analyze_change` and `explain_regression`.

The checked-in MCP/launcher setup includes machine-specific executable paths. A fresh checkout must be configured for its local installation. The repository's Bob configuration format is documented in the files above; do not place credentials in `.bob/`.

The `impactproof-fix` IDE mode has the `mcp` tool group and instructions to call only `apply_fix`; the repository does not define a per-tool ACL for that Bob IDE mode. Do not treat its instruction text as structural isolation. The dashboard's Bob Fix bridge separately validates the named tool event/result and rejects unexpected calls. Bob IDE approval prompts depend on the installed IDE's approval settings; those settings are not configured by this repository.

## Tools registered by the full MCP server

Tool inputs below are the registered input fields. Outputs are structured JSON in MCP `structuredContent` and text content.

| Tool | Inputs | Output/purpose | Modifies repository source? |
| --- | --- | --- | --- |
| `impactproof_status` | None | Readiness status. | No |
| `analyze_change` | `repo_path` | Git/Python static analysis: changed files/symbols and impact relationships. | No |
| `explain_regression` | `repo_path`, `scenario` | Runs proof and returns status, changed evidence, expected/actual values, mismatch, regression nodes, and source context. | No source fix; scenario executes target code. |
| `apply_fix` | `repo_path`, `file_rel`, `new_source` | Applies the supplied complete replacement contents as a targeted source update and returns status/file/undo metadata. | Yes |
| `verify_fix` | `repo_path`, `scenario` | Reruns deterministic proof and returns `PASS`, `REGRESSION`, or `ERROR`. | No source fix; scenario executes target code. |
| `undo_fix` | `repo_path` | Reverses the most recent recorded successful `apply_fix` if state validates. | Yes, on successful undo. |

### Tool safeguards

`apply_fix` accepts Python source files only, blocks test and scenario paths and path traversal, validates syntax before writing, and records backup/undo state. `undo_fix` validates its recorded change against the current file and fails safely when state is missing, ambiguous, or diverged; its implementation targets the recorded change rather than restoring the repository. `verify_fix` uses the same deterministic proof engine and does not report PASS based on Bob's interpretation.

These are code-level checks, not a claim that arbitrary application code is sandboxed.

## Dashboard Bob paths

1. **Analysis:** on explicit user action, the server-side `BobAnalysisBridge` calls `BobAnalyzer` and `BobRunner`. Runner creates a temporary workspace/configuration that points to `readonly_index.js`. That MCP server exposes only `analyze_change` and `explain_regression`. Bob's JSON response is checked against the evidence and scenario requirements before the dashboard displays it.
2. **Fix:** after valid regression analysis and an explicit user action, `BobFixBridge` requests one `apply_fix` call. It checks the named tool event and the matching result, target file, and undo identifier before reporting success. It does not let the browser supply arbitrary Bob flags or MCP configuration.
3. **Verify:** a separate dashboard action invokes deterministic verification. It is not automatic after Apply.
4. **Undo:** an optional explicit action invokes `undo_fix`; it is not run automatically.

In the dashboard, the explicit Explain and Apply controls are the user's workflow gates. Bob IDE tool approval behavior remains controlled by the local IDE configuration rather than by these project files.

The local dashboard process keeps Bob authentication server-side. `scripts/impactproof-dashboard` reads only `BOB_API_KEY` from a user-local `~/.config/impactproof/bob.env` file; the file is not created by the application. Do not commit it or expose its contents.

## What Bob does and does not do

**Bob does:** orchestrate MCP calls, interpret the returned evidence, explain a likely cause, and request an evidence-supported targeted fix when the developer asks.

**Bob does not:** replace Git diff/AST/dependency analysis, execute the proof itself, determine authoritative PASS/REGRESSION from prose, or make the final verification claim. ImpactProof executes and validates those deterministic operations.

The workflow contract is also in [`impactproof-agent-workflow.md`](impactproof-agent-workflow.md). The repository contains no custom-agent API implementation; the integration uses the checked-in Bob project modes, Markdown commands, and MCP configuration.

# ImpactProof Agent workflow contract

This document defines the repository-side workflow contract for IBM Bob. It is not an IBM Bob Custom Mode definition. The project ImpactProof mode loads a short rule that directs Bob to follow this contract.

## Responsibility boundary

ImpactProof is the source of truth for Git changes, AST and dependency analysis, graph construction, proof execution, and verification. Bob interprets the returned evidence, explains a likely cause, and chooses a focused source change. Bob must not invent dependencies, regression results, or verification results.

## Workflow: Explain → Apply Fix → Verify → Undo Fix (optional)

1. Keep the absolute repository root and proof scenario name available for each tool call. When no fresh analyzer result was provided by the current interaction, call `analyze_change` once to retrieve the current changed files, symbols, and analyzer relationships. A graph UI result is a useful signal, but it is not a substitute for proof evidence.
2. When a regression is reported, call `explain_regression` once with that repository root and scenario. It reruns the deterministic scenario and returns `proof_status`, `changed`, `regression_nodes`, `expected`, `actual`, `mismatches`, `call_chains`, `source_snippets`, and any `error`. Combine these fields with `analyze_change` relationships to form one evidence bundle. An empty `call_chains` list is not permission to invent a dependency; use the analyzer relationships or state that the evidence does not show a chain. Do not ask the developer to reconstruct facts returned by either tool.
3. If `proof_status` is `ERROR`, say that the proof could not establish a result. Do not describe it as a regression or a pass. If it is `PASS`, report that the scenario currently passes; do not propose a regression fix. Continue toward a fix only when the status is `REGRESSION`.
4. Explain a likely cause by tying the reported mismatch to the changed symbols, regression node, source snippets, `call_chains`, and `analyze_change` relationships. Treat the explanation as a reasoned interpretation of those fields. State when evidence is missing; do not fill gaps with guessed dependencies.
5. Propose the smallest relevant source change. Before preparing `new_source`, inspect the complete target file through the project workspace so unrelated code is preserved. The `source_snippets` field contains relevant function text, not necessarily the complete file.
6. Use `apply_fix` with the same `repo_path`, the evidence-supported `file_rel`, and the complete updated file contents. Do not edit tests, proof scenario files, unrelated files, or broaden the change into a refactor. Respect any tool error and its existing path, file-type, test-file, scenario-file, and syntax checks.
7. Immediately after `apply_fix` returns `status: "applied"`, call `verify_fix` with the same repository and scenario. Do not insert another action or claim success between applying and verifying.
8. Report the verification result exactly:
   - `PASS`: the deterministic proof passed; report the fix as verified and include the observed expected/actual values that support the result.
   - `REGRESSION`: the fix did not resolve the proof mismatch; do not claim success.
   - `ERROR`: verification could not establish correctness; do not claim success.
   - An `apply_fix` error or any result without an authoritative verification status is not a verified fix.
9. After a verified fix, make `undo_fix` available as an explicit developer action. Never call `undo_fix` automatically. Call it only when the developer explicitly requests Undo; report its result and do not claim anything was reverted unless it returns `status: "undone"`.

## Tool output contract

The MCP tools `analyze_change`, `explain_regression`, `apply_fix`, and `verify_fix` return JSON objects in MCP `structuredContent`. They retain the formatted JSON text content for clients that only consume text. The object fields are the existing deterministic tool outputs; MCP transport formatting does not alter their meaning.

- `analyze_change`: analyzer output such as `changed_files`, `directly_affected`, `indirectly_affected`, `relationships`, `symbol_relationships`, and `summary`.
- `explain_regression`: proof outcome and the evidence fields listed in step 2.
- `apply_fix`: application `status`, target `file`, backup path, and an `undo_id` for the recorded inverse edit, or an `error`. Undo metadata is kept under this repository's Git metadata, outside its working tree.
- `verify_fix`: authoritative proof `status` (`PASS`, `REGRESSION`, or `ERROR`) and its expected/actual values, mismatches, or error details.
- `undo_fix`: reverses only the most recent successfully recorded `apply_fix` line changes, or returns `status: "error"` without changing source when the state is missing, stale, or ambiguous. Invoke it only after an explicit developer request.

## Current Bob discovery path

The checked-in `.bob/mcp.json` starts the built full MCP entrypoint at `mcp-server/build/index.js` over MCP stdio. After changing MCP tool registrations, rebuild the entrypoint with `npm run build` from `mcp-server/`. `.bob/custom_modes.yaml` defines the project-level ImpactProof mode with slug `impactproof-agent`, `.bob/rules-impactproof-agent/` loads its workflow instruction, and `.bob/commands/impactproof-investigate.md` provides the `/impactproof-investigate` entry point. Bob must still connect to the server and invoke the tools in a live IDE session; this repository configuration alone does not demonstrate a live Bob run.

# Bob session evidence plan

This manifest is a capture plan. It does not indicate that any screenshots have been captured; the PNG evidence remains to be added after the corresponding real IBM Bob IDE sessions.

## Task 01 — MCP + ImpactProof investigation

- **Purpose:** Record Bob connected to the ImpactProof MCP server and starting the investigation workflow.
- **Demonstrate:** Bob can use the project’s ImpactProof tools to investigate a code change.
- **Show in screenshot:** The Bob IDE task session summary identifying the ImpactProof investigation, with the MCP/tool use and investigation outcome visible where the summary provides them.
- **Suggested filename:** `<team>_task01_mcp-impactproof-investigation_summary.png`
- **Status:** Not captured.

## Task 02 — Regression investigation and evidence analysis

- **Purpose:** Record Bob examining a regression using ImpactProof execution evidence.
- **Demonstrate:** The regression result and evidence Bob used to understand the affected change, expected and actual values, and relevant source/call-chain context.
- **Show in screenshot:** The Bob IDE task session summary with the regression finding and evidence-based analysis visible.
- **Suggested filename:** `<team>_task02_regression-evidence-analysis_summary.png`
- **Status:** Not captured.

## Task 03 — Explain → Fix → Verify workflow

- **Purpose:** Record Bob explaining the root cause, applying a minimal source fix, and verifying it through ImpactProof.
- **Demonstrate:** The complete tool sequence and the verification result; Bob reports success only after `verify_fix` returns PASS.
- **Show in screenshot:** The Bob IDE task session summary with the explanation, fix action, `verify_fix` result, and final PASS visible.
- **Suggested filename:** `<team>_task03_explain-fix-verify_summary.png`
- **Status:** Not captured.

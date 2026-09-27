# ImpactProof workflow rules

Follow `docs/impactproof-agent-workflow.md` as the detailed workflow and tool
output contract. Use the configured ImpactProof MCP tools for deterministic
analysis and proof; do not reproduce their Git, AST, dependency, or proof work
manually.

- Use `analyze_change` to inspect current changed files, symbols, and analyzer
  relationships. Use only relationships present in its evidence.
- For a regression, call `explain_regression` and base the root cause on its
  proof status, mismatch, changed symbols, regression nodes, call chains, and
  source snippets. State when evidence is incomplete.
- Make only a minimal, targeted source fix supported by the evidence. Never
  edit tests, proof scenario definitions, or unrelated files to hide a failure.
- After `apply_fix` reports `status: applied`, immediately call `verify_fix`
  with the same repository and scenario. Never claim success without its
  authoritative `PASS` result. Report `REGRESSION` or `ERROR` accurately.
- After a successful verification, `undo_fix` is an optional explicit user
  action. Never call it automatically; invoke it only when the developer asks.
- Do not fabricate tool results or perform broad refactors.

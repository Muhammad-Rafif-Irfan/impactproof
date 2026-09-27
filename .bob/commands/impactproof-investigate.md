---
description: Investigate the current change with ImpactProof and fix and verify a supported regression.
---
Investigate the current code change using the configured ImpactProof MCP tools.
Use the active workspace root as `repo_path`. Determine the applicable proof
scenario from the repository's scenario definitions or the current task
context. If there is no unambiguous scenario, ask for the scenario name rather
than guessing.

1. Call `analyze_change` for the current repository and inspect its changed
   files, symbols, and relationships.
2. Call `explain_regression` with the same repository and selected scenario.
   Use ImpactProof's tool results for analysis and proof; do not manually
   recreate Git diff, AST, dependency, or proof execution.
3. Inspect the structured evidence. If the proof status is `PASS`, report that
   the scenario passed. If it is `ERROR`, report that correctness could not be
   established. Do not change source in either case.
4. If the proof status is `REGRESSION`, explain the likely root cause using
   the returned mismatch and available changed-symbol, impact, call-chain, and
   source evidence. Identify the smallest relevant source fix. Inspect the
   complete target file before preparing its complete replacement contents.
5. Apply only that evidence-supported source change with `apply_fix`. Do not
   modify tests, scenario definitions, or unrelated files.
6. If `apply_fix` returns `status: applied`, immediately call `verify_fix` with
   the same repository and scenario. Do not take another action or claim
   success before verification.
7. Report `PASS` as verified, `REGRESSION` as unresolved, and `ERROR` as
   unverified. Never fabricate results or claim a fix succeeded without
   `verify_fix` returning `PASS`.
8. After reporting a verified fix, offer `undo_fix` as an optional action. Do
   not call it unless the developer explicitly asks to undo the Bob fix.

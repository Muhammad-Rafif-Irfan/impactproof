---
name: impactproof
description: >-
  Run the evidence-led ImpactProof investigate, explain, fix, and verify
  workflow.
metadata:
  user-invocable: true
  disable-model-invocation: true
---

Investigate the current change with the configured ImpactProof MCP tools. Use
the active workspace root as `repo_path`. Select the applicable scenario from
the repository's scenario definitions or current task context. If it is
ambiguous, ask for the scenario name instead of guessing.

Follow the same evidence and safety contract as `/impactproof-investigate`:

1. Call `analyze_change` and inspect its changed files, symbols, and
   relationships. Call `explain_regression` with the same repository and
   scenario; use its deterministic proof status, expected/actual values,
   mismatches, regression nodes, and available source/call-chain evidence.
2. Do not recreate Git diff, AST, dependency analysis, or proof execution.
   Do not invent results or dependencies. If the proof is `PASS`, report it;
   if `ERROR`, say correctness could not be established. Do not change source
   unless the proof reports `REGRESSION`.
3. For a regression, explain the likely cause from returned evidence and
   propose the smallest relevant source fix. Inspect the complete target file
   before preparing its replacement contents. Use `apply_fix` only for that
   evidence-supported source file. Never modify tests, scenario definitions,
   Git metadata, or unrelated files.
4. After `apply_fix` returns `status: applied`, immediately call `verify_fix`
   with the same repository and scenario. Report the verification accurately;
   claim a fix succeeded only when `verify_fix` returns `PASS`.
5. Offer `undo_fix` after a verified fix, but call it only when the developer
   explicitly requests Undo.

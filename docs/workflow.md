# ImpactProof workflow

The product flow is **Predict → Prove → Explain → Fix → Verify**. Each stage has a different source of truth: static analyzer results, executed proof values, or Bob reasoning based on those values.

| Stage | Input | Process | Output | Deterministic or agentic? |
| --- | --- | --- | --- | --- |
| Developer change | Git working tree | Developer edits code as usual. | Tracked source diff against the repository baseline. | Developer action |
| `analyze_change` | `repo_path` | Git diff inspection, Python AST parsing, import/dependency and symbol relationship analysis. | Changed files/symbols, direct and indirect impact evidence, relationships. | Deterministic static analysis |
| Impact graph | Analyzer JSON | Graph transformer maps returned files and relationships to nodes/edges. | Predicted blast radius. | Deterministic transformation |
| Proof | Repository path and named JSON scenario | Engine calls the declared Python functions in a subprocess; captures and extracts configured values; compares expected with actual. | `PASS`, `REGRESSION`, or `ERROR`, plus values and mismatches. | Deterministic execution |
| Explain | Validated analysis/proof evidence | Bob calls `explain_regression` and interprets returned evidence, changed symbols, mismatches, and source context. | Root-cause explanation and evidence-based fix proposal. | Bob reasoning; evidence is deterministic |
| Fix | Developer's explicit request and validated regression evidence | Dashboard Bob Fix bridge requests one `apply_fix` call for the regression target; MCP validates and applies targeted source. | Applied-file result and undo identifier, or unavailable/error. | Bob orchestration plus deterministic MCP validation/write |
| Verify | Applied fix and selected scenario | ImpactProof reruns the proof engine. | `PASS`, `REGRESSION`, or `ERROR`. | Deterministic execution |
| Optional Undo | Recorded latest successful fix | MCP validates current target state and reverses only the recorded Bob line changes. | Undone result or safe refusal. | Deterministic targeted operation |

## Detailed stage contract

### 1. Change analysis

**Input:** absolute path to a Git repository root.  
**Process:** `analyze_change` runs the Python analyzer, which uses Git change data and static Python AST/dependency evidence.  
**Output:** changed files/symbols, direct/indirect affected files, and relationships.  
**Purpose:** predict where a change may propagate. This stage does not execute application behavior.

### 2. Proof execution

**Input:** repository path and a named JSON scenario.  
**Process:** the proof engine executes only the scenario's declared calls, captures results, extracts configured values, and compares them with explicit expected values.  
**Output:** structured values, mismatch list, and `PASS`/`REGRESSION`/`ERROR`.  
**Purpose:** establish what happened for those specified inputs and calls.

### 3. Evidence-led explanation

**Input:** structured analyzer and proof evidence.  
**Process:** the Bob analysis task uses the read-only MCP entrypoint and is validated by `bob_analyzer.py`. Bob's explanation is not proof and cannot turn missing evidence into a successful result.  
**Output:** a dashboard-oriented explanation and root-cause field only when the returned result passes validation.  
**Purpose:** help the developer understand the evidence.

### 4. Apply, verify, and undo

The dashboard's Bob Fix action invokes a separate bridge that accepts a named `apply_fix` tool event/result for the intended Python file. Applying a fix does not automatically verify it. A separate user action invokes deterministic verification. `undo_fix` is available only as an explicit optional action and targets the most recent recorded fix; it does not reset or restore the whole repository.

## Trust boundary

- The graph predicts possible impact; it is not a list of runtime failures.
- The proof engine's execution result determines `PASS`, `REGRESSION`, or `ERROR`.
- Bob can explain and orchestrate tool calls; Bob prose is not accepted as proof.
- `PASS` means the selected scenario's expectations matched in that run. It does not guarantee correctness outside the scenario's inputs and execution path.


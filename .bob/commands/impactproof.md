---
description: Open the existing ImpactProof dashboard for the active workspace.
---
Open the existing ImpactProof dashboard for the active Bob workspace. This
command only launches the dashboard; do not call ImpactProof MCP tools, apply
fixes, verify fixes, or undo fixes. The developer will use the dashboard's
existing INVESTIGATE CHANGE and Explain/Fix/Verify/Undo controls.

1. Resolve the active workspace root to an absolute path. Find the applicable
   proof scenario from `proof/scenarios/` or the current task context. If no
   scenario can be selected confidently, ask the developer which scenario to
   use before launching; do not guess.
2. Use Bob's `execute` tool to launch the existing `graph/launch_graph.py`.
   Use the active workspace root both as the launcher repository path and as
   `repo_path`. Pass the selected scenario with the launcher's documented
   `--scenario <name>` option so INVESTIGATE CHANGE is enabled. Use its
   documented `--out <file>` option to write the generated viewer HTML to a
   unique file under the system temporary directory rather than overwriting a
   workspace file. Substitute the resolved, safely quoted workspace and
   scenario values in this command:

   ```sh
   output_file="$(mktemp /tmp/impactproof-dashboard-XXXXXX.html)"
   log_file="${output_file%.html}.log"
   python3 "<workspace-root>/graph/launch_graph.py" "<workspace-root>" --scenario "<scenario-name>" --out "$output_file" >"$log_file" 2>&1 </dev/null &
   printf 'ImpactProof launch log: %s\n' "$log_file"
   ```
3. Let the existing launcher open the local browser with its built-in
   `webbrowser.open(...)` behavior and keep its local server running. Do not
   add another launcher or server. If Bob asks for approval to run the local
   command, leave that approval to the developer.
4. Confirm launch only from the command result or launch log. Do not claim the
   dashboard opened if the command failed. After it opens, let the developer
   continue in the dashboard; do not start Fix, Verify, or Undo from Bob.

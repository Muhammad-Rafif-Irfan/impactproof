---
name: impact
description: Open the existing ImpactProof dashboard for the active workspace.
metadata:
  user-invocable: true
  disable-model-invocation: true
---

Open the existing ImpactProof dashboard for the active Bob workspace. This
command only launches the dashboard. Do not call ImpactProof MCP tools, run
analysis or proof yourself, explain a regression, or apply, verify, or undo a
fix. The developer will use the dashboard controls.

1. Resolve the active workspace root to an absolute path. Inspect its
   `proof/scenarios/` definitions and the current task context to select the
   applicable scenario. If there is no single scenario you can select
   confidently, ask the developer which scenario to use before launching.
2. Use Bob's Execute capability to run the existing launcher, with the active
   workspace root as its repository argument and the selected scenario passed
   via the documented `--scenario` option. Use `--out` to write the generated
   viewer HTML to a unique file in the system temporary directory. For example,
   after safely substituting the absolute workspace and scenario values, run:

   ```sh
   output_file="$(mktemp /tmp/impact-dashboard-XXXXXX.html)" || exit 1
   log_file="${output_file%.html}.log"
   nohup python3 "<absolute-workspace-root>/graph/launch_graph.py" "<absolute-workspace-root>" --scenario "<scenario-name>" --out "$output_file" >"$log_file" 2>&1 </dev/null &
   launcher_pid=$!
   for attempt in $(seq 1 30); do
     if grep -q 'Viewer and investigation bridge running at ' "$log_file"; then
       grep 'Viewer and investigation bridge running at ' "$log_file"
       exit 0
     fi
     if ! kill -0 "$launcher_pid" 2>/dev/null; then
       cat "$log_file"
       exit 1
     fi
     sleep 1
   done
   printf 'Dashboard launch did not confirm readiness. Log: %s\n' "$log_file"
   exit 1
   ```

3. Let `launch_graph.py` open the browser using its existing behavior. Preserve
   Bob's normal Execute approval flow. Confirm the dashboard opened only if the
   Execute result or launcher output indicates a successful launch; otherwise
   report the launch failure without claiming it opened.

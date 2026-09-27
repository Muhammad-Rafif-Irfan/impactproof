---
description: Open the existing ImpactProof dashboard for the active workspace.
---
Open the existing ImpactProof dashboard for the active Bob workspace. This
command only launches the dashboard. Do not call ImpactProof MCP tools, run
analysis or proof yourself, explain a regression, or apply, verify, or undo a
fix. The developer will use the dashboard controls.

1. Resolve the active workspace root to an absolute path. Inspect its
   `proof/scenarios/` definitions and the current task context to select the
   applicable scenario. If there is no single scenario you can select
   confidently, ask the developer which scenario to use before launching.
2. **You MUST call Bob's Execute tool and run the shell block below.** Do not
   merely describe the command, ask the developer to run it, or stop after
   presenting it. Use the installed local launcher, with the active workspace
   root as its repository argument and the selected scenario passed via
   `--scenario`. The launcher reads the user's private
   `~/.config/impactproof/bob.env`, forwards `BOB_API_KEY` only to the dashboard
   process, and creates a unique temporary viewer output path. Never read, print,
   or place the key in the command or its arguments. Preserve Bob's normal Execute
   approval flow. After resolving the absolute workspace and scenario, run:

   ```sh
   log_file="$(mktemp /tmp/impact-dashboard-XXXXXX.log)" || exit 1
   if [ ! -x /home/mri/.local/bin/impactproof-dashboard ]; then
     printf 'ImpactProof local launcher is not installed.\n' >&2
     exit 1
   fi
   nohup /usr/bin/python3 /home/mri/.local/bin/impactproof-dashboard "<absolute-workspace-root>" --scenario "<scenario-name>" >"$log_file" 2>&1 </dev/null &
   launcher_pid=$!
   for attempt in $(seq 1 30); do
     if grep -q 'Viewer and investigation bridge running at ' "$log_file"; then
       grep 'Viewer and investigation bridge running at ' "$log_file"
       exit 0
     fi
     if ! kill -0 "$launcher_pid" 2>/dev/null; then
       printf 'ImpactProof dashboard launcher exited. Log: %s\n' "$log_file" >&2
       exit 1
     fi
     sleep 1
   done
   printf 'Dashboard launch did not confirm readiness. Log: %s\n' "$log_file" >&2
   exit 1
   ```

3. Let `launch_graph.py` open the browser using its existing behavior. Preserve
   Bob's normal Execute approval flow. Confirm the dashboard opened only if the
   Execute result or launcher output indicates a successful launch; otherwise
   report the launch failure without claiming it opened.

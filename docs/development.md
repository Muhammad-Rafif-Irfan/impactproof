# Development guide

This guide documents the commands and structures present in the repository. It does not assume a project-wide dependency manager or aggregate test command.

## Requirements

- Python 3; no minimum version is declared. The project code uses modern type syntax and the current validation environment is Python 3.13.3.
- Node.js and npm for the TypeScript MCP server; `package.json` does not declare an engines range.
- Git for change analysis; the target must be a Git repository with a `HEAD` baseline.

The Python analyzer and proof engine use the standard library. MCP dependencies are in `mcp-server/package.json` and `package-lock.json`.

## Build MCP server

```bash
cd mcp-server
npm ci
npm run build
```

The build compiles the TypeScript MCP entrypoints and copies `analyze.py` and `regression_tools.py` to `build/`. The full MCP starts at `build/index.js`; the analysis-only entrypoint is `build/readonly_index.js`.

## Run deterministic analysis and proof

The analyzer is used by MCP and expects a valid Git target. The proof CLI is:

```bash
python3 proof/run_proof.py /path/to/target-repository premium_checkout_refund
```

To open the interactive dashboard:

```bash
python3 graph/launch_graph.py /path/to/target-repository --scenario premium_checkout_refund
```

The viewer starts idle and performs the investigation only after the user activates its button. The local HTTP bridge binds to loopback. D3 is loaded from a CDN in the viewer.

## Test commands

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s graph
PYTHONDONTWRITEBYTECODE=1 python3 proof/test_proof.py
PYTHONDONTWRITEBYTECODE=1 python3 mcp-server/test_analyze.py
PYTHONDONTWRITEBYTECODE=1 python3 mcp-server/test_regression_tools.py
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s mcp-server -p 'test_readonly_entrypoint.py'
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s mcp-server -p 'test_agent_contract.py'
```

`test_analyze.py`, `test_regression_tools.py`, and `proof/test_proof.py` are direct Python test runners. Graph and selected MCP contract suites use `unittest`. The full graph tests create loopback HTTP servers; a restrictive environment may need local socket permission for those tests.

## Add a proof scenario

Add a JSON file under `proof/scenarios/`. Follow the existing schema: name, ordered steps, function/module identifiers, arguments or captured arguments, capture names, extraction rules, and expected values. Extraction can read a capture directly or traverse nested dictionaries with dot-separated `key` paths. Add deterministic proof tests for the scenario behavior. Do not encode a desired status such as PASS/REGRESSION in the scenario; comparison determines the status.

## Develop MCP tools

The full server is registered in `mcp-server/src/index.ts`; read-only Bob analysis tools are registered separately in `mcp-server/src/readonly_index.ts`. Deterministic Python tool implementations live in `mcp-server/src/analyze.py` and `mcp-server/src/regression_tools.py`. After changing MCP TypeScript or copied Python files, rebuild with `npm run build` and run the relevant MCP tests. Keep mutation registrations out of the read-only entrypoint.

## Bob configuration and credentials

Project Bob configuration is in `.bob/`. Do not add secrets there. For local dashboard Bob Shell use, the provided user-local launcher reads `~/.config/impactproof/bob.env`; create it manually and restrict its permissions. The repository's Bob/MCP/launcher paths are currently machine-specific and need local adaptation before a fresh clone can use Bob without changes.


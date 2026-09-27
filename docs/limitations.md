# Scope and limitations

This document distinguishes current implementation boundaries from possible future work.

## Current scope

- The analyzer processes Python source using AST parsing and Git working-tree changes.
- Dependency and symbol relationships depend on imports and symbol uses the implementation can statically resolve within the repository.
- Graph nodes and edges are built from analyzer output; the graph transformer does not infer additional dependencies.
- The proof engine executes declared Python function calls for declared scenario inputs. It is scenario-based rather than exhaustive.
- Bob IDE integration uses project MCP configuration and custom modes. Dashboard Bob integration uses a local Bob Shell process and Bob authentication.
- The dashboard serves over loopback and uses D3 from jsDelivr.

## Known limitations

1. **Static-analysis coverage:** runtime-generated dependencies, reflection, dynamic imports, and relationships outside supported Python parsing/resolution may not appear in the graph.
2. **Git scope:** the analyzer requires a Git repository with a commit baseline and examines tracked working-tree changes. Untracked source files are not treated as changed files by the Git-diff path.
3. **Scenario scope:** proof only establishes results for calls, values, and code paths declared by a scenario. A `PASS` is not a guarantee for all inputs, integrations, or runtime conditions.
4. **Execution isolation:** proof calls run in a subprocess with a timeout, but application code is not run inside a complete security sandbox. A scenario can invoke repository functions with side effects.
5. **Local Bob setup:** the current Bob Shell executable/runtime paths, MCP registration, and `/impact` launcher commands are specific to the hackathon workstation. `mcp-server/package.json` does not declare a Node engines range, and this repository does not declare a formal Python minimum.
6. **Workspace allowlist:** Bob runner and bridge accept only the configured ImpactProof product and demo workspaces. Using another target requires deliberate local configuration changes.
7. **Bob availability and authentication:** Bob actions require a working local IBM Bob installation and authentication. Bob Shell can fail independently of deterministic analysis/proof. The browser should show an unavailable state rather than treat a failed Bob task as a proof result.
8. **External viewer asset:** the graph uses D3 loaded from jsDelivr; the browser needs access to that resource.
9. **Submission screenshots:** `bob_sessions/` currently contains a README and capture manifest but no actual Bob IDE summary PNGs.

## Safeguards and limits of safeguards

The mutation tool restricts the target to Python source, blocks tests/scenarios and traversal, validates syntax, and records undo state. Undo is targeted and fails when recorded state is missing or no longer matches. These protections reduce accidental scope but do not guarantee that a proposed code change is semantically correct. Deterministic verification still needs to return `PASS` for the selected scenario.

## Possible extensions (not implemented here)

Broader language support, deeper dynamic dependency discovery, richer scenario coverage, portable Bob setup, and additional execution isolation are possible future directions. They are not current capabilities.

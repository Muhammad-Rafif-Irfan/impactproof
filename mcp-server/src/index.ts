#!/usr/bin/env node
import { McpServer } from "@modelcontextprotocol/server";
import { StdioServerTransport } from "@modelcontextprotocol/server/stdio";
import { z } from "zod/v4";
import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import path from "node:path";

const execFileAsync = promisify(execFile);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
// Python scripts live next to this file in build/ (copied there during build)
const ANALYZER_SCRIPT         = path.join(__dirname, "analyze.py");
const REGRESSION_TOOLS_SCRIPT = path.join(__dirname, "regression_tools.py");
const jsonObjectSchema = z.record(z.string(), z.unknown());

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const server = new McpServer({ name: "impactproof-mcp-server", version: "0.1.0" });

server.registerTool(
  "impactproof_status",
  {
    description: "Returns the current status of the ImpactProof Change Impact Simulator.",
    inputSchema: z.object({}),
    outputSchema: jsonObjectSchema,
  },
  async () => {
    const result = {
      status: "ready",
      product: "ImpactProof",
      message: "ImpactProof MCP is connected successfully.",
    };
    return {
      structuredContent: result,
      content: [
        {
          type: "text",
          text: JSON.stringify(result),
        },
      ],
    };
  }
);

server.registerTool(
  "analyze_change",
  {
    description:
      "Analyze the current Git working-tree changes and identify the blast radius: which source files and symbols are directly or indirectly affected. Returns analyzer evidence as structuredContent and JSON text. Use this evidence as the source of dependency relationships; do not infer additional edges.",
    outputSchema: jsonObjectSchema,
    inputSchema: z.object({
      repo_path: z
        .string()
        .describe("Absolute path to the Git repository root to analyze."),
    }),
  },
  async ({ repo_path }) => {
    let stdout: string;
    let stderr: string;
    try {
      ({ stdout, stderr } = await execFileAsync("python3", [ANALYZER_SCRIPT, repo_path], {
        timeout: 30_000,
      }));
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      const parsed = { error: `Analyzer failed: ${msg}` };
      return {
        structuredContent: parsed,
        content: [{ type: "text", text: `Analyzer failed: ${msg}` }],
        isError: true,
      };
    }

    if (stderr) {
      console.error("[analyze_change] analyzer stderr:", stderr);
    }

    let parsed: unknown;
    try {
      parsed = JSON.parse(stdout);
    } catch {
      const parsed = { error: "Analyzer returned non-JSON output." };
      return {
        structuredContent: parsed,
        content: [
          {
            type: "text",
            text: `Analyzer returned non-JSON output:\n${stdout}`,
          },
        ],
        isError: true,
      };
    }

    if (!isJsonObject(parsed)) {
      const error = { error: "Analyzer returned JSON that is not an object." };
      return {
        structuredContent: error,
        content: [{ type: "text", text: error.error }],
        isError: true,
      };
    }

    return {
      structuredContent: parsed,
      content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
    };
  }
);

// ---------------------------------------------------------------------------
// Helper — run a regression_tools.py sub-command and return parsed JSON
// ---------------------------------------------------------------------------
async function runRegressionTool(args: string[]): Promise<{ parsed: Record<string, unknown>; isError: boolean }> {
  let stdout: string;
  let stderr: string;
  try {
    ({ stdout, stderr } = await execFileAsync("python3", [REGRESSION_TOOLS_SCRIPT, ...args], {
      timeout: 60_000,
      maxBuffer: 4 * 1024 * 1024, // 4 MB — apply_fix passes source code
    }));
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    return { parsed: { error: `Tool failed: ${msg}` }, isError: true };
  }
  if (stderr) console.error("[regression_tools]", stderr);
  try {
    const parsed: unknown = JSON.parse(stdout);
    if (!isJsonObject(parsed)) {
      return { parsed: { error: "Tool returned JSON that is not an object." }, isError: true };
    }
    return { parsed, isError: false };
  } catch {
    return { parsed: { error: `Non-JSON output:\n${stdout}` }, isError: true };
  }
}

// ---------------------------------------------------------------------------
// explain_regression — gather deterministic evidence for Bob to reason over
// ---------------------------------------------------------------------------
server.registerTool(
  "explain_regression",
  {
    description:
      "Gather structured deterministic regression evidence from ImpactProof. " +
      "Runs the proof scenario, identifies the regression node, collects " +
      "changed symbols, expected/actual values, mismatches, analyzer call chains, " +
      "and relevant source snippets. Use proof_status and mismatch evidence before " +
      "reasoning; do not claim a cause unsupported by the returned evidence. " +
      "Returns the same object in structuredContent and JSON text.",
    outputSchema: jsonObjectSchema,
    inputSchema: z.object({
      repo_path: z
        .string()
        .describe("Absolute path to the Git repository root to analyze."),
      scenario: z
        .string()
        .describe("Proof scenario name (e.g. premium_checkout_refund)."),
    }),
  },
  async ({ repo_path, scenario }) => {
    const { parsed, isError } = await runRegressionTool(["explain", repo_path, scenario]);
    return {
      structuredContent: parsed,
      content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
      isError,
    };
  }
);

// ---------------------------------------------------------------------------
// apply_fix — write a targeted source fix to the repository
// ---------------------------------------------------------------------------
server.registerTool(
  "apply_fix",
  {
    description:
      "Apply a targeted source-code fix to one Python file in the repository. " +
      "Safety rules: only .py files, no test files, no proof scenario files, " +
      "no path traversal, syntax validation before write, automatic backup. " +
      "Choose a minimal change supported by explain_regression evidence. " +
      "Applying a fix is not verification; follow the active workflow to " +
      "determine when a separate verify_fix action is appropriate. Returns " +
      "structuredContent and JSON text.",
    outputSchema: jsonObjectSchema,
    inputSchema: z.object({
      repo_path: z
        .string()
        .describe("Absolute path to the Git repository root."),
      file_rel: z
        .string()
        .describe("Relative path to the Python file to modify (e.g. refund.py)."),
      new_source: z
        .string()
        .describe("Complete new source content for the file."),
    }),
  },
  async ({ repo_path, file_rel, new_source }) => {
    const { parsed, isError } = await runRegressionTool([
      "apply_fix", repo_path, file_rel, new_source,
    ]);
    return {
      structuredContent: parsed,
      content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
      isError,
    };
  }
);

// ---------------------------------------------------------------------------
// undo_fix — reverse only the last recorded apply_fix line edits
// ---------------------------------------------------------------------------
server.registerTool(
  "undo_fix",
  {
    description:
      "Undo the most recent successful ImpactProof apply_fix in this repository. " +
      "Uses the exact recorded line changes and fails safely if the target has " +
      "diverged or the state is missing/ambiguous. It preserves unrelated edits, " +
      "including other changes in the same file. This is an explicit user action; " +
      "never call it automatically. Returns structuredContent and JSON text.",
    outputSchema: jsonObjectSchema,
    inputSchema: z.object({
      repo_path: z
        .string()
        .describe("Absolute path to the Git repository root containing the Bob fix."),
    }),
  },
  async ({ repo_path }) => {
    const { parsed, isError } = await runRegressionTool(["undo_fix", repo_path]);
    return {
      structuredContent: parsed,
      content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
      isError: isError || parsed.status === "error",
    };
  }
);

// ---------------------------------------------------------------------------
// verify_fix — rerun proof scenario; deterministic pass/fail verdict
// ---------------------------------------------------------------------------
server.registerTool(
  "verify_fix",
  {
    description:
      "Re-run the ImpactProof proof scenario against the repository after a fix " +
      "has been applied. Returns PASS, REGRESSION, or ERROR — determined " +
      "entirely by the deterministic proof engine, never by AI inference. " +
      "This is the authoritative verdict on whether the fix is correct. Never " +
      "claim success without status PASS from this tool; status ERROR means " +
      "correctness could not be established. Returns structuredContent and JSON text.",
    outputSchema: jsonObjectSchema,
    inputSchema: z.object({
      repo_path: z
        .string()
        .describe("Absolute path to the Git repository root."),
      scenario: z
        .string()
        .describe("Proof scenario name (e.g. premium_checkout_refund)."),
    }),
  },
  async ({ repo_path, scenario }) => {
    const { parsed, isError } = await runRegressionTool(["verify", repo_path, scenario]);
    return {
      structuredContent: parsed,
      content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
      isError,
    };
  }
);

async function main() {
  const transport = new StdioServerTransport();
  await server.connect(transport);
  console.error("ImpactProof MCP server running on stdio");
}

main().catch((error) => {
  console.error("Fatal error:", error);
  process.exit(1);
});

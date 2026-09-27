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
const ANALYZER_SCRIPT = path.join(__dirname, "analyze.py");
const REGRESSION_TOOLS_SCRIPT = path.join(__dirname, "regression_tools.py");
const jsonObjectSchema = z.record(z.string(), z.unknown());
function isJsonObject(value) {
    return typeof value === "object" && value !== null && !Array.isArray(value);
}
const server = new McpServer({ name: "impactproof-readonly-mcp-server", version: "0.1.0" });
server.registerTool("analyze_change", {
    description: "Analyze the current Git working-tree changes and identify the blast radius: which source files and symbols are directly or indirectly affected. Returns analyzer evidence as structuredContent and JSON text. Use this evidence as the source of dependency relationships; do not infer additional edges.",
    outputSchema: jsonObjectSchema,
    inputSchema: z.object({
        repo_path: z.string().describe("Absolute path to the Git repository root to analyze."),
    }),
}, async ({ repo_path }) => {
    let stdout;
    let stderr;
    try {
        ({ stdout, stderr } = await execFileAsync("python3", [ANALYZER_SCRIPT, repo_path], {
            timeout: 30_000,
        }));
    }
    catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const error = { error: `Analyzer failed: ${msg}` };
        return {
            structuredContent: error,
            content: [{ type: "text", text: `Analyzer failed: ${msg}` }],
            isError: true,
        };
    }
    if (stderr)
        console.error("[analyze_change] analyzer stderr:", stderr);
    let parsed;
    try {
        parsed = JSON.parse(stdout);
    }
    catch {
        return {
            structuredContent: { error: "Analyzer returned non-JSON output." },
            content: [{ type: "text", text: `Analyzer returned non-JSON output:\n${stdout}` }],
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
});
server.registerTool("explain_regression", {
    description: "Gather structured deterministic regression evidence from ImpactProof. " +
        "Runs the proof scenario, identifies the regression node, collects " +
        "changed symbols, expected/actual values, mismatches, analyzer call chains, " +
        "and relevant source snippets. Use proof_status and mismatch evidence before " +
        "reasoning; do not claim a cause unsupported by the returned evidence. " +
        "Returns the same object in structuredContent and JSON text.",
    outputSchema: jsonObjectSchema,
    inputSchema: z.object({
        repo_path: z.string().describe("Absolute path to the Git repository root to analyze."),
        scenario: z.string().describe("Proof scenario name (e.g. premium_checkout_refund)."),
    }),
}, async ({ repo_path, scenario }) => {
    let stdout;
    let stderr;
    let parsed;
    try {
        ({ stdout, stderr } = await execFileAsync("python3", [REGRESSION_TOOLS_SCRIPT, "explain", repo_path, scenario], { timeout: 60_000, maxBuffer: 4 * 1024 * 1024 }));
    }
    catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        parsed = { error: `Tool failed: ${msg}` };
        return {
            structuredContent: parsed,
            content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
            isError: true,
        };
    }
    if (stderr)
        console.error("[regression_tools]", stderr);
    try {
        const result = JSON.parse(stdout);
        if (!isJsonObject(result)) {
            parsed = { error: "Tool returned JSON that is not an object." };
            return {
                structuredContent: parsed,
                content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
                isError: true,
            };
        }
        parsed = result;
    }
    catch {
        parsed = { error: `Non-JSON output:\n${stdout}` };
        return {
            structuredContent: parsed,
            content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
            isError: true,
        };
    }
    return {
        structuredContent: parsed,
        content: [{ type: "text", text: JSON.stringify(parsed, null, 2) }],
        isError: false,
    };
});
async function main() {
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error("ImpactProof read-only MCP server running on stdio");
}
main().catch((error) => {
    console.error("Fatal error:", error);
    process.exit(1);
});

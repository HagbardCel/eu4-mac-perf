// Illustrative OpenCode /docs generation adapter. Requires a trusted PR-F bridge.
// Not independently runnable until EU4_ORACLE_BRIDGE is installed and tested.
// Tool names: oracle_inspect, oracle_fixtures, oracle_test, oracle_diff,
//             oracle_coverage, oracle_certify.
import { tool } from "@opencode-ai/plugin"

async function call(method: string, data: Record<string, unknown>): Promise<string> {
  const bridge = process.env.EU4_ORACLE_BRIDGE
  if (!bridge || !bridge.startsWith("/")) throw new Error("Trusted oracle bridge path not configured")
  const args = JSON.stringify({ protocol: "eu4-oracle/v2", method, args: data })
  // No shell interpolation: argv only. Bridge must enforce allowlisted methods,
  // strict IDs, bounded output, authorized candidate path, and time/resource limits.
  const proc = Bun.spawn([bridge], { stdin: "pipe", stdout: "pipe", stderr: "pipe" })
  proc.stdin.write(args + "\n")
  proc.stdin.end()
  const [output, errors, exit] = await Promise.all([
    new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited,
  ])
  if (exit !== 0) throw new Error(`oracle bridge failed (${exit}): ${errors.slice(0, 300)}`)
  if (output.length > 64000) throw new Error("Tool response exceeded model-facing limit")
  return output
}

const functionId = tool.schema.string().regex(/^[a-zA-Z0-9:_@.\-]+$/).max(128)
const fixtureId = tool.schema.string().regex(/^[a-zA-Z0-9:_@.\-]+$/).max(128)
export const inspect = tool({
  description: "Inspect verified static function evidence and ABI uncertainties",
  args: { function_id: functionId },
  async execute(a) { return call("inspect", a) },
})
export const fixtures = tool({
  description: "List bounded visible fixtures and capture completeness metadata",
  args: { function_id: functionId, limit: tool.schema.number().int().min(1).max(50).default(10) },
  async execute(a) { return call("fixtures", a) },
})
export const coverage = tool({
  description: "Read exact invocation, capture and deduplicated observation counts",
  args: { function_id: functionId },
  async execute(a) { return call("coverage", a) },
})
export const test = tool({
  description: "Compile candidate in sandbox and run selected non-held-out fixtures",
  args: { function_id: functionId, candidate_path: tool.schema.string().max(256) },
  async execute(a) { return call("test", a) },
})
export const diff = tool({
  description: "Get first observable mismatch from an oracle test run",
  args: { run_id: fixtureId, max_events: tool.schema.number().int().min(1).max(32).default(8) },
  async execute(a) { return call("diff", a) },
})
export const certify = tool({
  description: "Run trusted full and held-out certification gates; returns outcome only",
  args: { function_id: functionId, candidate_path: tool.schema.string().max(256) },
  async execute(a) { return call("certify", a) },
})

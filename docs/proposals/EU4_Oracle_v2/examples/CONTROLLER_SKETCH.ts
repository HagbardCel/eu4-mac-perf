// API-level illustration, not executed in this artifact build. Pin SDK release.
import { createOpencode } from "@opencode-ai/sdk"

export async function runOne(functionId: string): Promise<void> {
  const runtime = await createOpencode({ hostname: "127.0.0.1", port: 4096 })
  const client = runtime.client
  try {
    const created = await client.session.create({ body: { title: `Reconstruct ${functionId}` } })
    // Confirm SDK response wrapper (.data) against pinned release in PR-F.
    const session = (created as any).data ?? created
    const id = session.id
    const result = await client.session.prompt({
      path: { id },
      body: { agent: "eu4-reconstructor", parts: [{ type: "text", text: `Reconstruct ${functionId}. Only use oracle tools and candidate edits. Stop at explicit blocker or certified completion.` }] },
    })
    // Trust service-produced certification receipts, not the LLM response.
    // Controller: enforce per-function max time, steps, attempts and costs;
    // poll status, audit receipts, abort if limits are reached.
    void result
  } finally {
    runtime.server.close()
  }
}

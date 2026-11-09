/**
 * EU4 campaign binding, trusted validation and completion gate for DeepSeek
 * Harness.
 *
 * Orchestration stays native: this plugin never schedules a goal round. It
 * exposes three model-facing tools that call the deterministic Python bridge,
 * and one monotonic guard that denies model-initiated goal completion.
 * Completion happens in exactly one place — inside the finalize tool, after a
 * fresh checker pass, by calling the goal service directly.
 *
 * @module @eu4/dsh-goal
 */

import { spawn } from 'node:child_process'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  completionDenialReason,
  digestObjective,
  goalBindingProblems,
  qualificationRefusal,
} from './decisions.js'

export const name = 'eu4-goal'

/** Services this plugin needs before apply() runs. */
export const inject = ['tools', 'goals', 'agents']

const BRIDGE_TIMEOUT_MS = 15 * 60 * 1000
const BRIDGE_OUTPUT_CAP = 1_000_000
const BINDING_FILE = 'plugin-binding.json'

/** The trusted checkout that owns this plugin: <infra>/dsh/eu4-goal-plugin/lib. */
export function resolveInfra(config = {}) {
  if (config.infra) return path.resolve(config.infra)
  const here = fs.realpathSync(fileURLToPath(import.meta.url))
  return path.resolve(path.dirname(here), '..', '..', '..')
}

/**
 * Run one bridge operation.
 *
 * The interpreter, cwd and PYTHONPATH are explicit (never ambient): a candidate
 * checkout must not be able to shadow `automation.dsh_bridge`, and the bridge
 * must import its own trusted `automation` package. `-P` suppresses the
 * implicit script-directory entry so cwd injection is not possible either.
 *
 * `EU4_DSH_WORKSPACE` is deliberately NOT set here: the plugin cannot observe
 * DSH's effective workspace or sandbox, so the bridge records the assertion as
 * unverified rather than reporting a workspace the plugin merely guessed.
 */
export function bridgeInvocation(infra, python, operation, sidecar, extra = []) {
  return {
    command: python,
    // No -P: it only exists from Python 3.11, and it is not needed because cwd
    // is pinned to the infrastructure root below, so the implicit script
    // directory is already the intended import location. The bridge's own
    // root assertion rejects a candidate that could shadow `automation`.
    args: ['-m', 'automation.dsh_bridge', operation, '--sidecar', sidecar, ...extra],
    options: {
      cwd: infra,
      env: {
        PATH: process.env.PATH ?? '',
        HOME: process.env.HOME ?? '',
        TMPDIR: process.env.TMPDIR ?? '/tmp',
        PYTHONPATH: infra,
        PYTHONNOUSERSITE: '1',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  }
}

/** Run one bridge operation and return its parsed result, never throwing. */
export function runBridge(infra, python, operation, sidecar, extra = []) {
  const { command, args, options } = bridgeInvocation(
    infra, python, operation, sidecar, extra
  )
  return new Promise((resolve) => {
    let settled = false
    const finish = (value) => {
      if (settled) return
      settled = true
      resolve(value)
    }
    let child
    try {
      child = spawn(command, args, options)
    } catch (error) {
      finish({ ok: false, operation, classification: 'infrastructure_error',
               kind: 'spawn', detail: String(error.message || error) })
      return
    }
    let stdout = ''
    let stderr = ''
    const timer = setTimeout(() => {
      child.kill('SIGKILL')
    }, BRIDGE_TIMEOUT_MS)
    child.stdout.on('data', (chunk) => {
      stdout += chunk
      if (stdout.length > BRIDGE_OUTPUT_CAP) child.kill('SIGKILL')
    })
    child.stderr.on('data', (chunk) => {
      stderr += chunk
    })
    child.on('error', (error) => {
      clearTimeout(timer)
      finish({ ok: false, operation, classification: 'infrastructure_error',
               kind: 'spawn', detail: String(error.message || error) })
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      const text = stdout.trim()
      if (!text) {
        finish({ ok: false, operation, classification: 'infrastructure_error',
                 kind: 'no_output',
                 detail: stderr.trim().slice(0, 2000) || `exit ${code}` })
        return
      }
      try {
        finish(JSON.parse(text))
      } catch {
        finish({ ok: false, operation, classification: 'infrastructure_error',
                 kind: 'unparseable', detail: text.slice(0, 2000) })
      }
    })
  })
}

function textContent(value) {
  return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
}

function tool(name, description, execute) {
  return {
    name,
    description,
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => textContent(value),
    },
    execute,
  }
}

/**
 * Mount the plugin on the host plane.
 * @param {object} ctx - cordis context
 * @param {object} config - resolved profile configuration
 */
export function apply(ctx, config = {}) {
  const sidecar = config.sidecar || ''
  const infra = resolveInfra(config)
  const python = config.python || path.join(infra, '.venv', 'bin', 'python3')
  const bindings = new Map()
  let registeredTools = 0
  let registeredGuards = 0

  if (!sidecar) {
    ctx.logger?.warn?.('[eu4-goal] no sidecar configured; campaign tools are unavailable')
  }

  function requireSidecar() {
    if (!sidecar) {
      throw new Error('no EU4 campaign sidecar is configured for this profile')
    }
  }

  function persistBiding(sessionId, agentId, goal, runRoot) {
    if (!runRoot || !goal) return bindings.get(sessionId)
    const value = {
      schema: 'eu4-dsh-plugin-binding/v1',
      session_id: sessionId,
      agent_id: agentId,
      goal_id: goal.id,
      revision: goal.revision,
      objective_digest: digestObjective(goal.objective),
      bound_at: new Date().toISOString(),
    }
    bindings.set(sessionId, value)
    try {
      fs.writeFileSync(path.join(runRoot, BINDING_FILE), JSON.stringify(value, null, 2))
    } catch (error) {
      ctx.logger?.warn?.(`[eu4-goal] could not persist binding: ${error.message}`)
    }
    return value
  }

  function bindingFor(sessionId, runRoot) {
    if (bindings.has(sessionId)) return bindings.get(sessionId)
    if (!runRoot) return undefined
    try {
      const file = path.join(runRoot, BINDING_FILE)
      if (!fs.existsSync(file)) return undefined
      const stored = JSON.parse(fs.readFileSync(file, 'utf8'))
      bindings.set(sessionId, stored)
      return stored
    } catch {
      // An unreadable binding is no binding: the caller must rebind.
      return undefined
    }
  }

  // --- completion gate -----------------------------------------------------
  // On the plain context, so it applies globally and is evaluated before any
  // agent-scoped guard layer, and with no I/O so it cannot stall the loop.
  ctx.effect(() => {
    const dispose = ctx.tools.guard((exec) => completionDenialReason(exec))
    registeredGuards += 1
    return dispose
  })

  // --- model-facing tools --------------------------------------------------
  const register = (definition) =>
    ctx.effect(() => {
      const dispose = ctx.tools.register(definition)
      registeredTools += 1
      return dispose
    })

  register(
    tool(
      'eu4_campaign_status',
      'Report the bound EU4 campaign: identities, validation counters, the ' +
        'current receipt, and whether it is stale relative to the candidate.',
      async (_args, exec) => {
        requireSidecar()
        const sessionId = exec?.agent?.sessionId ?? 'unknown'
        const goal = exec?.agent ? ctx.goals.get(exec.agent) : undefined
        const result = await runBridge(infra, python, 'status', sidecar)
        if (result && result.run_root) {
          persistBiding(sessionId, exec?.agent?.id, goal, result.run_root)
        }
        return result
      }
    )
  )

  register(
    tool(
      'eu4_validate_candidate',
      'Run the campaign trusted checker against the current candidate and ' +
        'return its verdict and diagnostics. A pass is necessary but not ' +
        'sufficient: completion also requires eu4_finalize_candidate.',
      async (_args, exec) => {
        requireSidecar()
        const sessionId = exec?.agent?.sessionId ?? 'unknown'
        const goal = exec?.agent ? ctx.goals.get(exec.agent) : undefined
        const result = await runBridge(infra, python, 'validate', sidecar, [
          '--session-id',
          sessionId,
        ])
        if (result && result.run_root) {
          persistBiding(sessionId, exec?.agent?.id, goal, result.run_root)
        }
        return result
      }
    )
  )

  register(
    tool(
      'eu4_finalize_candidate',
      'Confirm the candidate with a fresh trusted checker run and, only if ' +
        'that run passes, complete the campaign goal. This is the sole way ' +
        'an EU4 campaign goal can be completed.',
      async (_args, exec) => {
        requireSidecar()
        const agent = exec?.agent
        if (!agent) throw new Error('finalization requires an owning agent')
        const sessionId = agent.sessionId
        const live = ctx.goals.get(agent)
        const status = await runBridge(infra, python, 'status', sidecar)
        const runRoot = status && status.run_root
        if (live) {
          persistBiding(sessionId, agent.id, live, runRoot)
        }
        const problems = goalBindingProblems(
          live ? bindingFor(sessionId, runRoot) : null,
          live
        )
        if (problems.length) {
          return {
            ok: false,
            operation: 'finalize',
            classification: 'refused',
            kind: 'goal_binding',
            completion: 'refused',
            detail: problems.join('; '),
          }
        }
        const result = await runBridge(infra, python, 'finalize', sidecar, [
          '--session-id',
          sessionId,
        ])
        const refusal = qualificationRefusal(result)
        if (refusal) {
          return { ...result, completion: 'refused', reason: refusal }
        }
        ctx.goals.complete(agent, { id: live.id, revision: live.revision })
        return { ...result, completion: 'completed', goal_id: live.id }
      }
    )
  )

  // Opt-in proof that the plugin mounted AND registered. The web app does not
  // surface plugin INFO logs, so composition-level checks cannot tell "row
  // present" from "guards and tools installed"; with EU4_DSH_BOOT_PROOF set the
  // registration counts are written here, after every ctx.effect has run.
  const bootProof = process.env.EU4_DSH_BOOT_PROOF
  if (bootProof) {
    try {
      const record = {
        schema: 'eu4-dsh-boot-proof/v1',
        mounted: true,
        registered_tools: registeredTools,
        registered_guards: registeredGuards,
        plugin: name,
        sidecar,
        infra,
        python,
        pid: process.pid,
        mounted_at: new Date().toISOString(),
      }
      fs.mkdirSync(path.dirname(bootProof), { recursive: true })
      fs.writeFileSync(bootProof, JSON.stringify(record, null, 2))
    } catch (error) {
      ctx.logger?.warn?.(`[eu4-goal] could not write boot proof: ${error.message}`)
    }
  }
}

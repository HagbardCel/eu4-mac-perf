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
  workspaceRefusal,
} from './decisions.js'

export const name = 'eu4-goal'

/** Services this plugin needs before apply() runs. */
export const inject = ['tools', 'goals', 'agents']

const BRIDGE_TIMEOUT_MS = 15 * 60 * 1000

/**
 * Finalization runs the checker twice -- the live candidate and its frozen
 * replay -- so one timeout for every operation would fail a valid candidate.
 */
export function bridgeTimeoutMs(operation, checkerSeconds) {
  if (operation !== 'finalize') return BRIDGE_TIMEOUT_MS
  const budget = Number.isFinite(checkerSeconds) && checkerSeconds > 0 ? checkerSeconds : 900
  return Math.ceil((budget * 2 + 120) * 1000)
}
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
/** Env vars the bridge and the reviewer it spawns are allowed to see. */
const BRIDGE_ENV_ALLOWED = [
  'PATH',
  'HOME',
  'TMPDIR',
  'LANG',
  'LC_ALL',
  'DSH_HOME',
  'EU4_DSH_HOME',
  'OPENCODE_GO_API_KEY',
  'DEEPSEEK_API_KEY',
]

export function bridgeEnvironment(infra, base = process.env) {
  const env = { PYTHONPATH: infra, PYTHONNOUSERSITE: '1' }
  for (const key of BRIDGE_ENV_ALLOWED) {
    const value = base[key]
    if (typeof value === 'string' && value) env[key] = value
  }
  if (!env.TMPDIR) env.TMPDIR = '/tmp'
  return env
}

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
      // An explicit allowlist, not process.env: the bridge is trusted host code
      // and needs the campaign's DSH home plus a provider credential so the
      // reviewer it spawns authenticates against the same installation. Anything
      // else stays out of the child.
      env: bridgeEnvironment(infra),
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  }
}

/** Run one bridge operation and return its parsed result, never throwing. */
export function runBridge(infra, python, operation, sidecar, extra = [], timeoutMs) {
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
    let timedOut = false
    const timer = setTimeout(() => {
      // Killed by us, not by the operation: say so rather than reporting the
      // absence of output as if the checker had produced none.
      timedOut = true
      child.kill('SIGKILL')
    }, timeoutMs || BRIDGE_TIMEOUT_MS)
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
      if (timedOut) {
        finish({ ok: false, operation, classification: 'infrastructure_error',
                 kind: 'timeout', goal_policy: 'pause',
                 detail: `the bridge operation exceeded its ${Math.round((timeoutMs || BRIDGE_TIMEOUT_MS) / 1000)}s budget` })
        return
      }
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
  const bridge = config.bridge || runBridge
  const bindings = new Map()
  let registeredTools = 0
  let registeredGuards = 0

  if (!sidecar) {
    ctx.logger?.warn?.('[eu4-goal] no sidecar configured; campaign tools are unavailable')
  }

  /**
   * The session identity, from the harness object rather than argv.
   *
   * `agent.sessionId` is absent on the runtime agent in headless runs, where the
   * session id lives on the session itself; passing the missing value through
   * argv produced the literal string "undefined" in a finalization record. The
   * chain is ordered so a real session id always wins over the agent id.
   */
  function sessionIdentity(agent) {
    const candidates = [agent && agent.session && agent.session.id, agent && agent.sessionId, agent && agent.id]
    for (const value of candidates) {
      if (typeof value === 'string' && value) return value
    }
    return ''
  }

  /** The harness's validated session working directory; never an env var. */
  function sessionCwd(agent) {
    const header = agent && agent.session && agent.session.header
    return header && typeof header.cwd === 'string' ? header.cwd : undefined
  }

  function workspaceProblem(agent, campaignWorkspace) {
    return workspaceRefusal(sessionCwd(agent), campaignWorkspace, path.sep)
  }

  const agentOf = (exec) => exec && exec.agent

  function requireSidecar() {
    if (!sidecar) {
      throw new Error('no EU4 campaign sidecar is configured for this profile')
    }
  }

  /**
   * Turn a terminal checker verdict into a native goal transition.
   *
   * `unavailable` means the infrastructure could not answer and `blocked` means
   * the checker says the work cannot proceed; both end the campaign. `fail` and
   * `inconclusive` stay active so the round driver keeps going.
   */
  function applyGoalPolicy(ctx, agent, result) {
    if (!agent || !result || typeof result !== 'object') return result
    const policy = result.goal_policy
    // automation.dsh_bridge.GOAL_POLICY is the single source: a blocked verdict
    // maps to "block", an unavailable one to "pause". "qualified", "remediate"
    // and "investigate" leave the goal active for the round driver.
    if (policy !== 'block' && policy !== 'pause') return result
    const live = ctx.goals.get(agent)
    if (!live) return result
    const ref = { id: live.id, revision: live.revision }
    const detail = String(result.detail || result.kind || policy).slice(0, 400)
    try {
      if (policy === 'block') {
        ctx.goals.block(agent, ref, { code: 'checker-blocked', message: detail })
      } else {
        ctx.goals.pause(agent, ref)
      }
    } catch (error) {
      // Never claim a policy was applied when it was not: the goal is still
      // active, so report the failed transition instead of the bare verdict.
      return {
        ...result,
        ok: false,
        classification: 'infrastructure_error',
        kind: 'goal_transition_failed',
        goal_policy_applied: false,
        detail:
          `the ${policy} policy could not be applied to the goal: ` +
          `${error && error.message ? error.message : error}`,
      }
    }
    return { ...result, goal_policy_applied: true }
  }

  function persistBiding(sessionId, agentId, goal, runRoot) {
    // Persistence is best-effort; the in-memory binding is what the current
    // finalization compares against. Dropping it because the run root could not
    // be resolved turned a live goal into "no binding recorded".
    if (!goal) return bindings.get(sessionId)
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
    if (!runRoot) return value
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
      // Every tool result goes through the goal policy exactly once, including
      // early returns such as a failed prepare or a workspace mismatch: those are
      // terminal conditions too, and silently leaving the goal active would let a
      // campaign continue on a bridge that cannot serve it.
      const wrapped = {
        ...definition,
        execute: async (args, exec) =>
          applyGoalPolicy(ctx, exec && exec.agent, await definition.execute(args, exec)),
      }
      const dispose = ctx.tools.register(wrapped)
      registeredTools += 1
      return dispose
    })

  register(
    tool(
      'eu4_validate_candidate',
      'Run the campaign trusted checker against the current candidate and ' +
        'return its verdict and diagnostics. A pass is necessary but not ' +
        'sufficient: completion also requires eu4_finalize_candidate.',
      async (_args, exec) => {
        requireSidecar()
        const sessionId = sessionIdentity(exec?.agent) || 'unknown'
        const goal = exec?.agent ? ctx.goals.get(exec.agent) : undefined
        // `validate` refuses an unprepared campaign and the model has no tool
        // to prepare one, so a fresh goal would stall here. `prepare` is
        // idempotent and anchors only trusted material, never the candidate.
        const prepared = await bridge(infra, python, 'prepare', sidecar)
        if (prepared && prepared.ok === false) return prepared
        const mismatch = workspaceProblem(agentOf(exec), prepared && prepared.workspace)
        if (mismatch) {
          return {
            ok: false,
            operation: 'validate',
            classification: 'refused',
            kind: 'workspace_mismatch',
            goal_policy: 'pause',
            detail: mismatch,
          }
        }
        const runRoot = (prepared && prepared.run_root) || undefined
        const bound = bindingFor(sessionId, runRoot)
        // No binding yet is the normal first validate; only an existing binding
        // can conflict with the live goal.
        const problems = bound ? goalBindingProblems(bound, goal) : []
        if (problems.length) {
          // The binding is established once and then compared, never replaced:
          // otherwise editing the goal and validating again would inherit the
          // earlier qualification.
          return {
            ok: false,
            operation: 'validate',
            classification: 'refused',
            kind: 'goal_binding',
            goal_policy: 'pause',
            detail: problems.join('; '),
          }
        }
        const validateArgs = sessionId === 'unknown' ? [] : ['--session-id', sessionId]
        const result = await bridge(
          infra, python, 'validate', sidecar, validateArgs,
          bridgeTimeoutMs('validate', prepared && prepared.checker_timeout_seconds)
        )
        if (!bound) {
          persistBiding(sessionId, exec?.agent?.id, goal, runRoot || (result && result.run_root))
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
        const sessionId = sessionIdentity(agent)
        const live = ctx.goals.get(agent)

        // `prepare` is idempotent and reports both the workspace and the run
        // root, so it replaces a separate status call.
        const prepared = await bridge(infra, python, 'prepare', sidecar)
        if (prepared && prepared.ok === false) return prepared
        const runRoot = prepared && prepared.run_root
        // Never rebind here: a binding established by validation is compared,
        // not replaced. Rebinding would let an edited objective inherit the
        // earlier qualification, which is exactly what must not happen.
        const binding = bindingFor(sessionId, runRoot)
        const problems = goalBindingProblems(binding, live)
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
        const mismatch = workspaceProblem(agentOf(exec), prepared && prepared.workspace)
        if (mismatch) {
          return {
            ok: false,
            operation: 'finalize',
            classification: 'refused',
            kind: 'workspace_mismatch',
            completion: 'refused',
            detail: mismatch,
          }
        }
        const finalizeArgs = sessionId ? ['--session-id', sessionId] : []
        const result = await bridge(
          infra, python, 'finalize', sidecar, finalizeArgs,
          // Two checker runs, so the budget has to cover both.
          bridgeTimeoutMs('finalize', prepared && prepared.checker_timeout_seconds)
        )
        const refusal = qualificationRefusal(result)
        if (refusal) {
          return { ...result, completion: 'refused', reason: refusal }
        }
        ctx.goals.complete(agent, { id: live.id, revision: live.revision })
        return { ...result, completion: 'completed', goal_id: live.id }
      }
    )
  )

  if (config.requireReview) {
    register(
      tool(
        'eu4_review_candidate',
        'Request an independent review of the frozen candidate. Returns the ' +
          'reviewer outcome and findings; a rejection means the candidate needs ' +
          'repair before finalization can succeed.',
        async (_args, exec) => {
          requireSidecar()
          const agent = exec?.agent
          const prepared = await bridge(infra, python, 'prepare', sidecar)
          if (prepared && prepared.ok === false) return prepared
          const mismatch = workspaceProblem(agent, prepared && prepared.workspace)
          if (mismatch) {
            return {
              ok: false,
              operation: 'review',
              classification: 'refused',
              kind: 'workspace_mismatch',
              detail: mismatch,
            }
          }
          // The reviewer runs as its own headless session; the bridge freezes
          // the candidate, validates the report and writes the receipt.
          return await bridge(infra, python, 'review-run', sidecar)
        }
      )
    )
  }

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

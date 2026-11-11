import assert from 'node:assert/strict'
import test from 'node:test'

import { apply, resolveInfra } from '../lib/index.js'

const INFRA = resolveInfra({})

/** A context whose tools are captured, with a scripted bridge. */
function harness(results) {
  const ops = []
  const state = { tools: [], guards: [], completed: [], paused: [], blocked: [] }
  const ctx = {
    logger: { warn() {}, info() {} },
    effect: (fn) => fn(),
    tools: {
      register(definition) {
        state.tools.push(definition)
        return () => {}
      },
      guard(fn) {
        state.guards.push(fn)
        return () => {}
      },
    },
    goals: {
      get: () => state.liveGoal ?? { id: 'g1', revision: 1, objective: 'objective' },
      complete: (agent, ref) => state.completed.push({ agent, ref }),
      pause: (agent, ref) => state.paused.push({ agent, ref }),
      block: (agent, ref, reason) => state.blocked.push({ agent, ref, reason }),
    },
    agents: {},
    _state: state,
  }
  const calls = []
  const bridge = async (_infra, _python, operation, _sidecar, extra) => {
    ops.push(operation)
    if (extra && extra.length) calls.push([operation, ...extra])
    const value = results[operation]
    return typeof value === 'function' ? value() : value
  }
  apply(ctx, { sidecar: '/tmp/campaign.json', infra: INFRA, bridge })
  return { ctx, ops, calls }
}

function tool(ctx, name) {
  return ctx._state.tools.find((entry) => entry.name === name)
}

const agent = { sessionId: 's1', id: 'a1', session: { header: { cwd: '/tmp/ws' } } }

test('validate prepares the campaign first', async () => {
  const { ctx, ops } = harness({
    prepare: { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run' },
    validate: { ok: true, classification: 'verdict', checker_verdict: 'fail' },
  })
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.deepEqual(ops, ['prepare', 'validate'])
  assert.equal(result.checker_verdict, 'fail')
})

test('a failed prepare does not proceed to validate', async () => {
  const { ctx, ops } = harness({
    prepare: { ok: false, kind: 'sidecar', detail: 'bad sidecar' },
    validate: { ok: true, checker_verdict: 'pass' },
  })
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.deepEqual(ops, ['prepare'])
  assert.equal(result.ok, false)
  assert.equal(result.kind, 'sidecar')
})

test('finalize prepares first and completes only on a fresh pass', async () => {
  const { ctx, ops } = harness({
    prepare: { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run' },
    finalize: {
      ok: true,
      operation: 'finalize',
      classification: 'verdict',
      checker_verdict: 'pass',
    },
  })
  // Finalization requires a prior validation: that is what establishes the
  // binding and the trusted receipt it confirms.
  await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  const result = await tool(ctx, 'eu4_finalize_candidate').execute({}, { agent })
  assert.deepEqual(ops, ['prepare', 'validate', 'prepare', 'finalize'])
  assert.equal(result.completion, 'completed')
  assert.equal(ctx._state.completed.length, 1)
})

test('a finalize that is not a fresh pass does not complete the goal', async () => {
  const { ctx } = harness({
    prepare: { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run' },
    finalize: {
      ok: false,
      operation: 'finalize',
      classification: 'integrity_refusal',
      kind: 'fresh_validation_failed',
    },
  })
  const result = await tool(ctx, 'eu4_finalize_candidate').execute({}, { agent })
  assert.equal(result.completion, 'refused')
  assert.equal(ctx._state.completed.length, 0)
})

test('a session rooted outside the campaign workspace is refused', async () => {
  const { ctx, ops } = harness({
    prepare: { ok: true, workspace: '/elsewhere/ws' },
    validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
  })
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.deepEqual(ops, ['prepare'])
  assert.equal(result.kind, 'workspace_mismatch')
  assert.match(result.detail, /is not the campaign workspace/)
})

test('an unavailable session cwd is refused rather than assumed', async () => {
  const { ctx } = harness({
    prepare: { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run' },
    validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
  })
  const result = await tool(ctx, 'eu4_validate_candidate').execute(
    {}, { agent: { sessionId: 's2', id: 'a2' } }
  )
  assert.equal(result.kind, 'workspace_mismatch')
  assert.match(result.detail, /session working directory is unavailable/)
})

test('the session identity comes from the harness object, not argv', async () => {
  const { ctx, calls } = harness({
    prepare: { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run' },
    validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
  })
  // `agent.sessionId` is absent in headless; the id lives on the session.
  const headlessAgent = { id: 'agent-1', session: { id: 'sess-1', header: { cwd: '/tmp/ws' } } }
  await tool(ctx, 'eu4_validate_candidate').execute({}, { agent: headlessAgent })
  assert.deepEqual(calls, [['validate', '--session-id', 'sess-1']])
})

test('an agent with no session identity does not send the flag at all', async () => {
  const { ctx, calls } = harness({
    prepare: { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run' },
    validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
  })
  await tool(ctx, 'eu4_validate_candidate').execute(
    {}, { agent: { session: { header: { cwd: '/tmp/ws' } } } }
  )
  assert.deepEqual(calls, [], 'no --session-id with a missing identity')
})

test('an objective edited after binding cannot reuse the qualification', async () => {
  const { ctx, calls } = harness({
    prepare: { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run' },
    validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
    finalize: { ok: true, operation: 'finalize', classification: 'verdict',
                checker_verdict: 'pass' },
  })
  await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  // The human edits the goal after the campaign was bound.
  ctx.goals.get = () => ({ id: 'g1', revision: 2, objective: 'a different objective' })
  const result = await tool(ctx, 'eu4_finalize_candidate').execute({}, { agent })
  assert.equal(result.kind, 'goal_binding')
  assert.match(result.detail, /objective changed/)
  assert.equal(ctx._state.completed.length, 0)
  assert.ok(!calls.some((call) => call[0] === 'finalize'),
    'finalize must not reach the bridge once the goal changed')
})

const PREPARED = { ok: true, workspace: '/tmp/ws', run_root: '/tmp/run', objective: 'objective' }

test('an unavailable verdict pauses the goal instead of leaving it active', async () => {
  const { ctx } = harness({
    prepare: PREPARED,
    // Exactly what the bridge returns for an unavailable verdict.
    validate: { ok: false, kind: 'unavailable', goal_policy: 'pause', detail: 'checker broke' },
  })
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(result.kind, 'unavailable')
  assert.equal(ctx._state.paused.length, 1)
  assert.deepEqual(ctx._state.paused[0].ref, { id: 'g1', revision: 1 })
  assert.equal(ctx._state.blocked.length, 0)
})

test('a blocked verdict blocks the goal with the checker reason', async () => {
  const { ctx } = harness({
    prepare: PREPARED,
    validate: { ok: false, kind: 'blocked', goal_policy: 'block', detail: 'pin mismatch' },
  })
  await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(ctx._state.blocked.length, 1)
  assert.equal(ctx._state.blocked[0].reason.code, 'checker-blocked')
  assert.match(ctx._state.blocked[0].reason.message, /pin mismatch/)
  assert.equal(ctx._state.paused.length, 0)
})

test('a remediable verdict leaves the goal active for the next round', async () => {
  const { ctx } = harness({
    prepare: PREPARED,
    validate: { ok: false, kind: 'fail', goal_policy: 'remediate', detail: 'not ready' },
  })
  await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(ctx._state.paused.length, 0)
  assert.equal(ctx._state.blocked.length, 0)
})

test('an objective edited after binding cannot be revalidated into a new binding', async () => {
  const { ctx } = harness({ prepare: PREPARED, validate: { ok: true, checker_verdict: 'pass' } })
  await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })

  ctx._state.liveGoal = { id: 'g1', revision: 2, objective: 'a different objective' }

  const second = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(second.kind, 'goal_binding')
  assert.match(second.detail, /objective changed/)

  const finalize = await tool(ctx, 'eu4_finalize_candidate').execute({}, { agent })
  assert.equal(finalize.kind, 'goal_binding')
  assert.equal(ctx._state.completed.length, 0)
})

test('a rejected goal transition is reported, not swallowed', async () => {
  const { ctx } = harness({
    prepare: PREPARED,
    validate: { ok: false, kind: 'unavailable', goal_policy: 'pause', detail: 'checker broke' },
  })
  ctx.goals.pause = () => {
    throw new Error('goal service refused the transition')
  }
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(result.kind, 'goal_transition_failed')
  assert.equal(result.ok, false)
  assert.equal(result.goal_policy_applied, false)
  assert.match(result.detail, /refused the transition/)
})

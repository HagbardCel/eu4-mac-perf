import assert from 'node:assert/strict'
import test from 'node:test'

import { apply, resolveInfra } from '../lib/index.js'

const INFRA = resolveInfra({})

/** A context whose tools are captured, with a scripted bridge. */
function harness(results) {
  const ops = []
  const state = { tools: [], guards: [], completed: [] }
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
      get: () => ({ id: 'g1', revision: 1, objective: 'objective' }),
      complete: (agent, ref) => state.completed.push({ agent, ref }),
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

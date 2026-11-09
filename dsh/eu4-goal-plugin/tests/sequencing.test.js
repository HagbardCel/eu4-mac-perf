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
  const bridge = async (_infra, _python, operation) => {
    ops.push(operation)
    const value = results[operation]
    return typeof value === 'function' ? value() : value
  }
  apply(ctx, { sidecar: '/tmp/campaign.json', infra: INFRA, bridge })
  return { ctx, ops }
}

function tool(ctx, name) {
  return ctx._state.tools.find((entry) => entry.name === name)
}

const agent = { sessionId: 's1', id: 'a1' }

test('validate prepares the campaign first', async () => {
  const { ctx, ops } = harness({
    prepare: { ok: true },
    status: { ok: true, run_root: '/tmp/run', workspace: '/tmp/ws' },
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
    prepare: { ok: true },
    status: { ok: true, run_root: '/tmp/run', workspace: '/tmp/ws' },
    finalize: {
      ok: true,
      operation: 'finalize',
      classification: 'verdict',
      checker_verdict: 'pass',
    },
  })
  const result = await tool(ctx, 'eu4_finalize_candidate').execute({}, { agent })
  assert.deepEqual(ops, ['status', 'prepare', 'finalize'])
  assert.equal(result.completion, 'completed')
  assert.equal(ctx._state.completed.length, 1)
})

test('a finalize that is not a fresh pass does not complete the goal', async () => {
  const { ctx } = harness({
    prepare: { ok: true },
    status: { ok: true, run_root: '/tmp/run', workspace: '/tmp/ws' },
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

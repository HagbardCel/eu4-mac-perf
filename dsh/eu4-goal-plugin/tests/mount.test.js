import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { apply, resolveInfra } from '../lib/index.js'
import { digestObjective } from '../lib/decisions.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const INFRA = path.resolve(HERE, '..', '..', '..')

/** A minimal stand-in for the harness context: enough to prove registration. */
function fakeContext() {
  const state = { tools: [], guards: [], completed: [] }
  const ctx = {
    logger: { warn() {}, info() {} },
    effect(fn) {
      return fn()
    },
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
      get() {
        return undefined
      },
      complete(agent, ref) {
        state.completed.push({ agent, ref })
      },
    },
    agents: {},
    _state: state,
  }
  return ctx
}

test('resolveInfra finds the checkout that owns the plugin', () => {
  assert.equal(resolveInfra({}), INFRA)
})

test('apply registers the guard and the three campaign tools', () => {
  const ctx = fakeContext()
  apply(ctx, { sidecar: '/tmp/does-not-matter.json', infra: INFRA, python: '/bin/false' })
  const names = ctx._state.tools.map((tool) => tool.name).sort()
  assert.deepEqual(names, [
    'eu4_campaign_status',
    'eu4_finalize_candidate',
    'eu4_validate_candidate',
  ])
  assert.equal(ctx._state.guards.length, 1)
})

test('the mounted guard denies premature completion and nothing else', () => {
  const ctx = fakeContext()
  apply(ctx, { sidecar: '/tmp/x.json', infra: INFRA, python: '/bin/false' })
  const guard = ctx._state.guards[0]
  assert.ok(guard({ name: 'update_goal', arguments: { action: 'complete' } }))
  assert.equal(guard({ name: 'update_goal', arguments: { action: 'pause' } }), undefined)
  assert.equal(guard({ name: 'eu4_validate_candidate', arguments: {} }), undefined)
})

test('tools declare a JSON-schema parameter object and a renderer', () => {
  const ctx = fakeContext()
  apply(ctx, { sidecar: '/tmp/x.json', infra: INFRA, python: '/bin/false' })
  for (const tool of ctx._state.tools) {
    assert.equal(tool.parameters.type, 'object')
    assert.equal(typeof tool.execute, 'function')
    const blocks = tool.output.render({}, { ok: true })
    assert.equal(blocks[0].type, 'text')
    assert.match(blocks[0].text, /"ok": true/)
  }
})

test('finalize refuses when the goal objective was edited', async () => {
  const ctx = fakeContext()
  apply(ctx, { sidecar: '/tmp/x.json', infra: INFRA, python: '/bin/false' })
  const finalize = ctx._state.tools.find((t) => t.name === 'eu4_finalize_candidate')
  // A live goal with no prior binding must not be completable: the binding
  // check runs before any bridge call, so no checker is involved here.
  ctx.goals.get = () => ({ id: 'g-new', revision: 2, objective: 'something else' })
  const agent = { sessionId: 's1', id: 'a1' }
  const result = await finalize.execute({}, { agent })
  assert.equal(result.completion, 'refused')
  assert.equal(result.kind, 'goal_binding')
  assert.equal(ctx._state.completed.length, 0)
})

import assert from 'node:assert/strict'
import path from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { apply, bridgeTimeoutMs, resolveInfra } from '../lib/index.js'
import { digestObjective } from '../lib/decisions.js'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const INFRA = path.resolve(HERE, '..', '..', '..')

/** A minimal stand-in for the harness context: enough to prove registration. */
function fakeContext() {
  const state = { tools: [], guards: [], completed: [], effects: [], handlers: {} }
  const ctx = {
    logger: { warn() {}, info() {} },
    effect(fn) {
      const dispose = fn()
      state.effects.push(dispose)
      return dispose
    },
    on(name, fn) {
      ;(state.handlers[name] = state.handlers[name] || []).push(fn)
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

test('apply registers the guard and the two campaign tools', () => {
  const ctx = fakeContext()
  apply(ctx, { sidecar: '/tmp/does-not-matter.json', infra: INFRA, python: '/bin/false' })
  const names = ctx._state.tools.map((tool) => tool.name).sort()
  assert.deepEqual(names, ['eu4_finalize_candidate', 'eu4_validate_candidate'])
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

test('the review tool is registered only when the campaign requires review', () => {
  const without = fakeContext()
  apply(without, { sidecar: '/tmp/x.json', infra: INFRA, python: '/bin/false' })
  assert.ok(!without._state.tools.some((t) => t.name === 'eu4_review_candidate'))

  const withReview = fakeContext()
  apply(withReview, {
    sidecar: '/tmp/x.json', infra: INFRA, python: '/bin/false', requireReview: true,
  })
  const names = withReview._state.tools.map((t) => t.name).sort()
  assert.deepEqual(names, [
    'eu4_finalize_candidate',
    'eu4_review_candidate',
    'eu4_validate_candidate',
  ])
})

test('finalization gets twice the checker budget, other operations do not', () => {
  const single = bridgeTimeoutMs('validate', 900)
  const both = bridgeTimeoutMs('finalize', 900)
  assert.equal(single, 15 * 60 * 1000)
  assert.ok(both > 2 * 900 * 1000, `finalize budget ${both} must cover two runs`)
  assert.ok(both < 40 * 60 * 1000, 'and stay finite')
})

test('plugin unload disposes prompt fibers installed on live agents', async () => {
  const ctx = fakeContext()
  const fiber = { disposed: 0, dispose() { this.disposed += 1 } }
  const agent = {
    ctx: {
      inject(_names, register) {
        register({ systemPrompt: { section() {} } })
        return fiber
      },
    },
    session: { id: 's1', header: { cwd: '/tmp/ws' } },
  }
  apply(ctx, {
    sidecar: '/tmp/x.json',
    infra: INFRA,
    python: '/bin/false',
    bridge: async () => ({
      ok: true,
      status: 'authorized',
      startup_text: 'workspace: /tmp/ws',
      startup: { workspace: '/tmp/ws' },
    }),
  })
  const created = ctx._state.handlers['agent/created'][0]
  await created({ agent })
  assert.equal(fiber.disposed, 0)
  for (const dispose of ctx._state.effects.slice().reverse()) {
    if (typeof dispose === 'function') dispose()
  }
  assert.equal(fiber.disposed, 1)
})

test('plugin unload during an in-flight context install leaves no prompt section', async () => {
  const ctx = fakeContext()
  const fiber = { disposed: 0, dispose() { this.disposed += 1 } }
  let injects = 0
  const agent = {
    ctx: {
      inject(_names, register) {
        injects += 1
        register({ systemPrompt: { section() {} } })
        return fiber
      },
    },
    session: { id: 's2', header: { cwd: '/tmp/ws' } },
  }
  let release
  const gate = new Promise((resolve) => { release = resolve })
  apply(ctx, {
    sidecar: '/tmp/x.json',
    infra: INFRA,
    python: '/bin/false',
    bridge: async () => {
      await gate
      return {
        ok: true,
        status: 'authorized',
        startup_text: 'workspace: /tmp/ws',
        startup: { workspace: '/tmp/ws' },
      }
    },
  })
  const created = ctx._state.handlers['agent/created'][0]
  const pending = created({ agent })
  // Unload while the bridge answer is still in flight.
  for (const dispose of ctx._state.effects.slice().reverse()) {
    if (typeof dispose === 'function') dispose()
  }
  release()
  await pending
  assert.equal(injects, 0, 'a late bridge answer must not install a section after unload')
  assert.equal(fiber.disposed, 0)
})

test('a fiber that races past inject is disposed instead of retained', async () => {
  const ctx = fakeContext()
  const fiber = { disposed: 0, dispose() { this.disposed += 1 } }
  let unload = () => {}
  const agent = {
    ctx: {
      inject(_names, register) {
        register({ systemPrompt: { section() {} } })
        // Simulate unload re-entering after the fiber is created but before
        // the install promise's .then runs.
        unload()
        return fiber
      },
    },
    session: { id: 's3', header: { cwd: '/tmp/ws' } },
  }
  apply(ctx, {
    sidecar: '/tmp/x.json',
    infra: INFRA,
    python: '/bin/false',
    bridge: async () => ({
      ok: true,
      status: 'authorized',
      startup_text: 'workspace: /tmp/ws',
      startup: { workspace: '/tmp/ws' },
    }),
  })
  unload = () => {
    for (const dispose of ctx._state.effects.slice().reverse()) {
      if (typeof dispose === 'function') dispose()
    }
  }
  const created = ctx._state.handlers['agent/created'][0]
  await created({ agent })
  assert.equal(fiber.disposed, 1)
})

import assert from 'node:assert/strict'
import test from 'node:test'

import { apply, authorityPromptText, resolveInfra } from '../lib/index.js'

const INFRA = resolveInfra({})

const AUTHORIZED = {
  ok: true,
  operation: 'context',
  status: 'authorized',
  outcome: 'authorized',
  anchored: true,
  startup: { workspace: '/tmp/ws' },
  startup_text: [
    'EU4 harness context (prepared, not model-reported):',
    '- workspace: /tmp/ws (mode=seed, head=seed)',
    '- identity: digest=0123456789abcdef (verified against the prepared anchor: workspace, policy, budget, sidecar and campaign)',
    '- policy: digest=fedcba9876543210; allow_symlinks=False',
    '- stop path: `eu4_finalize_candidate` only after a checker pass',
  ].join('\n'),
}

/** A context that records prompt sections and agent lifecycle listeners. */
function harness({ contextResult = AUTHORIZED, contextError, sidecar = '/tmp/campaign.json' } = {}) {
  const state = {
    tools: [], guards: [], listeners: {}, warnings: [], calls: [],
  }
  const ctx = {
    logger: { warn(message) { state.warnings.push(message) }, info() {} },
    effect: (fn) => fn(),
    on(event, handler) {
      if (!state.listeners[event]) state.listeners[event] = []
      state.listeners[event].push(handler)
    },
    async emit(event, payload) {
      const handlers = state.listeners[event] || []
      for (const handler of handlers) await handler(payload)
    },
    tools: {
      register(definition) { state.tools.push(definition); return () => {} },
      guard(fn) { state.guards.push(fn); return () => {} },
    },
    goals: { get: () => undefined, complete() {}, pause() {}, block() {} },
    systemPrompt: {
      section(section) { state.sections = state.sections || []; state.sections.push(section); return () => {} },
    },
    agents: { list: () => [] },
  }
  const bridge = async (_infra, _python, operation, _sidecar, _extra, timeout) => {
    state.calls.push({ operation, timeout })
    if (contextError) throw contextError
    return contextResult
  }
  apply(ctx, { sidecar, infra: INFRA, bridge })
  return { ctx, state }
}

/** One live agent with a scoped prompt registry, as `agent.ctx` provides. */
function agentAt(cwd, { id = 'a1' } = {}) {
  const sections = []
  const fibers = []
  return {
    id,
    sessionId: id,
    session: { header: { cwd } },
    _sections: sections,
    _fibers: fibers,
    ctx: {
      inject(services, install) {
        assert.deepEqual(services, ['systemPrompt'])
        install({
          systemPrompt: {
            section(section) { sections.push(section); return () => {} },
          },
        })
        const fiber = {
          disposed: false,
          dispose() { this.disposed = true },
        }
        fibers.push(fiber)
        return fiber
      },
    },
  }
}

test('a resumed agent receives the verified boundary in its scoped system prompt', async () => {
  const { ctx, state } = harness()
  const agent = agentAt('/tmp/ws')
  await ctx.emit('agent/created', { agent })

  assert.deepEqual(state.calls.map((call) => call.operation), ['context'])
  assert.equal(state.calls[0].timeout, 60 * 1000)
  assert.equal(agent._sections.length, 1)
  const section = agent._sections[0]
  assert.equal(section.name, 'campaign:eu4-authority')
  assert.equal(section.interpolate, false, 'paths must not be interpolated as variables')
  const text = section.text()
  assert.match(text, /EU4 campaign authority boundary/)
  assert.match(text, /workspace: \/tmp\/ws/)
  assert.match(text, /identity: digest=0123456789abcdef/)
  assert.match(text, /stop path:/)
})

test('one context call serves every assembly for the same agent', async () => {
  const { ctx, state } = harness()
  const agent = agentAt('/tmp/ws')
  await ctx.emit('agent/created', { agent })
  await ctx.emit('agent/created', { agent })
  assert.equal(state.calls.length, 1)
  assert.equal(agent._sections[0].text(), agent._sections[0].text())
  assert.equal(state.calls.length, 1, 'rendering must not re-run the bridge')
})

test('a refused context preserves the baseline prompt', async () => {
  const { ctx, state } = harness({
    contextResult: {
      ok: false,
      operation: 'context',
      status: 'drift',
      outcome: 'refused',
      kind: 'identity_modified',
      detail: 'protected material changed since prepare: budget.max_validations',
    },
  })
  const agent = agentAt('/tmp/ws')
  await ctx.emit('agent/created', { agent })
  assert.equal(agent._sections.length, 0)
  assert.match(state.warnings.join('\n'), /baseline prompt preserved/)
  assert.match(state.warnings.join('\n'), /budget\.max_validations/)
})

test('an unavailable context operation preserves the baseline prompt', async () => {
  const { ctx, state } = harness({ contextError: new Error('spawn failed') })
  const agent = agentAt('/tmp/ws')
  await ctx.emit('agent/created', { agent })
  assert.equal(agent._sections.length, 0)
  assert.match(state.warnings.join('\n'), /context could not be read/)
})

test('an authorized context is not mounted on a different session workspace', async () => {
  const { ctx, state } = harness()
  const agent = agentAt('/tmp/elsewhere')
  await ctx.emit('agent/created', { agent })
  assert.equal(agent._sections.length, 0)
  assert.match(state.warnings.join('\n'), /not mounted on this session/)
})

test('a profile without a campaign sidecar makes no context call', async () => {
  const { ctx, state } = harness({ sidecar: '' })
  const agent = agentAt('/tmp/ws')
  await ctx.emit('agent/created', { agent })
  assert.equal(state.calls.length, 0)
  assert.equal(agent._sections.length, 0)
})

test('disposing an agent releases its prompt fiber', async () => {
  const { ctx } = harness()
  const agent = agentAt('/tmp/ws')
  await ctx.emit('agent/created', { agent })
  assert.equal(agent._fibers.length, 1)
  await ctx.emit('agent/disposed', { agent })
  assert.equal(agent._fibers[0].disposed, true)
})

test('only an ok context result becomes prompt text', () => {
  assert.equal(authorityPromptText(undefined), '')
  assert.equal(authorityPromptText({ ok: false }), '')
  assert.equal(authorityPromptText({ ok: true }), '')
  assert.equal(authorityPromptText({ ok: true, startup_text: '  ' }), '')
  assert.match(authorityPromptText(AUTHORIZED), /verified by the trusted bridge/)
})

test('the plugin declares the system-prompt service it contributes through', async () => {
  const module = await import('../lib/index.js')
  assert.ok(module.inject.includes('systemPrompt'))
})

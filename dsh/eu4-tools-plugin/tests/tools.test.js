// Unit tests use a fake cordis context and an injected runner: no Python, no EU4.
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { apply, definitions, name, inject, TOOL_NAMES } from '../lib/index.js'
import { buildArgs, sanitizedEnv } from '../lib/bridge.js'

function fakeContext() {
  const registered = []
  return {
    registered,
    tools: {
      register(definition) {
        registered.push(definition)
        return () => {}
      },
    },
    effect(callback) {
      return callback()
    },
    logger: { info() {}, warn() {} },
  }
}

test('mounts exactly the seven logical tools with typed schemas', () => {
  const ctx = fakeContext()
  apply(ctx, { runner: async () => ({ ok: true }) })
  assert.equal(name, 'eu4-tools')
  assert.deepEqual(inject, ['tools'])
  assert.deepEqual(ctx.registered.map((tool) => tool.name), [...TOOL_NAMES])
  for (const tool of ctx.registered) {
    assert.equal(tool.parameters.type, 'object')
    assert.ok(Array.isArray(tool.parameters.required))
    assert.equal(tool.output.schema.type, 'object')
    assert.equal(typeof tool.output.render, 'function')
  }
  const re = ctx.registered[0]
  assert.ok(re.parameters.properties.op.enum.includes('resolve'))
  assert.deepEqual(re.parameters.required, ['op'])
})

test('execute passes the typed object to the shared runner', async () => {
  const seen = []
  const ctx = fakeContext()
  apply(ctx, { runner: async (tool, args) => { seen.push([tool, args]); return { ok: true } } })
  await ctx.registered[0].execute({ op: 'resolve', target: 'SShaderOpenGL', limit: 2 })
  assert.deepEqual(seen, [['eu4_re_query', { op: 'resolve', target: 'SShaderOpenGL', limit: 2 }]])
})

test('runner errors become a structured envelope, not a crash', async () => {
  const ctx = fakeContext()
  apply(ctx, { runner: async () => { throw new Error('boom') } })
  const value = await ctx.registered[0].execute({ op: 'provenance' })
  assert.equal(value.ok, false)
  assert.equal(value.status, 'error')
  assert.match(value.reason, /boom/)
})

test('argument mapping covers each tool', () => {
  assert.deepEqual(buildArgs('eu4_re_query', { op: 'function', target: '0x2000' }),
    ['eu4_re_query', 'function', '0x2000'])
  assert.deepEqual(buildArgs('eu4_session', { op: 'inspect', run: '/r' }),
    ['eu4_session', 'inspect', '--run', '/r'])
  assert.deepEqual(buildArgs('eu4_session', { op: 'lease', executable: '/bin/true' }),
    ['eu4_session', 'lease', '--executable', '/bin/true'])
  assert.deepEqual(buildArgs('eu4_capture', { op: 'request', run: '/r', role: 'candidate', frames: [4, 5] }),
    ['eu4_capture', 'request', '--run', '/r', '--role', 'candidate', '--frame', '4', '--frame', '5'])
  const diff = buildArgs('eu4_diff_run', { scenario: 's.json', run_root: '/r', ledger: '/l', mode: 'lockstep' })
  assert.deepEqual(diff, ['eu4_diff_run', '--scenario', 's.json', '--run-root', '/r', '--ledger', '/l', '--mode', 'lockstep'])
  const staggered = buildArgs('eu4_diff_run', { scenario: 's.json', run_root: '/r', ledger: '/l', staggered: true, stagger_wait: 120 })
  assert.ok(staggered.includes('--staggered'))
  assert.deepEqual(staggered.slice(-2), ['--stagger-wait', '120'])
  assert.deepEqual(buildArgs('eu4_diff_inspect', { run: '/r', build_case: true }),
    ['eu4_diff_inspect', '--run', '/r', '--build-case'])
  assert.deepEqual(buildArgs('eu4_replay', { op: 'case', case: '/c' }),
    ['eu4_replay', 'case', '--case', '/c'])
  assert.deepEqual(buildArgs('eu4_case_inspect', { case: '/c' }),
    ['eu4_case_inspect', '--case', '/c'])
})

test('environment is allowlisted', () => {
  const env = sanitizedEnv({ PATH: '/bin', SECRET: 'x', EU4_RE_INDEX: '/i' }, { EXTRA: '1' })
  assert.equal(env.PATH, '/bin')
  assert.equal(env.EU4_RE_INDEX, '/i')
  assert.equal(env.EXTRA, '1')
  assert.equal(env.SECRET, undefined)
})

test('definitions are buildable with a no-op runner', () => {
  const tools = definitions(async () => ({ ok: true }))
  assert.equal(tools.length, 7)
})

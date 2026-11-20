import assert from 'node:assert/strict'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
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
    return typeof value === 'function' ? value(extra) : value
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
  assert.deepEqual(calls, [
    ['prepare', '--session-id', 'sess-1'],
    ['validate', '--session-id', 'sess-1'],
  ])
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

test('a failed prepare pauses instead of leaving the campaign running', async () => {
  const { ctx } = harness({
    prepare: { ok: false, kind: 'identity_modified', goal_policy: 'pause',
               detail: 'protected material changed' },
  })
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(result.kind, 'identity_modified')
  assert.equal(ctx._state.paused.length, 1)
  assert.equal(ctx._state.completed.length, 0)
})

test('lock contention pauses the goal rather than continuing', async () => {
  const { ctx } = harness({
    // The shape the bridge actually returns for a contended run.
    prepare: { ok: false, classification: 'policy_stop', kind: 'busy',
               goal_policy: 'pause', detail: 'another run holds the lock' },
  })
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(result.kind, 'busy')
  assert.equal(ctx._state.paused.length, 1)
})

test('a workspace mismatch pauses the goal', async () => {
  const { ctx } = harness({ prepare: PREPARED })
  const elsewhere = { sessionId: 's1', id: 'a1', session: { header: { cwd: '/tmp/other' } } }
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent: elsewhere })
  assert.equal(result.kind, 'workspace_mismatch')
  assert.equal(ctx._state.paused.length, 1)
})

test('a goal-binding failure pauses and cannot be bypassed by validating again', async () => {
  const { ctx } = harness({ prepare: PREPARED, validate: { ok: true, checker_verdict: 'pass' } })
  await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  ctx._state.liveGoal = { id: 'g1', revision: 2, objective: 'different work' }

  const second = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(second.kind, 'goal_binding')
  assert.equal(ctx._state.paused.length, 1)

  const third = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(third.kind, 'goal_binding')
  assert.equal(ctx._state.completed.length, 0)
})

test('a passing validation never completes the goal by itself', async () => {
  const { ctx } = harness({ prepare: PREPARED, validate: { ok: true, checker_verdict: 'pass' } })
  const result = await tool(ctx, 'eu4_validate_candidate').execute({}, { agent })
  assert.equal(result.checker_verdict, 'pass')
  assert.equal(ctx._state.completed.length, 0)
  assert.equal(ctx._state.paused.length, 0)
})


/** A scripted bridge that reports a per-session root exactly like the real one. */
function sessionScript(runRoot) {
  const prepare = (extra) => {
    const index = Array.isArray(extra) ? extra.indexOf('--session-id') : -1
    const sessionId = index >= 0 ? extra[index + 1] : null
    const root = sessionId ? path.join(runRoot, 'sessions', sessionId) : runRoot
    fs.mkdirSync(root, { recursive: true })
    return {
      ok: true,
      workspace: '/tmp/ws',
      run_root: runRoot,
      session: {
        session_id: sessionId,
        root,
        kind: sessionId ? 'session' : 'legacy-shared',
        reason: sessionId ? 'isolated' : 'no session id was supplied',
      },
    }
  }
  return {
    prepare,
    validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
    finalize: { ok: true, operation: 'finalize', classification: 'verdict',
                checker_verdict: 'pass' },
  }
}

test('two sessions bind into their own roots and a reconnect resumes the same root', async () => {
  const runRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'eu4-goal-bind-'))
  try {
    const a = { sessionId: 'sess-a', id: 'a1', session: { header: { cwd: '/tmp/ws' } } }
    const b = { sessionId: 'sess-b', id: 'b1', session: { header: { cwd: '/tmp/ws' } } }

    const first = harness(sessionScript(runRoot))
    await tool(first.ctx, 'eu4_validate_candidate').execute({}, { agent: a })
    const aFile = path.join(runRoot, 'sessions', 'sess-a', 'plugin-binding.json')
    assert.ok(fs.existsSync(aFile), 'session A binds in its own root')

    await tool(first.ctx, 'eu4_validate_candidate').execute({}, { agent: b })
    const bFile = path.join(runRoot, 'sessions', 'sess-b', 'plugin-binding.json')
    assert.ok(fs.existsSync(bFile), 'session B binds in its own root')
    assert.notEqual(aFile, bFile)

    const storedA = JSON.parse(fs.readFileSync(aFile, 'utf8'))
    const storedB = JSON.parse(fs.readFileSync(bFile, 'utf8'))
    assert.equal(storedA.session_id, 'sess-a')
    assert.equal(storedB.session_id, 'sess-b')

    // A fresh plugin instance is a reconnect: no in-memory binding, so the
    // record is read back from the same session root.
    const reconnected = harness(sessionScript(runRoot))
    const result = await tool(reconnected.ctx, 'eu4_finalize_candidate')
      .execute({}, { agent: a })
    assert.equal(result.completion, 'completed')
    assert.equal(reconnected.ctx._state.completed.length, 1)
  } finally {
    fs.rmSync(runRoot, { recursive: true, force: true })
  }
})

test('two sessions in a legacy shared root do not overwrite each other', async () => {
  const runRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'eu4-goal-legacy-'))
  try {
    const plugin = harness({
      prepare: { ok: true, workspace: '/tmp/ws', run_root: runRoot },
      validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
    })
    const a = { sessionId: 'sess-a', id: 'a1', session: { header: { cwd: '/tmp/ws' } } }
    const b = { sessionId: 'sess-b', id: 'b1', session: { header: { cwd: '/tmp/ws' } } }
    await tool(plugin.ctx, 'eu4_validate_candidate').execute({}, { agent: a })
    const aFile = path.join(runRoot, 'plugin-binding.sess-a.json')
    assert.ok(fs.existsSync(aFile))
    const before = fs.readFileSync(aFile, 'utf8')

    await tool(plugin.ctx, 'eu4_validate_candidate').execute({}, { agent: b })
    assert.ok(fs.existsSync(path.join(runRoot, 'plugin-binding.sess-b.json')))
    assert.equal(fs.readFileSync(aFile, 'utf8'), before)
    assert.equal(JSON.parse(before).session_id, 'sess-a')
  } finally {
    fs.rmSync(runRoot, { recursive: true, force: true })
  }
})

test('a reconnect does not adopt another session binding in a shared root', async () => {
  const runRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'eu4-goal-adopt-'))
  try {
    // No session layout reported: the bridge fell back to the legacy shared
    // root, where another session already recorded its binding.
    fs.writeFileSync(path.join(runRoot, 'plugin-binding.json'), JSON.stringify({
      schema: 'eu4-dsh-plugin-binding/v1',
      session_id: 'sess-other',
      goal_id: 'g1',
      objective_digest: 'not-this-session',
    }))
    const plugin = harness({
      prepare: { ok: true, workspace: '/tmp/ws', run_root: runRoot },
      validate: { ok: true, classification: 'verdict', checker_verdict: 'pass' },
      finalize: { ok: true, operation: 'finalize', classification: 'verdict',
                  checker_verdict: 'pass' },
    })
    const result = await tool(plugin.ctx, 'eu4_finalize_candidate')
      .execute({}, { agent })
    assert.equal(result.kind, 'goal_binding')
    assert.match(result.detail, /no campaign binding has been recorded/)
    assert.equal(plugin.ctx._state.completed.length, 0)
  } finally {
    fs.rmSync(runRoot, { recursive: true, force: true })
  }
})

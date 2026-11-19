import assert from 'node:assert/strict'
import test from 'node:test'

import {
  completionDenialReason,
  digestObjective,
  goalBindingProblems,
  qualificationRefusal,
  workspaceRefusal,
  mergeDenialReason,
} from '../lib/decisions.js'

test('every model-initiated completion is denied, bound or not', () => {
  assert.ok(completionDenialReason({ name: 'update_goal', arguments: { action: 'complete' } }))
  // The first autonomous round reaches this before any campaign tool has run.
  assert.ok(completionDenialReason({ name: 'update_goal', arguments: { action: 'complete' } }))
})

test('other goal actions and other tools are left alone', () => {
  assert.equal(completionDenialReason({ name: 'update_goal', arguments: { action: 'pause' } }), undefined)
  assert.equal(completionDenialReason({ name: 'get_goal', arguments: {} }), undefined)
  assert.equal(completionDenialReason({ name: 'eu4_finalize_candidate', arguments: {} }), undefined)
  assert.equal(completionDenialReason({ name: 'update_goal' }), undefined)
})

test('merges and direct main pushes are denied for command tools', () => {
  const denied = [
    { name: 'bash', arguments: { command: 'gh pr merge 214 --squash' } },
    { name: 'bash', arguments: { command: 'git merge m7/pr1' } },
    { name: 'bash', arguments: { command: 'git push origin main' } },
    { name: 'shell', arguments: { command: 'gh api -X PUT repos/x/y/pulls/1/merge' } },
  ]
  for (const exec of denied) assert.ok(mergeDenialReason(exec), exec.name)
  const allowed = [
    { name: 'bash', arguments: { command: 'git merge-base --is-ancestor a b' } },
    { name: 'bash', arguments: { command: 'git push origin feature/x' } },
    { name: 'bash', arguments: { command: 'gh pr view 214' } },
    { name: 'read', arguments: { path: 'README.md' } },
  ]
  for (const exec of allowed) assert.equal(mergeDenialReason(exec), undefined)
})

test('a replaced goal invalidates the binding', () => {
  const goal = { id: 'g1', objective: 'make it ready' }
  const binding = { goal_id: 'g1', objective_digest: digestObjective(goal.objective) }
  assert.deepEqual(goalBindingProblems(binding, goal), [])
  assert.equal(goalBindingProblems(binding, { id: 'g2', objective: 'make it ready' }).length, 1)
})

test('an edited objective invalidates the binding', () => {
  const binding = { goal_id: 'g1', objective_digest: digestObjective('original') }
  const problems = goalBindingProblems(binding, { id: 'g1', objective: 'edited' })
  assert.equal(problems.length, 1)
  assert.match(problems[0], /objective changed/)
})

test('missing binding and missing goal are both refused', () => {
  assert.equal(goalBindingProblems(null, { id: 'g1', objective: 'x' }).length, 1)
  assert.equal(goalBindingProblems({ goal_id: 'g1', objective_digest: 'x' }, null).length, 1)
})

test('only a fresh passing finalize may authorize completion', () => {
  const good = {
    ok: true,
    operation: 'finalize',
    classification: 'verdict',
    checker_verdict: 'pass',
  }
  assert.equal(qualificationRefusal(good), undefined)
  assert.ok(qualificationRefusal({ ...good, checker_verdict: 'fail' }))
  assert.ok(qualificationRefusal({ ...good, classification: 'infrastructure_error' }))
  assert.ok(qualificationRefusal({ ...good, operation: 'validate' }))
  assert.ok(qualificationRefusal({ ...good, ok: false, kind: 'stale' }))
  assert.ok(qualificationRefusal(undefined))
})

test('only the campaign workspace itself is an acceptable session root', () => {
  assert.equal(workspaceRefusal('/repo/.local/dsh/x/workspace',
                                '/repo/.local/dsh/x/workspace'), undefined)
  assert.equal(workspaceRefusal('/repo/.local/dsh/x/workspace/', '/repo/.local/dsh/x/workspace'), undefined)
})

test('a parent or child of the candidate is refused', () => {
  const parent = workspaceRefusal('/repo', '/repo/.local/dsh/x/workspace')
  assert.match(parent, /is not the campaign workspace/)
  const child = workspaceRefusal('/repo/.local/dsh/x/workspace', '/repo')
  assert.match(child, /is not the campaign workspace/)
  // A sibling with a shared prefix must not be treated as equal either.
  assert.ok(workspaceRefusal('/repo-other', '/repo'))
})

test('an unknown session cwd or campaign workspace is refused', () => {
  assert.ok(workspaceRefusal(undefined, '/repo/ws'))
  assert.ok(workspaceRefusal('/repo', undefined))
})

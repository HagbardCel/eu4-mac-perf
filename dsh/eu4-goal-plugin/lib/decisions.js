/**
 * Pure decisions for the EU4 completion gate.
 *
 * Kept free of I/O and of the harness so they can be tested with `node --test`
 * without booting a session, a sandbox, or a Python interpreter.
 *
 * @module @eu4/dsh-goal/decisions
 */

import { createHash } from 'node:crypto'

/**
 * Deny every model-initiated goal completion in this profile.
 *
 * Binding is deliberately not consulted: a guard that depends on binding order
 * would allow completion on the first autonomous round, before any campaign
 * tool has run, and would fail open if binding state were lost. Completion has
 * exactly one path — the finalize tool, which re-verifies and then calls the
 * goal service itself.
 *
 * @param {{name?: string, arguments?: unknown}} exec
 * @returns {string|undefined} denial reason, or undefined to leave the call
 */
export function completionDenialReason(exec) {
  if (!exec || exec.name !== 'update_goal') return undefined
  const args = exec.arguments
  if (!args || typeof args !== 'object') return undefined
  if (args.action !== 'complete') return undefined
  return (
    'Completion is not self-certified for an EU4 campaign goal. ' +
    'update_goal(action="complete") is denied; call eu4_finalize_candidate, ' +
    'which re-runs the trusted checker and completes the goal only on a fresh pass.'
  )
}

/** sha256 of a goal objective, for binding qualification to one objective. */
export function digestObjective(text) {
  return createHash('sha256').update(String(text ?? ''), 'utf8').digest('hex')
}

/**
 * Problems with the qualification binding for the live goal.
 *
 * A human may legitimately edit or replace a goal. When they do, work already
 * qualified for the previous objective must not authorize the new one.
 *
 * @param {object|null|undefined} binding
 * @param {{id?: string, objective?: string}|null|undefined} liveGoal
 * @returns {string[]} problems; empty means the binding still applies
 */
export function goalBindingProblems(binding, liveGoal) {
  if (!liveGoal || !liveGoal.id) return ['there is no current goal to complete']
  if (!binding) return ['no campaign binding has been recorded for this session']
  const problems = []
  if (binding.goal_id !== liveGoal.id) {
    problems.push(
      `the bound goal (${binding.goal_id}) is not the current goal (${liveGoal.id})`
    )
  }
  if (binding.objective_digest !== digestObjective(liveGoal.objective)) {
    problems.push(
      'the goal objective changed since this campaign was bound; qualification ' +
        'for the previous objective does not authorize the new one'
    )
  }
  return problems
}

/**
 * Whether a bridge result may authorize completion.
 *
 * `pass` here means the checker passed. It is not the same as "the run
 * completed" — an infrastructure error carries no verdict at all.
 *
 * @param {object|undefined} result parsed bridge output
 * @returns {string|undefined} refusal reason, or undefined when qualified
 */
export function qualificationRefusal(result) {
  if (!result) return 'the bridge produced no result'
  if (result.ok === false) {
    return `the bridge refused: ${result.kind || 'unknown'} ${result.detail || ''}`.trim()
  }
  if (result.classification !== 'verdict') {
    return `the bridge classification was ${result.classification}, not a verdict`
  }
  if (result.checker_verdict !== 'pass') {
    return `the checker verdict was ${result.checker_verdict}, not pass`
  }
  if (result.operation !== 'finalize') {
    return 'completion requires the finalize operation specifically'
  }
  return undefined
}

/**
 * Dump the merged skill catalog once, at boot.
 *
 * `preflight.sh` can only show that the skill-filesystem row is configured. The
 * question that actually matters is whether the agent's catalog contains the
 * reconstruction skill, which is a registry view and cannot be read from the
 * composed config. This probe injects `skills`, reads the same catalog the model
 * sees, and writes it to EU4_SKILL_PROBE_OUT. It is loaded through `--patch`, so
 * it never becomes part of the profile.
 */

import fs from 'node:fs'

export const name = 'skill-catalog-probe'
export const inject = ['skills']

export function apply(ctx) {
  const out = process.env.EU4_SKILL_PROBE_OUT
  if (!out) return
  ctx.effect(() => {
    Promise.resolve()
      .then(() => ctx.skills.list({ cwd: process.cwd() }))
      .then((rows) => {
        fs.writeFileSync(out, JSON.stringify({
          ok: true,
          count: Array.isArray(rows) ? rows.length : -1,
          skills: rows,
        }, null, 2))
      })
      .catch((error) => {
        fs.writeFileSync(out, JSON.stringify({
          ok: false,
          error: String((error && error.message) || error),
        }, null, 2))
      })
    return () => {}
  })
}

/**
 * EU4 campaign plugin for DeepSeek Harness.
 *
 * M1 scope: prove the profile mounts an out-of-tree cordis plugin. Campaign
 * binding, the trusted validation bridge and the fail-closed completion guard
 * land in M2/M3 (see docs/dsh-native-goal.md).
 *
 * @module @eu4/dsh-goal
 */

export const name = 'eu4-goal'

/** Services this plugin needs before apply() runs. */
export const inject = ['tools', 'goals', 'agents']

/**
 * Mount the plugin on the host plane.
 * @param ctx - cordis context
 * @param config - resolved profile configuration
 */
export function apply(ctx, config = {}) {
  const sidecar = config.sidecar || ''
  ctx.logger?.info?.(
    `[eu4-goal] mounted (M1); sidecar=${sidecar || '<unset>'}`,
  )
}

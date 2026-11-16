// Thin bridge from a DSH tool call to the stable Python tool core.
import { spawn } from 'node:child_process'
import { accessSync, constants } from 'node:fs'
import { delimiter, isAbsolute, join } from 'node:path'

export const TOOL_NAMES = [
  'eu4_re_query',
  'eu4_session',
  'eu4_capture',
  'eu4_diff_run',
  'eu4_diff_inspect',
  'eu4_replay',
  'eu4_case_inspect',
]

// The adapter must spawn exactly the interpreter the launcher resolved and
// preflight verified. A relative name is resolved once, to an absolute PATH
// target; it is never passed through as a name the DSH server could resolve
// differently. An empty value fails closed: no silent interpreter default.
export function resolvePython(requested) {
  const value = requested || process.env.EU4_DSH_PYTHON || ''
  if (!value) {
    throw new Error(
      'EU4_DSH_PYTHON is not configured; start the profile through ' +
        'dsh/scripts/run.sh or set it to an absolute Python with NumPy')
  }
  const executable = (candidate) => {
    try {
      accessSync(candidate, constants.X_OK)
      return true
    } catch {
      return false
    }
  }
  if (isAbsolute(value)) {
    if (!executable(value)) {
      throw new Error(`configured Python interpreter is not executable: ${value}`)
    }
    return value
  }
  for (const directory of (process.env.PATH || '').split(delimiter)) {
    if (!directory) continue
    const candidate = join(directory, value)
    if (executable(candidate)) return candidate
  }
  throw new Error(
    `cannot resolve Python interpreter ${JSON.stringify(value)} on PATH; ` +
      'set EU4_DSH_PYTHON to an absolute path')
}

const ENV_ALLOW = new Set([
  'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'TERM',
  'EU4_RE_EXPORT', 'EU4_RE_INDEX', 'EU4_RE_REVISION',
  'EU4_GFX_SESSION_ROOT', 'EU4_LAUNCH_LEASE_DIR',
  'EU4_EVIDENCE_ROOT',
  'GHIDRA_INSTALL', 'JAVA_HOME',
])

export function buildArgs(tool, args = {}) {
  const value = (name) => (args[name] === undefined || args[name] === null ? null : String(args[name]))
  const add = (flag, name) => {
    const item = value(name)
    if (item !== null && item !== '') output.push(flag, item)
  }
  const output = [tool]
  if (tool === 'eu4_re_query') {
    if (!args.op) throw new Error('eu4_re_query requires op')
    output.push(String(args.op))
    if (value('target')) output.push(String(args.target))
    add('--index', 'index')
    add('--limit', 'limit')
    add('--max-bytes', 'max_bytes')
  } else if (tool === 'eu4_session') {
    output.push(String(args.op || 'list'))
    add('--run', 'run')
    add('--executable', 'executable')
    if (args.include_history) output.push('--include-history')
    add('--status', 'status')
    add('--limit', 'limit')
    add('--offset', 'offset')
    add('--evidence-root', 'evidence_root')
    add('--query', 'query')
    add('--classification', 'classification')
    add('--since', 'since')
  } else if (tool === 'eu4_capture') {
    if (!args.op) throw new Error('eu4_capture requires op')
    output.push(String(args.op))
    add('--run', 'run')
    add('--role', 'role')
    add('--frame-dir', 'frame_dir')
    for (const frame of args.frames || []) output.push('--frame', String(frame))
    add('--around', 'around')
    add('--offset', 'offset')
    add('--limit', 'limit')
    if (args.full) output.push('--full')
    if (args.require_pixels) output.push('--require-pixels')
    add('--checkpoint', 'checkpoint')
    add('--occurrence', 'occurrence')
    add('--output', 'output')
    add('--campaign-run-root', 'campaign_run_root')
    add('--caller-rva', 'caller_rva')
    add('--shader-cache', 'shader_cache')
    add('--timeout', 'timeout')
  } else if (tool === 'eu4_diff_run') {
    add('--scenario', 'scenario')
    add('--run-root', 'run_root')
    add('--ledger', 'ledger')
    add('--shader-cache', 'shader_cache')
    add('--qualified-sources', 'qualified_sources')
    add('--mode', 'mode')
    add('--max-frames', 'max_frames')
    add('--profile', 'profile')
    if (args.staggered) output.push('--staggered')
    add('--stagger-wait', 'stagger_wait')
    if (args.background) output.push('--background')
    add('--display-name', 'display_name')
  } else if (tool === 'eu4_diff_inspect') {
    add('--run', 'run')
    if (args.build_case) output.push('--build-case')
    if (args.full) output.push('--full')
    add('--limit', 'limit')
    add('--offset', 'offset')
    if (args.temporal) output.push('--temporal')
    add('--reference-frame', 'reference_frame')
    add('--candidate-frame', 'candidate_frame')
    add('--temporal-window', 'temporal_window')
    if (args.temporal_artifacts) output.push('--temporal-artifacts')
    add('--temporal-artifact-dir', 'temporal_artifact_dir')
  } else if (tool === 'eu4_replay') {
    output.push(String(args.op || 'case'))
    add('--case', 'case')
    add('--frame', 'frame')
    for (const frame of args.frame_dirs || []) output.push('--frame-dir', String(frame))
    add('--metal', 'metal')
    add('--shaders', 'shaders')
    add('--replay-out', args.replay_out === undefined ? 'out' : 'replay_out')
    add('--policy', 'policy')
    if (args.require_seal) output.push('--require-seal')
    add('--max-frames', 'max_frames')
    add('--max-bytes', 'max_bytes')
    add('--case-max-bytes', 'case_max_bytes')
    add('--max-seconds', 'max_seconds')
    add('--frame-timeout', 'frame_timeout')
    add('--timeout', 'timeout')
    if (args.continue_on_difference) output.push('--continue-on-difference')
    add('--package-dir', 'package_dir')
    add('--only-event', 'only_event')
    if (args.disable_render_target_vflip) output.push('--disable-render-target-vflip')
    add('--sampler-shift', 'sampler_shift')
    add('--sampler-shift-min', 'sampler_shift_min')
  } else if (tool === 'eu4_case_inspect') {
    add('--case', 'case')
  } else {
    throw new Error(`unknown EU4 tool: ${tool}`)
  }
  return output
}

export function sanitizedEnv(base = process.env, extra = {}) {
  const env = {}
  for (const [key, value] of Object.entries(base)) {
    if (ENV_ALLOW.has(key)) env[key] = value
  }
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined && value !== null) env[key] = String(value)
  }
  return env
}

export function parseEnvelope(stdout) {
  const text = String(stdout || '').trim()
  if (!text) throw new Error('tool produced no JSON')
  return JSON.parse(text)
}

export function runTool(tool, args, options = {}) {
  return new Promise((resolve, reject) => {
    let python
    try {
      python = resolvePython(options.python)
    } catch (error) {
      reject(error)
      return
    }
    const cwd = options.cwd || process.cwd()
    const argv = ['-m', 'automation.agent_tools', ...buildArgs(tool, args)]
    const child = spawn(python, argv, {
      cwd,
      env: sanitizedEnv(process.env, options.env),
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.on('data', (chunk) => { stdout += chunk })
    child.stderr.on('data', (chunk) => { stderr += chunk })
    const timeout = options.timeoutMs || 900000
    const timer = setTimeout(() => {
      child.kill('SIGTERM')
      reject(new Error(`tool ${tool} exceeded ${timeout}ms`))
    }, timeout)
    child.on('error', (error) => { clearTimeout(timer); reject(error) })
    child.on('close', (code) => {
      clearTimeout(timer)
      try {
        const value = parseEnvelope(stdout)
        value.exit_code = code
        if (stderr && value.stderr === undefined) value.stderr = stderr.slice(-4000)
        resolve(value)
      } catch (error) {
        reject(new Error(`${error.message}; exit=${code}; stderr=${stderr.slice(-1000)}`))
      }
    })
  })
}

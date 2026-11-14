// Thin bridge from a DSH tool call to the stable Python tool core.
import { spawn } from 'node:child_process'

export const TOOL_NAMES = [
  'eu4_re_query',
  'eu4_session',
  'eu4_capture',
  'eu4_diff_run',
  'eu4_diff_inspect',
  'eu4_replay',
  'eu4_case_inspect',
]

const ENV_ALLOW = new Set([
  'PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'TERM',
  'EU4_RE_EXPORT', 'EU4_RE_INDEX', 'EU4_RE_REVISION',
  'EU4_GFX_SESSION_ROOT', 'EU4_LAUNCH_LEASE_DIR',
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
  } else if (tool === 'eu4_capture') {
    if (!args.op) throw new Error('eu4_capture requires op')
    output.push(String(args.op))
    add('--run', 'run')
    add('--role', 'role')
    for (const frame of args.frames || []) output.push('--frame', String(frame))
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
  } else if (tool === 'eu4_diff_inspect') {
    add('--run', 'run')
    if (args.build_case) output.push('--build-case')
  } else if (tool === 'eu4_replay') {
    output.push(String(args.op || 'case'))
    add('--case', 'case')
    add('--frame', 'frame')
    add('--shaders', 'shaders')
    add('--out', 'out')
    add('--only-event', 'only_event')
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
    const python = options.python || 'python3.14'
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

// Structured EU4 reconstruction tools for DeepSeek Harness.
//
// The plugin is deliberately thin: every tool spawns the stable Python core
// (`automation.agent_tools`) with a typed argument object and returns its JSON
// envelope.  No rendering, comparison or launch logic lives here.
import { runTool, TOOL_NAMES } from './bridge.js'

export const name = 'eu4-tools'
export const inject = ['tools']

function textContent(value) {
  return [{ type: 'text', text: JSON.stringify(value, null, 2) }]
}

function tool(name, description, properties, required, execute) {
  return {
    name,
    description,
    parameters: {
      type: 'object',
      properties,
      required,
      additionalProperties: false,
    },
    output: {
      schema: { type: 'object', additionalProperties: true },
      render: (_args, value) => textContent(value),
    },
    execute,
  }
}

const string = (description) => ({ type: 'string', description })
const integer = (description) => ({ type: 'integer', description })
const boolean = (description) => ({ type: 'boolean', description })

export function definitions(run) {
  const safe = (toolName) => async (args, _exec) => {
    try {
      return await run(toolName, args || {})
    } catch (error) {
      return {
        schema: 'eu4-agent-tool/v1',
        tool: toolName,
        ok: false,
        status: 'error',
        reason: `${error.name || 'Error'}: ${error.message || String(error)}`,
        result: {},
      }
    }
  }
  return [
    tool(
      'eu4_re_query',
      'Query existing Ghidra/corpus knowledge (function, callers/callees, xrefs, ' +
        'pseudocode, ABI, struct layouts, provenance). Query before disassembling.',
      {
        op: { type: 'string', enum: ['resolve', 'function', 'callers', 'callees', 'decompile', 'xrefs', 'abi', 'struct', 'provenance'], description: 'query operation' },
        target: string('name, RVA, or struct/layout name'),
        index: string('optional explicit index.sqlite path'),
        limit: integer('maximum matches'),
        max_bytes: integer('maximum pseudocode bytes'),
      },
      ['op'],
      safe('eu4_re_query'),
    ),
    tool(
      'eu4_session',
      'Inspect or safely terminate a session owned by this harness. Never lists or kills unrelated EU4 processes.',
      {
        op: { type: 'string', enum: ['list', 'inspect', 'terminate'], description: 'session operation' },
        run: string('session run directory'),
      },
      ['op'],
      safe('eu4_session'),
    ),
    tool(
      'eu4_capture',
      'Inspect frame observations/health for a run, request bounded frame dumps, or run one targeted live checkpoint probe.',
      {
        op: { type: 'string', enum: ['health', 'frames', 'request', 'probe'], description: 'capture operation' },
        run: string('run or session directory'),
        role: { type: 'string', enum: ['reference', 'candidate'], description: 'backend role' },
        frames: { type: 'array', items: { type: 'integer' }, description: 'frame indices to request' },
        require_pixels: boolean('require an aligned pixel comparison'),
        checkpoint: string('probe checkpoint'),
        occurrence: integer('probe occurrence'),
        output: string('probe output directory'),
        campaign_run_root: string('authorized campaign run root for probe'),
        caller_rva: string('probe caller RVA'),
        shader_cache: string('shader-cache JSON file for probe'),
        timeout: { type: 'number', description: 'probe timeout seconds' },
      },
      ['op'],
      safe('eu4_capture'),
    ),
    tool(
      'eu4_diff_run',
      'Start and supervise a dual-process OpenGL-vs-Metal differential campaign from a validated scenario and an authorized ledger.',
      {
        scenario: string('scenario JSON path'),
        run_root: string('campaign run root'),
        ledger: string('authoritative workflow ledger'),
        shader_cache: string('Metal shader-cache JSON file'),
        qualified_sources: string('qualified shader sources JSON'),
        mode: { type: 'string', enum: ['streaming', 'lockstep'], description: 'synchronization mode' },
        max_frames: integer('maximum paired logical frames'),
        profile: { type: 'string', enum: ['early', 'targeted', 'qualification'], description: 'wait profile' },
        staggered: boolean('let the reference reach the target scene before starting the candidate'),
        stagger_wait: { type: 'number', description: 'reference phase wait seconds' },
      },
      ['scenario', 'run_root', 'ledger'],
      safe('eu4_diff_run'),
    ),
    tool(
      'eu4_diff_inspect',
      'Inspect a campaign result: stop reason, health, alignment, first differing frame, and optionally build the offline case package.',
      {
        run: string('session/run directory'),
        build_case: boolean('build the self-contained diagnostic case'),
      },
      ['run'],
      safe('eu4_diff_inspect'),
    ),
    tool(
      'eu4_replay',
      'Run a stored offline reproduction (packaged case comparison or selected-event frame replay).',
      {
        op: { type: 'string', enum: ['case', 'frame'], description: 'replay kind' },
        case: string('case directory'),
        frame: string('frame trace directory'),
        shaders: string('shader directory'),
        out: string('output directory'),
        only_event: integer('selected event index'),
      },
      ['op'],
      safe('eu4_replay'),
    ),
    tool(
      'eu4_case_inspect',
      'Summarize a diagnostic case package (classification, analysis path, artifact list).',
      { case: string('case directory') },
      ['case'],
      safe('eu4_case_inspect'),
    ),
  ]
}

export function apply(ctx, config = {}) {
  const python = config.python || 'python3.14'
  const cwd = config.infra || process.cwd()
  const run = config.runner || ((toolName, args) => runTool(toolName, args, {
    python,
    cwd,
    env: config.env,
    timeoutMs: config.timeoutMs,
  }))
  let registered = 0
  for (const definition of definitions(run)) {
    ctx.effect(() => {
      const dispose = ctx.tools.register(definition)
      registered += 1
      return dispose
    })
  }
  ctx.logger?.info?.(`[eu4-tools] registered ${registered} tools from ${TOOL_NAMES.length} names`)
}

export { TOOL_NAMES }

// Structured EU4 reconstruction tools for DeepSeek Harness.
//
// The plugin is deliberately thin: every tool spawns the stable Python core
// (`automation.agent_tools`) with a typed argument object and returns its JSON
// envelope.  No rendering, comparison or launch logic lives here.
import { resolvePython, runTool, TOOL_NAMES } from './bridge.js'

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
const number = (description) => ({ type: 'number', description })
const boolean = (description) => ({ type: 'boolean', description })

export function definitions(run) {
  const safe = (toolName) => async (args, _exec) => {
    try {
      return await run(toolName, args || {})
    } catch (error) {
      return {
        schema: 'eu4-agent-tool/v2',
        tool: toolName,
        ok: false,
        status: 'error',
        reason: `${error.name || 'Error'}: ${error.message || String(error)}`,
        result: {},
        outcome: {
          execution: 'error',
          diagnostic: 'failed',
          diagnostic_complete: false,
          verdict: null,
          evidence: null,
          qualified: null,
        },
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
        op: { type: 'string', enum: ['list', 'inspect', 'terminate', 'lease', 'discover'], description: 'session operation' },
        run: string('session run directory'),
        executable: string('executable path for a lease query'),
        include_history: boolean('include closed and stale session records'),
        status: { type: 'string', enum: ['active', 'closed', 'stale', 'all'], description: 'session class filter' },
        limit: integer('bounded result count'),
        offset: integer('pagination offset'),
        evidence_root: string('pathsep-separated evidence roots (default EU4_EVIDENCE_ROOT or .local)'),
        query: string('discovery text filter'),
        classification: string('discovery classification filter'),
        since: string('discovery ISO-8601 lower bound on finish time'),
      },
      ['op'],
      safe('eu4_session'),
    ),
    tool(
      'eu4_capture',
      'Inspect frame observations/health for a run, request bounded frame dumps, or run one targeted live checkpoint probe.',
      {
        op: { type: 'string', enum: ['health', 'frames', 'request', 'probe', 'graph', 'gl-control', 'gl-control-qualify'], description: 'capture operation' },
        run: string('run or session directory'),
        frame_dir: string('frame trace directory for the graph operation'),
        live_image: string('sealed live BGRA image for gl-control-qualify'),
        control_image: string('control BGRA image for --comparison-only'),
        renderer: string('original-API control renderer executed by the qualifier'),
        control_out: string('empty output directory for the renderer receipt/artifact'),
        checkpoint_event: integer('checkpoint event to qualify'),
        draw_event: integer('draw event that produced the checkpoint'),
        resource: string('checkpoint resource (default fb:0)'),
        drawable: string('drawable JSON record'),
        prestate: string('pre-draw state JSON record'),
        comparison_only: boolean('report pixel equality only; never qualifies'),
        width: integer('image width'),
        height: integer('image height'),
        role: { type: 'string', enum: ['reference', 'candidate'], description: 'backend role' },
        frames: { type: 'array', items: { type: 'integer' }, description: 'exact frame indices to select or request' },
        around: integer('include at most N neighbouring frames'),
        offset: integer('pagination offset'),
        limit: integer('bounded result count'),
        full: boolean('return raw observation records instead of the compact view'),
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
        background: boolean('run on the reusable BetterDisplay virtual screen'),
        display_name: string('BetterDisplay virtual screen name'),
      },
      ['scenario', 'run_root', 'ledger'],
      safe('eu4_diff_run'),
    ),
    tool(
      'eu4_diff_inspect',
      'Inspect a session or campaign root: compact stop/health/correspondence/classification summary, first discrepancy, missing evidence, next step, and optional temporal frame diagnostics.',
      {
        run: string('session directory or campaign root'),
        build_case: boolean('build the self-contained diagnostic case'),
        full: boolean('include the raw recorded comparison and first pair'),
        limit: integer('bounded session count for a campaign root'),
        offset: integer('pagination offset for a campaign root'),
        temporal: boolean('compare preserved consecutive and cross-process frames'),
        reference_frame: integer('reference frame index for temporal analysis'),
        candidate_frame: integer('candidate frame index for temporal analysis'),
        temporal_window: integer('consecutive frames to include from each index'),
        temporal_artifacts: boolean('write bounded difference PNGs'),
        temporal_artifact_dir: string('artifact directory for temporal PNGs'),
      },
      ['run'],
      safe('eu4_diff_inspect'),
    ),
    tool(
      'eu4_replay',
      'Run a stored reproduction or a bounded M7 same-session sequence: packaged v1/v2 case ' +
        'comparison, case verification, diagnosis of already-produced Metal results, or ordered ' +
        'sequence execution. This is not the two-process eu4_diff_run workflow.',
      {
        op: { type: 'string', enum: ['case', 'frame', 'independent', 'sequence', 'diagnose', 'verify'], description: 'replay/verification kind' },
        case: string('case directory (case/verify)'),
        frame: string('frame trace directory (frame/diagnose)'),
        frame_dirs: { type: 'array', items: { type: 'string' }, description: 'ordered M7 frame directories (sequence)' },
        metal: string('already-produced Metal result directory (diagnose)'),
        shaders: string('shader directory'),
        replay_out: string('replay/diagnosis output directory'),
        out: string('replay output directory (legacy alias)'),
        policy: string('comparison/state policy JSON'),
        rationale: string('JSON file path or inline JSON justifying an expensive replay: question, evidence, gap, hypothesis, action, expected_information, cheaper_alternatives_checked'),
        require_seal: boolean('require an authoritative frame seal for diagnosis'),
        max_frames: integer('bounded M7 sequence length'),
        max_bytes: integer('bounded captured-sequence input budget'),
        case_max_bytes: integer('bounded reproduction-case budget'),
        max_seconds: number('bounded M7 wall-clock budget'),
        frame_timeout: number('bounded M7 per-frame deadline'),
        timeout: number('bounded M7 overall deadline'),
        continue_on_difference: boolean('keep executing after a nonqualified M7 frame'),
        package_dir: string('explicit reproduction case destination'),
        only_event: integer('selected event index'),
        full: boolean('return the complete report instead of the bounded agent projection'),
        disable_render_target_vflip: boolean('controlled divergence experiment: disable render-target V flip'),
        sampler_shift: integer('controlled sampler-unit shift for the historical defect'),
        sampler_shift_min: integer('first texN unit affected by the shift'),
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
  // Resolve the interpreter once, at mount, to an absolute path. A launcher
  // that did not set EU4_DSH_PYTHON leaves every tool with an actionable
  // failure instead of silently spawning whichever Python is first on PATH.
  const cwd = config.infra || process.cwd()
  let python = ''
  let pythonError = ''
  try {
    python = resolvePython(config.python)
  } catch (error) {
    pythonError = error.message || String(error)
  }
  if (pythonError) {
    ctx.logger?.warn?.(`[eu4-tools] ${pythonError}`)
  } else {
    ctx.logger?.info?.(`[eu4-tools] Python core: ${python}`)
  }
  const run = config.runner || ((toolName, args) => {
    if (pythonError) return Promise.reject(new Error(pythonError))
    return runTool(toolName, args, {
      python,
      cwd,
      env: config.env,
      timeoutMs: config.timeoutMs,
    })
  })
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

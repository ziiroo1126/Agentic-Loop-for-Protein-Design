// The only execution boundary is the host's registered bash tool. Keep this
// module dependency-free so its transport contract can be tested offline.
import { isAbsolute, resolve } from 'node:path';

export class ALPDToolError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'ALPDToolError';
    this.code = code;
  }
}

function fail(code, message) {
  throw new ALPDToolError(code, message);
}

function record(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function exactKeys(value, allowed, required, label) {
  if (!record(value)
    || Object.keys(value).some((key) => !allowed.includes(key))
    || required.some((key) => !Object.hasOwn(value, key))) {
    fail('INVALID_ARGUMENTS', `${label} must be an object containing only the documented fields, including all required fields.`);
  }
}

function pathString(value, label) {
  if (typeof value !== 'string' || value.trim().length === 0 || value.includes('\0')) {
    fail('INVALID_ARGUMENTS', `${label} must be a nonempty path without NUL characters.`);
  }
  return value;
}

function positive(value, label) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) {
    fail('INVALID_ARGUMENTS', `${label} must be a finite positive number.`);
  }
  return value;
}

function integer(value, min, max, label) {
  if (!Number.isInteger(value) || value < min || value > max) {
    fail('INVALID_ARGUMENTS', `${label} must be an integer from ${min} to ${max}.`);
  }
  return value;
}

export function resolveConfig(config = {}, env = process.env) {
  exactKeys(config, ['projectRoot', 'cli', 'timeoutMs'], [], 'ALPD configuration');
  const projectRoot = pathString(config.projectRoot === undefined ? env.ALPD_PROJECT_ROOT : config.projectRoot, 'projectRoot or ALPD_PROJECT_ROOT');
  if (!isAbsolute(projectRoot)) {
    fail('INVALID_CONFIGURATION', 'projectRoot or ALPD_PROJECT_ROOT must be an absolute ALPD checkout path.');
  }
  const workdir = resolve(projectRoot, 'interaction-design-mvp');
  const cli = resolve(workdir, pathString(config.cli === undefined ? '.venv/bin/alpd' : config.cli, 'cli'));
  const timeoutMs = config.timeoutMs === undefined ? undefined : positive(config.timeoutMs, 'timeoutMs');
  return Object.freeze({ projectRoot: resolve(projectRoot), workdir, cli, timeoutMs });
}

/** Quote a single POSIX shell argument, including embedded apostrophes. */
export function shellQuote(value) {
  if (typeof value !== 'string' || value.includes('\0')) {
    fail('INVALID_ARGUMENTS', 'Shell arguments must be strings without NUL characters.');
  }
  return `'${value.replaceAll("'", "'\\''")}'`;
}

const evidenceFields = [
  'pre.monomer_plddt', 'pre.monomer_design_rmsd',
  'pre.generated_hotspot_coverage', 'pre.generated_clash_residue_pairs',
  'post.iptm', 'post.hotspot_coverage', 'post.clash_residue_pairs',
  'post.binder_rmsd_after_target_alignment',
];

function checkSubmission(submission) {
  exactKeys(submission, ['request_sha256', 'decision', 'actor'], ['request_sha256', 'decision', 'actor'], 'submission');
  if (typeof submission.request_sha256 !== 'string' || !/^[0-9a-f]{64}$/.test(submission.request_sha256)) {
    fail('INVALID_ARGUMENTS', 'submission.request_sha256 must be the lowercase SHA-256 from the latest observe response.');
  }
  const { decision, actor } = submission;
  exactKeys(decision, ['action', 'candidate_ids', 'reason', 'evidence'], ['action', 'candidate_ids', 'reason', 'evidence'], 'submission.decision');
  if (!['evaluate', 'stop'].includes(decision.action)
    || !Array.isArray(decision.candidate_ids) || decision.candidate_ids.some((id) => typeof id !== 'string')
    || typeof decision.reason !== 'string' || [...decision.reason].length < 1 || [...decision.reason].length > 1200
    || !Array.isArray(decision.evidence)) {
    fail('INVALID_ARGUMENTS', 'decision requires evaluate or stop, string candidate_ids, a reason of 1–1200 characters, and an evidence array.');
  }
  for (const item of decision.evidence) {
    exactKeys(item, ['candidate_id', 'field', 'value'], ['candidate_id', 'field', 'value'], 'decision.evidence item');
    if (typeof item.candidate_id !== 'string' || !evidenceFields.includes(item.field)
      || typeof item.value !== 'number' || !Number.isFinite(item.value)) {
      fail('INVALID_ARGUMENTS', 'Evidence requires a candidate_id, a documented pre/post field, and a finite numeric value from visible state.');
    }
  }
  exactKeys(actor, ['harness', 'model', 'agent_id'], ['harness', 'model', 'agent_id'], 'submission.actor');
  if (typeof actor.harness !== 'string' || [...actor.harness].length < 1 || [...actor.harness].length > 120
    || (actor.model !== null && typeof actor.model !== 'string')
    || (actor.agent_id !== null && typeof actor.agent_id !== 'string')) {
    fail('INVALID_ARGUMENTS', 'actor requires harness (1–120 characters), model (string or null), and agent_id (string or null); use null for unknown metadata.');
  }
}

function interrupted(exec) {
  if (exec.signal.aborted) fail('ABORTED', 'ALPD call was cancelled. Inspect the session before submitting further work.');
}

// Whitelist actionable summaries; never reflect shell commands, raw submission
// values, stderr, or path-bearing exception messages into this tool's errors.
function cliFailure(value) {
  if (value.sandbox?.denied || value.sandbox?.runnerFailed) {
    return 'The host sandbox denied or could not run ALPD. Review the host policy result before continuing.';
  }
  const diagnostic = typeof value.stderr?.text === 'string' ? value.stderr.text : '';
  if (/stale or missing observe request|submission request identity mismatch/.test(diagnostic)) {
    return 'The submission does not match the current request. Observe the session and use its current request_sha256.';
  }
  if (/unfinished intent|failed or running step|completed receipt lacks/.test(diagnostic)) {
    return 'The session contains unfinished or inconsistent work. Inspect its preserved artifacts before continuing; automatic retry is disabled.';
  }
  if (/screening source changed|screening implementation changed/.test(diagnostic)) {
    return 'The screening implementation or source inputs changed. Prepare a new session.';
  }
  if (value.exitCode === 126 || value.exitCode === 127 || /No such file or directory|Permission denied/.test(diagnostic)) {
    return 'Check projectRoot, cli, input paths, executable permissions, and the ALPD virtual environment in the host bash result.';
  }
  return 'Check the host bash result for CLI validation or runtime diagnostics. Inspect the session before deciding whether to submit further work.';
}

async function runCli(ctx, config, exec, action, command) {
  interrupted(exec);
  let result;
  try {
    result = await ctx.tools.execute({
      callId: `${exec.callId}:alpd:${action}:bash`,
      rootCallId: exec.rootCallId,
      name: 'bash',
      arguments: {
        command,
        description: {
          prepare: 'Prepare a bounded ALPD screening session',
          observe: 'Read the current ALPD screening observation',
          apply: 'Apply an evidence based ALPD screening decision',
          pipeline_prepare: 'Validate and prepare a ALPD design pipeline',
          pipeline_run: 'Generate candidates, evaluate monomers and start bounded complex screening',
          pipeline_observe: 'Read the current ALPD pipeline status and visible screening request',
          pipeline_apply: 'Apply a visible-evidence decision to the ALPD design pipeline',
          pipeline_export: 'Export a completed ALPD pipeline result bundle',
        }[action],
        workdir: config.workdir,
        run_in_background: false,
        ...(config.timeoutMs === undefined ? {} : { timeoutMs: config.timeoutMs }),
      },
      agent: exec.agent,
      parent: exec.token,
      signal: exec.signal,
    });
  } catch {
    interrupted(exec);
    fail('HOST_EXECUTION_FAILED', 'The host bash dispatch failed. Check the host tool configuration and policy diagnostics; this call was not retried.');
  }
  for (const context of result?.additionalContexts ?? []) exec.deferContext(context);
  interrupted(exec);
  if (result?.isError !== false) {
    const code = result?.error?.info?.code;
    if (code === 'UNKNOWN_TOOL') fail('HOST_EXECUTION_FAILED', 'The host bash tool is unavailable. Enable the standard DeepSeek Harness bash tool for ALPD.');
    if (code === 'ABORTED' || code === 'ABORTED_BEFORE_DISPATCH') {
      fail('ABORTED', 'The host cancelled the ALPD call. Inspect the session before submitting further work.');
    }
    fail('HOST_EXECUTION_FAILED', 'The host bash tool rejected or failed the ALPD call. Review its policy or execution diagnostics; this call was not retried.');
  }
  if (result.concludesTurn === true) exec.concludeTurn();
  const value = result.value;
  if (!record(value) || value.kind !== 'foreground') {
    fail('INVALID_HOST_RESULT', 'ALPD requires a completed foreground bash result. Inspect any returned background job before continuing.');
  }
  if (value.aborted === true) fail('ABORTED', 'The host aborted ALPD. Inspect preserved session work before continuing.');
  if (value.timedOut === true) fail('TIMED_OUT', 'ALPD exceeded the host bash timeout. Inspect preserved session work and the configured host timeout before continuing.');
  if (value.aborted !== false || value.timedOut !== false || value.signal !== null) {
    fail('INVALID_HOST_RESULT', 'ALPD did not finish normally or the host result lacks completion metadata. Inspect the host bash result.');
  }
  if (value.exitCode !== 0 || value.sandbox?.denied || value.sandbox?.runnerFailed) {
    const status = Number.isInteger(value.exitCode) ? ` (exit code ${value.exitCode})` : '';
    fail('CLI_FAILED', `ALPD ${action} failed${status}. ${cliFailure(value)}`);
  }
  if (!record(value.stdout) || typeof value.stdout.text !== 'string' || value.stdout.truncated !== false) {
    fail('INCOMPLETE_STDOUT', 'ALPD stdout is missing or truncated. Inspect the full host output and increase its output limit before continuing.');
  }
  return value.stdout.text;
}

function parseOutput(stdout) {
  let value;
  try { value = JSON.parse(stdout); } catch {
    fail('INVALID_CLI_OUTPUT', 'ALPD did not return complete JSON. Check the CLI version and host bash diagnostics.');
  }
  const pending = [value];
  while (pending.length) {
    const item = pending.pop();
    if (typeof item === 'number' && !Number.isFinite(item)) {
      fail('INVALID_CLI_OUTPUT', 'ALPD returned a non-finite JSON number. Inspect the CLI output.');
    }
    if (item !== null && typeof item === 'object') {
      for (const child of Object.values(item)) pending.push(child);
    }
  }
  if (!record(value)) fail('INVALID_CLI_OUTPUT', 'ALPD must return a JSON object. Check the CLI version.');
  return value;
}

function pipelineReceipt(stdout, status, paths) {
  const value = parseOutput(stdout);
  if (value.status !== status || paths.some((key) => typeof value[key] !== 'string'
    || !isAbsolute(value[key]) || /[\r\n\0]/.test(value[key]))) {
    fail('INVALID_CLI_OUTPUT', `ALPD pipeline ${status} must return its documented status and absolute directory paths. Check the CLI version and host bash diagnostics.`);
  }
  return value;
}

const requiredString = (description) => ({ type: 'string', required: true, description });
const nullableString = (description) => ({ oneOf: [{ type: 'string' }, { type: 'null' }], required: true, description });
const submissionSchema = {
  type: 'object', required: true, additionalProperties: false,
  description: 'Complete response to the current observe request. Preserve truthful actor provenance; never invent model or agent versions.',
  properties: {
    request_sha256: requiredString('Exact request_sha256 from the latest observe response.'),
    decision: {
      type: 'object', required: true, additionalProperties: false,
      properties: {
        action: { type: 'string', enum: ['evaluate', 'stop'], required: true },
        candidate_ids: { type: 'array', items: { type: 'string' }, required: true },
        reason: requiredString('Evidence-grounded explanation of 1–1200 characters.'),
        evidence: {
          type: 'array', required: true,
          items: {
            type: 'object', additionalProperties: false,
            properties: {
              candidate_id: requiredString('Candidate whose visible measurement supports the decision.'),
              field: { type: 'string', enum: evidenceFields, required: true },
              value: { type: 'number', required: true },
            },
          },
        },
      },
    },
    actor: {
      type: 'object', required: true, additionalProperties: false,
      properties: {
        harness: requiredString('Actual host harness identity, normally deepseek-harness; 1–120 characters.'),
        model: nullableString('Actual model identifier if known; otherwise null.'),
        agent_id: nullableString('Actual agent identifier if known; otherwise null.'),
      },
    },
  },
};

const output = {
  schema: { type: 'json' },
  render: (_args, value) => [{ type: 'text', text: JSON.stringify(value) }],
};

/** Return official defineTool options without importing the host runtime. */
export function createToolOptions(ctx, config) {
  const absoluteInput = (value, label) => resolve(config.workdir, pathString(value, label));
  const command = (args) => [config.cli, ...args].map(shellQuote).join(' ');
  return [
    {
      name: 'alpd_screen_prepare',
      description: 'Prepare bounded candidate screening controlled by this harness. Supply a monomer job and exactly one of saved feedback or a live complex configuration. Live configuration can launch new model inference. Continue with observe, then submit an evidence-grounded decision using apply.',
      parameters: {
        monomer_job: requiredString('Monomer job directory; relative paths resolve from interaction-design-mvp.'),
        feedback: { type: 'string', description: 'Saved feedback directory for replay; excludes complex_config and tool_wall_budget_seconds.' },
        complex_config: { type: 'string', description: 'Live complex runtime configuration JSON path; excludes feedback.' },
        batch_size: { type: 'integer', default: 2, description: 'Maximum candidates per evaluation batch, from 1 to 32.' },
        max_evaluations: { type: 'integer', default: 8, description: 'Total candidate evaluation limit, from 1 to 256.' },
        tool_wall_budget_seconds: { type: 'number', description: 'Optional finite positive live evaluation wall-time budget; unavailable for replay.' },
      },
      output,
      async execute(args, exec) {
        exactKeys(args, ['monomer_job', 'feedback', 'complex_config', 'batch_size', 'max_evaluations', 'tool_wall_budget_seconds'], ['monomer_job'], 'prepare arguments');
        const replay = args.feedback !== undefined;
        if (replay === (args.complex_config !== undefined)) fail('INVALID_ARGUMENTS', 'Provide exactly one of feedback or complex_config.');
        const argv = ['screen', 'prepare', absoluteInput(args.monomer_job, 'monomer_job'), '--strategy', 'harness',
          replay ? '--feedback' : '--complex-config', absoluteInput(replay ? args.feedback : args.complex_config, replay ? 'feedback' : 'complex_config'),
          '--batch-size', String(integer(args.batch_size === undefined ? 2 : args.batch_size, 1, 32, 'batch_size')),
          '--max-evaluations', String(integer(args.max_evaluations === undefined ? 8 : args.max_evaluations, 1, 256, 'max_evaluations'))];
        if (args.tool_wall_budget_seconds !== undefined) {
          if (replay) fail('INVALID_ARGUMENTS', 'tool_wall_budget_seconds requires live complex_config; replay cannot simulate live tool time.');
          argv.push('--tool-wall-budget-seconds', String(positive(args.tool_wall_budget_seconds, 'tool_wall_budget_seconds')));
        }
        const stdout = await runCli(ctx, config, exec, 'prepare', command(argv));
        const sessionDir = stdout.replace(/\r?\n$/, '');
        if (!isAbsolute(sessionDir) || /[\r\n\0]/.test(sessionDir)) {
          fail('INVALID_CLI_OUTPUT', 'ALPD prepare must return one absolute session directory. Check the CLI version and host bash diagnostics.');
        }
        return { session_dir: sessionDir };
      },
    },
    {
      name: 'alpd_screen_observe',
      description: 'Read the current visible screening state, request_sha256, and response schema. Use only these observations for selection; do not inspect replay files or unselected complex outputs.',
      parameters: { session_dir: requiredString('Screening session directory returned by prepare.') },
      output,
      async execute(args, exec) {
        exactKeys(args, ['session_dir'], ['session_dir'], 'observe arguments');
        return parseOutput(await runCli(ctx, config, exec, 'observe', command(['screen', 'observe', absoluteInput(args.session_dir, 'session_dir')])));
      },
    },
    {
      name: 'alpd_screen_apply',
      description: 'Apply one complete proposal bound to the latest observe request. Evaluation may perform new inference in live mode. Include exact visible numeric evidence and truthful actor metadata; use null for unknown model or agent identity. The executor validates evidence and budgets. Inspect interrupted work before any further submission.',
      parameters: {
        session_dir: requiredString('Screening session directory returned by prepare.'),
        submission: submissionSchema,
      },
      output,
      async execute(args, exec) {
        exactKeys(args, ['session_dir', 'submission'], ['session_dir', 'submission'], 'apply arguments');
        const sessionDir = absoluteInput(args.session_dir, 'session_dir');
        checkSubmission(args.submission);
        const applyCommand = `printf '%s' ${shellQuote(JSON.stringify(args.submission))} | ${command(['screen', 'apply', sessionDir, '--decision', '-'])}`;
        return parseOutput(await runCli(ctx, config, exec, 'apply', applyCommand));
      },
    },
    {
      name: 'alpd_pipeline_prepare',
      description: 'Validate a user-specified protein binder task and prepare the full local ODesign → ESMFold v1 monomer → bounded ESMFold2 complex pipeline. TASK and runtime are JSON file paths, authored by the host from supplied biological requirements and available local assets. Preparation does not run models; run starts inference. The complex selection budget does not cap generation or monomer work.',
      parameters: {
        task: requiredString('InteractionDesignSpec JSON file; obtain target structure, chain mapping, hotspots and binder constraints from the user or supplied task.'),
        runtime: requiredString('Full local runtime JSON file with generation, monomer and complex sections; no LLM API credentials.'),
        strategy: { type: 'string', enum: ['harness', 'fixed', 'heuristic'], default: 'harness', description: 'Harness delegates complex selection to this host; fixed and heuristic are deterministic baselines.' },
        batch_size: { type: 'integer', default: 2, description: 'Maximum candidates per complex evaluation batch, from 1 to 32.' },
        max_evaluations: { type: 'integer', default: 8, description: 'Total complex candidate evaluation limit, from 1 to 256.' },
        tool_wall_budget_seconds: { type: 'number', description: 'Optional finite positive wall-time budget for complex screening only.' },
        artifacts: { type: 'string', description: 'Parent directory for a new pipeline; defaults to artifacts/pipelines under interaction-design-mvp.' },
      },
      output,
      async execute(args, exec) {
        exactKeys(args, ['task', 'runtime', 'strategy', 'batch_size', 'max_evaluations', 'tool_wall_budget_seconds', 'artifacts'], ['task', 'runtime'], 'pipeline prepare arguments');
        const strategy = args.strategy === undefined ? 'harness' : args.strategy;
        if (!['harness', 'fixed', 'heuristic'].includes(strategy)) fail('INVALID_ARGUMENTS', 'strategy must be harness, fixed or heuristic.');
        const argv = ['pipeline', 'prepare', absoluteInput(args.task, 'task'), '--runtime', absoluteInput(args.runtime, 'runtime'),
          '--strategy', strategy,
          '--batch-size', String(integer(args.batch_size === undefined ? 2 : args.batch_size, 1, 32, 'batch_size')),
          '--max-evaluations', String(integer(args.max_evaluations === undefined ? 8 : args.max_evaluations, 1, 256, 'max_evaluations'))];
        if (args.tool_wall_budget_seconds !== undefined) argv.push('--tool-wall-budget-seconds', String(positive(args.tool_wall_budget_seconds, 'tool_wall_budget_seconds')));
        if (args.artifacts !== undefined) argv.push('--artifacts', absoluteInput(args.artifacts, 'artifacts'));
        return pipelineReceipt(await runCli(ctx, config, exec, 'pipeline_prepare', command(argv)), 'prepared', ['pipeline_dir']);
      },
    },
    ...['run', 'observe'].map((action) => ({
      name: `alpd_pipeline_${action}`,
      description: action === 'run'
        ? 'Run prepared ODesign generation and ESMFold v1 monomer checks, then prepare ESMFold2 complex screening. Harness mode returns awaiting_selection and a request for this host; fixed/heuristic modes finish their bounded screening. Existing finished stages are preserved. Inspect interrupted work before continuing.'
        : 'Read a prepared, pending or completed pipeline. When awaiting_selection, use only request.payload.visible_state and the full request.response_schema for selection. The request hash comes from request.request_sha256. Do not inspect unselected complex outputs.',
      parameters: { pipeline_dir: requiredString('Absolute pipeline directory returned by pipeline prepare, or its path relative to interaction-design-mvp.') },
      output,
      async execute(args, exec) {
        exactKeys(args, ['pipeline_dir'], ['pipeline_dir'], `pipeline ${action} arguments`);
        return parseOutput(await runCli(ctx, config, exec, `pipeline_${action}`, command(['pipeline', action, absoluteInput(args.pipeline_dir, 'pipeline_dir')])));
      },
    })),
    {
      name: 'alpd_pipeline_apply',
      description: 'Apply one full Submission to a pending pipeline. Use request.request_sha256 from its latest run/observe/apply response, exact visible numerical evidence and truthful actor metadata. This may run selected ESMFold2 complex inference. Returns the next pipeline state, including the next request or completed report.',
      parameters: {
        pipeline_dir: requiredString('Pipeline directory returned by pipeline prepare.'),
        submission: submissionSchema,
      },
      output,
      async execute(args, exec) {
        exactKeys(args, ['pipeline_dir', 'submission'], ['pipeline_dir', 'submission'], 'pipeline apply arguments');
        const pipelineDir = absoluteInput(args.pipeline_dir, 'pipeline_dir');
        checkSubmission(args.submission);
        const applyCommand = `printf '%s' ${shellQuote(JSON.stringify(args.submission))} | ${command(['pipeline', 'apply', pipelineDir, '--decision', '-'])}`;
        return parseOutput(await runCli(ctx, config, exec, 'pipeline_apply', applyCommand));
      },
    },
    {
      name: 'alpd_pipeline_export',
      description: 'Export a completed pipeline to a local reviewable result bundle. Supply an output directory and report the returned export_dir. The bundle contains computational diagnostics; it does not establish experimental binding.',
      parameters: {
        pipeline_dir: requiredString('Completed pipeline directory.'),
        output: requiredString('Local destination directory for the result bundle; relative paths resolve from interaction-design-mvp.'),
      },
      output,
      async execute(args, exec) {
        exactKeys(args, ['pipeline_dir', 'output'], ['pipeline_dir', 'output'], 'pipeline export arguments');
        return pipelineReceipt(await runCli(ctx, config, exec, 'pipeline_export', command(['pipeline', 'export', absoluteInput(args.pipeline_dir, 'pipeline_dir'), '--output', absoluteInput(args.output, 'output')])), 'exported', ['pipeline_dir', 'export_dir']);
      },
    },
    {
      name: 'alpd_help',
      description: 'Explain the full ALPD task → prepare → run → select → export workflow, existing screening tools, and actor provenance requirements.',
      parameters: {},
      output,
      async execute(args) {
        exactKeys(args, [], [], 'help arguments');
        return {
          workflow: [
            'Author and validate an InteractionDesignSpec from user-supplied biological requirements. Supply the task and full local generation/monomer/complex runtime JSON paths to pipeline prepare.',
            'Run the prepared pipeline to generate its seed pool with ODesign, perform ESMFold v1 monomer checks and prepare ESMFold2 complex screening. Harness mode returns a request; fixed/heuristic modes finish automatically.',
            'For awaiting_selection, use only request.payload.visible_state and request.response_schema. Apply the full submission with request.request_sha256, exact visible numeric evidence and truthful actor provenance until completed.',
            'Export the completed pipeline to a local result bundle and report diagnostic results and budgets. Inspect preserved work after any interruption or failure.',
          ],
          screening: 'Existing screen prepare/observe/apply tools remain available for a completed monomer job and exactly one saved feedback source or live complex runtime. Screen apply requires a subsequent observe to obtain the next request; pipeline apply already returns the next pipeline state.',
          budget: 'Pipeline max_evaluations, batch_size and tool_wall_budget_seconds govern complex screening only. Generation uses the task seed pool and monomer checks cover generated candidates. AF3 is not used by this pipeline.',
          provenance: 'Set actor.harness to the actual host, normally deepseek-harness. Preserve known model and agent identifiers and use null for unknown values. Actor metadata is recorded as a declaration, not independently verified.',
          execution: 'Commands run through the host bash tool in the configured interaction-design-mvp directory. Host approval, cancellation, output, and timeout policies apply. Relative input paths resolve from that directory.',
        };
      },
    },
  ];
}

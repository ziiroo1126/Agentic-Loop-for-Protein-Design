import assert from 'node:assert/strict';
import test from 'node:test';
import { createToolOptions, MolClawToolError, resolveConfig, shellQuote } from './bridge.mjs';

const projectRoot = '/project/MolClaw';
const workdir = `${projectRoot}/interaction-design-mvp`;
const cli = `${workdir}/.venv/bin/interaction-design`;
const submission = () => ({
  request_sha256: 'a'.repeat(64),
  decision: {
    action: 'evaluate', candidate_ids: ['candidate-1'], reason: 'Visible monomer confidence supports evaluation.',
    evidence: [{ candidate_id: 'candidate-1', field: 'pre.monomer_plddt', value: 88.5 }],
  },
  actor: { harness: 'deepseek-harness', model: null, agent_id: null },
});
const completed = (stdout = '{}', overrides = {}) => ({
  isError: false,
  value: {
    kind: 'foreground', exitCode: 0, signal: null, timedOut: false, aborted: false, timeoutMs: 120000,
    stdout: { text: stdout, truncated: false }, stderr: { text: '', truncated: false }, ...overrides,
  },
  content: [{ type: 'text', text: 'Rendered text is not the canonical output.' }],
});

function fixture(response = completed(), config = {}) {
  const calls = [];
  const contexts = [];
  let concludes = 0;
  const controller = new AbortController();
  const exec = {
    callId: 'outer-call', rootCallId: 'model-root', token: Symbol('parent'),
    agent: { id: 'actual-agent' }, signal: controller.signal,
    deferContext: (context) => contexts.push(context), concludeTurn: () => { concludes += 1; },
  };
  const ctx = { tools: { execute: async (call) => {
    calls.push(call);
    return typeof response === 'function' ? response(call) : response;
  } } };
  const tools = Object.fromEntries(createToolOptions(ctx, resolveConfig({ projectRoot, ...config }))
    .map((tool) => [tool.name.replace('molclaw_screen_', ''), tool]));
  return { calls, contexts, controller, exec, tools, concludes: () => concludes };
}

async function rejectsCode(promise, code) {
  await assert.rejects(promise, (error) => error instanceof MolClawToolError && error.code === code);
}

test('configuration uses explicit root before environment and resolves executable paths', () => {
  assert.deepEqual(resolveConfig({}, { MOLCLAW_PROJECT_ROOT: '/env/root/' }), {
    projectRoot: '/env/root', workdir: '/env/root/interaction-design-mvp',
    cli: '/env/root/interaction-design-mvp/.venv/bin/interaction-design', timeoutMs: undefined,
  });
  assert.equal(resolveConfig({ projectRoot, cli: '../other/bin/tool' }, { MOLCLAW_PROJECT_ROOT: '/ignored' }).cli,
    '/project/MolClaw/other/bin/tool');
  assert.equal(resolveConfig({ projectRoot, cli: '/custom/cli', timeoutMs: 45000 }).cli, '/custom/cli');
});

test('configuration fails early for missing or relative roots, invalid paths, raw commands and bad timeouts', () => {
  for (const config of [
    {}, { projectRoot: 'relative' }, { projectRoot: null }, { projectRoot: '' },
    { projectRoot: '/tmp\0secret' }, { projectRoot, cli: null }, { projectRoot, cli: '' },
    { projectRoot, command: 'rm something' }, ...[0, -1, NaN, Infinity, '500', null].map((timeoutMs) => ({ projectRoot, timeoutMs })),
  ]) assert.throws(() => resolveConfig(config, {}), MolClawToolError);
});

test('POSIX quoting covers apostrophes, shell substitutions, newlines and empty strings', () => {
  assert.equal(shellQuote(''), "''");
  assert.equal(shellQuote("one'two"), "'one'\\''two'");
  assert.equal(shellQuote('$(touch /tmp/marker); `id`\n$HOME'), "'$(touch /tmp/marker); `id`\n$HOME'");
  assert.throws(() => shellQuote('a\0b'), MolClawToolError);
});

test('prepare fixes harness strategy and passes exact CLI options with defaults', async () => {
  const { tools, calls, exec } = fixture(completed('/artifact/session-1\n'));
  assert.deepEqual(await tools.prepare.execute({ monomer_job: 'artifacts/monomer', feedback: '/saved/feedback' }, exec),
    { session_dir: '/artifact/session-1' });
  assert.equal(calls[0].arguments.command,
    `'${cli}' 'screen' 'prepare' '${workdir}/artifacts/monomer' '--strategy' 'harness' '--feedback' '/saved/feedback' '--batch-size' '2' '--max-evaluations' '8'`);
});

test('live prepare forwards bounded settings and optional host timeout', async () => {
  const { tools, calls, exec } = fixture(completed('/artifact/session\n'), { timeoutMs: 90000 });
  await tools.prepare.execute({ monomer_job: '/monomer', complex_config: 'config/live.json', batch_size: 32,
    max_evaluations: 256, tool_wall_budget_seconds: 15.5 }, exec);
  assert.equal(calls[0].arguments.command,
    `'${cli}' 'screen' 'prepare' '/monomer' '--strategy' 'harness' '--complex-config' '${workdir}/config/live.json' '--batch-size' '32' '--max-evaluations' '256' '--tool-wall-budget-seconds' '15.5'`);
  assert.equal(calls[0].arguments.timeoutMs, 90000);
});

test('observe returns canonical CLI JSON and quotes injectable paths including leading option text', async () => {
  const payload = { request_sha256: 'b'.repeat(64), payload: { visible_state: { remaining: 4 } } };
  const { tools, calls, exec } = fixture(completed(JSON.stringify(payload)));
  assert.deepEqual(await tools.observe.execute({ session_dir: "--session'; $(touch /tmp/injected)\n`id`" }, exec), payload);
  assert.equal(calls[0].arguments.command,
    `'${cli}' 'screen' 'observe' '${workdir}/--session'\\''; $(touch /tmp/injected)\n\`id\`'`);
});

test('apply sends complete submission over stdin with exact actor provenance and safe quoting', async () => {
  const proposal = submission();
  proposal.decision.reason = "Use candidate's visible value; $(touch /tmp/injected) `id`\n$HOME";
  proposal.actor = { harness: 'actual-dsh-host', model: 'model-from-host', agent_id: null };
  const { tools, calls, exec } = fixture(completed('{"status":"evaluated","receipt":{"actor_metadata_verified":false}}'));
  assert.deepEqual(await tools.apply.execute({ session_dir: '/screen/session', submission: proposal }, exec),
    { status: 'evaluated', receipt: { actor_metadata_verified: false } });
  const json = JSON.stringify(proposal);
  assert.equal(calls[0].arguments.command,
    `printf '%s' '${json.replaceAll("'", "'\\''")}' | '${cli}' 'screen' 'apply' '/screen/session' '--decision' '-'`);
  assert.deepEqual(proposal.actor, { harness: 'actual-dsh-host', model: 'model-from-host', agent_id: null });
  assert.equal(calls.length, 1);
});

test('all execution identity, cancellation, foreground mode and contexts cross the host boundary', async () => {
  const additionalContexts = [{ role: 'user', source: 'policy', content: [{ type: 'text', text: 'Context instruction.' }] }];
  const { tools, calls, exec, contexts, concludes } = fixture({ ...completed(), additionalContexts, concludesTurn: true });
  await tools.observe.execute({ session_dir: '/session' }, exec);
  assert.equal(calls[0].callId, 'outer-call:molclaw:observe:bash');
  assert.equal(calls[0].rootCallId, exec.rootCallId);
  assert.equal(calls[0].parent, exec.token);
  assert.equal(calls[0].agent, exec.agent);
  assert.equal(calls[0].signal, exec.signal);
  assert.equal(calls[0].name, 'bash');
  assert.equal(calls[0].arguments.workdir, workdir);
  assert.equal(calls[0].arguments.run_in_background, false);
  assert.equal(Object.hasOwn(calls[0].arguments, 'timeoutMs'), false);
  assert.deepEqual(contexts, additionalContexts);
  assert.equal(concludes(), 1);
});

test('host policy failures preserve contexts but redact raw messages and do not retry', async () => {
  const f = fixture({ isError: true, error: { message: '/secret/path command SUBMISSION_SECRET', info: { code: 'DENIED', name: 'Policy' } },
    content: [{ type: 'text', text: '/secret/path' }], additionalContexts: [{ source: 'policy' }] });
  await assert.rejects(f.tools.observe.execute({ session_dir: '/session' }, f.exec), (error) => {
    assert.equal(error.code, 'HOST_EXECUTION_FAILED');
    assert.doesNotMatch(error.message, /secret|SUBMISSION/);
    return true;
  });
  assert.deepEqual(f.contexts, [{ source: 'policy' }]);
  assert.equal(f.concludes(), 0);
  assert.equal(f.calls.length, 1);
});

test('thrown host exceptions are redacted and not retried', async () => {
  const f = fixture(() => { throw new Error('command with /private/token and submission'); });
  await assert.rejects(f.tools.observe.execute({ session_dir: '/session' }, f.exec), (error) => {
    assert.equal(error.code, 'HOST_EXECUTION_FAILED');
    assert.doesNotMatch(error.message, /private|token|submission/);
    return true;
  });
  assert.equal(f.calls.length, 1);
});

test('pre-cancelled execution never dispatches; cancellation while waiting reaches host', async () => {
  const before = fixture();
  before.controller.abort(new Error('secret reason'));
  await rejectsCode(before.tools.observe.execute({ session_dir: '/session' }, before.exec), 'ABORTED');
  assert.equal(before.calls.length, 0);
  let observedAbort = false;
  const during = fixture((call) => new Promise((resolve) => {
    call.signal.addEventListener('abort', () => { observedAbort = true; resolve(completed()); }, { once: true });
  }));
  const pending = during.tools.observe.execute({ session_dir: '/session' }, during.exec);
  during.controller.abort();
  await rejectsCode(pending, 'ABORTED');
  assert.equal(observedAbort, true);
  assert.equal(during.calls.length, 1);
});

test('background, malformed, interrupted, sandbox-denied and truncated host outcomes fail closed', async () => {
  const outcomes = [
    [completed('{}', { kind: 'background', jobId: 'job-1' }), 'INVALID_HOST_RESULT'],
    [completed('{}', { kind: 'completed' }), 'INVALID_HOST_RESULT'],
    [{ isError: false, content: [] }, 'INVALID_HOST_RESULT'],
    [completed('{}', { exitCode: 1 }), 'CLI_FAILED'],
    [completed('{}', { exitCode: null }), 'CLI_FAILED'],
    [completed('{}', { aborted: true }), 'ABORTED'],
    [completed('{}', { timedOut: true }), 'TIMED_OUT'],
    [completed('{}', { signal: 'SIGTERM' }), 'INVALID_HOST_RESULT'],
    [completed('{}', { aborted: undefined }), 'INVALID_HOST_RESULT'],
    [completed('{}', { stdout: { text: '{}', truncated: true, spillPath: '/private/spill' } }), 'INCOMPLETE_STDOUT'],
    [completed('{}', { stdout: { text: '{}' } }), 'INCOMPLETE_STDOUT'],
    [completed('{}', { sandbox: { denied: true, mode: 'workspace' } }), 'CLI_FAILED'],
    [completed('{}', { sandbox: { denied: false, runnerFailed: true, mode: 'workspace' } }), 'CLI_FAILED'],
  ];
  for (const [outcome, code] of outcomes) {
    const f = fixture(outcome);
    await rejectsCode(f.tools.observe.execute({ session_dir: '/session' }, f.exec), code);
    assert.equal(f.calls.length, 1);
  }
});

test('known CLI failures give actionable summaries without echoing paths or submitted values', async () => {
  const f = fixture(completed('', { exitCode: 2,
    stderr: { text: 'stale or missing observe request /private/session SUBMISSION_SECRET', truncated: false } }));
  await assert.rejects(f.tools.observe.execute({ session_dir: '/session' }, f.exec), (error) => {
    assert.match(error.message, /Observe the session/);
    assert.doesNotMatch(error.message, /private|SUBMISSION_SECRET/);
    return true;
  });
});

test('malformed or incomplete CLI output is never presented as success', async () => {
  for (const stdout of ['traceback\n{}', '{"half":', 'null', '[]', '{"overflow":1e999}']) {
    const f = fixture(completed(stdout));
    await rejectsCode(f.tools.observe.execute({ session_dir: '/session' }, f.exec), 'INVALID_CLI_OUTPUT');
  }
  for (const stdout of ['', 'relative/session\n', '/absolute/session\nlog line\n']) {
    const f = fixture(completed(stdout));
    await rejectsCode(f.tools.prepare.execute({ monomer_job: '/job', feedback: '/feedback' }, f.exec), 'INVALID_CLI_OUTPUT');
  }
});

test('prepare rejects ambiguous backends, arbitrary commands and out-of-bound inputs before dispatch', async () => {
  const valid = { monomer_job: '/job', feedback: '/feedback' };
  const invalid = [
    { monomer_job: '/job' }, { ...valid, complex_config: '/runtime' }, { ...valid, strategy: 'fixed' },
    { ...valid, command: 'arbitrary' }, { ...valid, cli: '/other' }, { ...valid, workdir: '/other' },
    { ...valid, tool_wall_budget_seconds: 1 }, { ...valid, feedback: null },
    ...[0, 33, 2.5, '2', null].map((batch_size) => ({ ...valid, batch_size })),
    ...[0, 257, Infinity, null].map((max_evaluations) => ({ ...valid, max_evaluations })),
    ...[0, -1, NaN, Infinity, '3'].map((tool_wall_budget_seconds) => ({ monomer_job: '/job', complex_config: '/runtime', tool_wall_budget_seconds })),
  ];
  const f = fixture();
  for (const args of invalid) await rejectsCode(f.tools.prepare.execute(args, f.exec), 'INVALID_ARGUMENTS');
  assert.equal(f.calls.length, 0);
});

test('apply rejects incomplete or extra submission fields and invalid evidence/provenance before dispatch', async () => {
  const changes = [
    (s) => { delete s.actor; }, (s) => { s.request_sha256 = 'wrong'; }, (s) => { s.command = 'arbitrary'; },
    (s) => { delete s.actor.model; }, (s) => { s.actor.agent_id = 42; }, (s) => { s.actor.extra = true; },
    (s) => { s.actor.harness = ''; }, (s) => { s.decision.action = 'run'; }, (s) => { s.decision.reason = ''; },
    (s) => { s.decision.reason = 'x'.repeat(1201); }, (s) => { s.decision.candidate_ids = [1]; },
    (s) => { s.decision.evidence[0].value = Infinity; }, (s) => { s.decision.evidence[0].value = true; },
    (s) => { s.decision.evidence[0].field = 'secret.metric'; }, (s) => { s.decision.extra = {}; },
  ];
  const f = fixture();
  for (const change of changes) {
    const proposal = submission(); change(proposal);
    await rejectsCode(f.tools.apply.execute({ session_dir: '/session', submission: proposal }, f.exec), 'INVALID_ARGUMENTS');
  }
  await rejectsCode(f.tools.apply.execute({ session_dir: '/session', submission: submission(), command: 'arbitrary' }, f.exec), 'INVALID_ARGUMENTS');
  await rejectsCode(f.tools.observe.execute({ session_dir: '/session', command: 'arbitrary' }, f.exec), 'INVALID_ARGUMENTS');
  assert.equal(f.calls.length, 0);
});

test('help describes the workflow without spawning work or inventing actor identity', async () => {
  const f = fixture();
  const result = await f.tools.molclaw_help.execute({}, f.exec);
  assert.equal(result.workflow.length, 4);
  assert.match(result.provenance, /null for unknown/);
  assert.equal(f.calls.length, 0);
});

test('pipeline prepare passes full task/runtime paths and defaults without launching another command', async () => {
  const receipt = { pipeline_dir: '/artifact/pipeline-1', status: 'prepared' };
  const f = fixture(completed(JSON.stringify(receipt)));
  assert.deepEqual(await f.tools.molclaw_pipeline_prepare.execute({ task: 'tasks/binder.json', runtime: 'config/pipeline.json' }, f.exec), receipt);
  assert.equal(f.calls[0].arguments.command,
    `'${cli}' 'pipeline' 'prepare' '${workdir}/tasks/binder.json' '--runtime' '${workdir}/config/pipeline.json' '--strategy' 'harness' '--batch-size' '2' '--max-evaluations' '8'`);
  assert.equal(f.calls.length, 1);
});

test('pipeline prepare supports deterministic strategies, bounded options and safely quoted artifact paths', async () => {
  for (const strategy of ['fixed', 'heuristic']) {
    const f = fixture(completed('{"pipeline_dir":"/artifact/pipeline","status":"prepared"}'));
    await f.tools.molclaw_pipeline_prepare.execute({ task: '/task.json', runtime: '/runtime.json', strategy,
      batch_size: 32, max_evaluations: 256, tool_wall_budget_seconds: 17.25, artifacts: "exports'; $(id)\n`id`" }, f.exec);
    assert.equal(f.calls[0].arguments.command,
      `'${cli}' 'pipeline' 'prepare' '/task.json' '--runtime' '/runtime.json' '--strategy' '${strategy}' '--batch-size' '32' '--max-evaluations' '256' '--tool-wall-budget-seconds' '17.25' '--artifacts' '${workdir}/exports'\\''; $(id)\n\`id\`'`);
  }
});

test('pipeline run and observe preserve prepared, pending and completed state envelopes', async () => {
  for (const action of ['run', 'observe']) {
    for (const payload of [
      { pipeline_dir: '/pipeline', status: 'prepared' },
      { pipeline_dir: '/pipeline', status: 'awaiting_selection', screening_session: '/screen', request: {
        request_sha256: 'a'.repeat(64), payload: { visible_state: { remaining_evaluations: 2 } }, response_schema: {} } },
      { pipeline_dir: '/pipeline', status: 'completed', report: { evaluated_ids: ['candidate-1'] } },
    ]) {
      const f = fixture(completed(JSON.stringify(payload)), { timeoutMs: 60000 });
      assert.deepEqual(await f.tools[`molclaw_pipeline_${action}`].execute({ pipeline_dir: "--pipeline'; $(id)\n`id`" }, f.exec), payload);
      assert.equal(f.calls[0].arguments.command,
        `'${cli}' 'pipeline' '${action}' '${workdir}/--pipeline'\\''; $(id)\n\`id\`'`);
      assert.equal(f.calls[0].arguments.timeoutMs, 60000);
      assert.equal(f.calls.length, 1);
    }
  }
});

test('pipeline apply uses the existing full submission over stdin and returns the next state', async () => {
  const proposal = submission();
  proposal.decision.reason = "Candidate's value supports checking it; $(id) `id`\n$HOME";
  const payload = { pipeline_dir: '/pipeline', status: 'completed', report: { evaluated_ids: ['candidate-1'] } };
  const f = fixture(completed(JSON.stringify(payload)));
  assert.deepEqual(await f.tools.molclaw_pipeline_apply.execute({ pipeline_dir: '/pipeline', submission: proposal }, f.exec), payload);
  assert.equal(f.calls[0].arguments.command,
    `printf '%s' ${shellQuote(JSON.stringify(proposal))} | '${cli}' 'pipeline' 'apply' '/pipeline' '--decision' '-'`);
  assert.equal(f.calls.length, 1);
});

test('pipeline export quotes both directory arguments and returns the canonical export receipt', async () => {
  const receipt = { pipeline_dir: '/pipeline', export_dir: '/export', status: 'exported' };
  const f = fixture(completed(JSON.stringify(receipt)));
  assert.deepEqual(await f.tools.molclaw_pipeline_export.execute({ pipeline_dir: 'artifacts/run', output: "--export's $(id)\n`id`" }, f.exec), receipt);
  assert.equal(f.calls[0].arguments.command,
    `'${cli}' 'pipeline' 'export' '${workdir}/artifacts/run' '--output' '${workdir}/--export'\\''s $(id)\n\`id\`'`);
});

test('pipeline tools reject unknown keys, missing paths, invalid budgets and malformed submissions before dispatch', async () => {
  const f = fixture();
  const valid = { task: '/task.json', runtime: '/runtime.json' };
  const badPrepare = [
    {}, { task: '/task.json' }, { ...valid, task: null }, { ...valid, runtime: '' },
    { ...valid, strategy: 'custom' }, { ...valid, strategy: null }, { ...valid, feedback: '/replay' },
    { ...valid, command: 'arbitrary' }, { ...valid, artifacts: '/tmp\0invalid' },
    ...[0, 33, 2.5, '2', null].map((batch_size) => ({ ...valid, batch_size })),
    ...[0, 257, Infinity, null].map((max_evaluations) => ({ ...valid, max_evaluations })),
    ...[0, -1, NaN, Infinity, '3', null].map((tool_wall_budget_seconds) => ({ ...valid, tool_wall_budget_seconds })),
  ];
  for (const args of badPrepare) await rejectsCode(f.tools.molclaw_pipeline_prepare.execute(args, f.exec), 'INVALID_ARGUMENTS');
  for (const action of ['run', 'observe']) {
    for (const args of [{}, { pipeline_dir: null }, { pipeline_dir: '/pipeline', command: 'arbitrary' }]) {
      await rejectsCode(f.tools[`molclaw_pipeline_${action}`].execute(args, f.exec), 'INVALID_ARGUMENTS');
    }
  }
  const malformed = submission(); delete malformed.actor.model;
  await rejectsCode(f.tools.molclaw_pipeline_apply.execute({ pipeline_dir: '/pipeline', submission: malformed }, f.exec), 'INVALID_ARGUMENTS');
  await rejectsCode(f.tools.molclaw_pipeline_apply.execute({ pipeline_dir: '/pipeline', submission: submission(), command: 'arbitrary' }, f.exec), 'INVALID_ARGUMENTS');
  for (const args of [{ pipeline_dir: '/pipeline' }, { pipeline_dir: '/pipeline', output: null }, { pipeline_dir: '/pipeline', output: '/export', overwrite: true }]) {
    await rejectsCode(f.tools.molclaw_pipeline_export.execute(args, f.exec), 'INVALID_ARGUMENTS');
  }
  assert.equal(f.calls.length, 0);
});

test('pipeline prepare and export reject malformed JSON receipts and incomplete directory paths', async () => {
  for (const [action, args, good] of [
    ['prepare', { task: '/task', runtime: '/runtime' }, { pipeline_dir: '/pipeline', status: 'prepared' }],
    ['export', { pipeline_dir: '/pipeline', output: '/export' }, { pipeline_dir: '/pipeline', export_dir: '/export', status: 'exported' }],
  ]) {
    const invalid = ['{}', '[]', 'not json', JSON.stringify({ ...good, status: 'unexpected' }),
      JSON.stringify({ ...good, pipeline_dir: 'relative' }), JSON.stringify({ ...good, pipeline_dir: '/line\nbreak' })];
    if (action === 'export') invalid.push(JSON.stringify({ ...good, export_dir: null }));
    for (const stdout of invalid) {
      const f = fixture(completed(stdout));
      await rejectsCode(f.tools[`molclaw_pipeline_${action}`].execute(args, f.exec), 'INVALID_CLI_OUTPUT');
    }
  }
});

test('pipeline execution shares the host identity, cancellation and context boundary', async () => {
  const contexts = [{ source: 'policy', content: [{ type: 'text', text: 'Host context.' }] }];
  const f = fixture({ ...completed(), additionalContexts: contexts, concludesTurn: true });
  await f.tools.molclaw_pipeline_run.execute({ pipeline_dir: '/pipeline' }, f.exec);
  assert.equal(f.calls[0].callId, 'outer-call:molclaw:pipeline_run:bash');
  for (const key of ['rootCallId', 'agent', 'signal']) assert.equal(f.calls[0][key], f.exec[key]);
  assert.equal(f.calls[0].parent, f.exec.token);
  assert.equal(f.calls[0].arguments.run_in_background, false);
  assert.deepEqual(f.contexts, contexts);
  assert.equal(f.concludes(), 1);
  const cancelled = fixture(); cancelled.controller.abort();
  await rejectsCode(cancelled.tools.molclaw_pipeline_run.execute({ pipeline_dir: '/pipeline' }, cancelled.exec), 'ABORTED');
  assert.equal(cancelled.calls.length, 0);
  const interrupted = fixture(completed('{}', { timedOut: true }));
  await rejectsCode(interrupted.tools.molclaw_pipeline_apply.execute({ pipeline_dir: '/pipeline', submission: submission() }, interrupted.exec), 'TIMED_OUT');
  assert.equal(interrupted.calls.length, 1);
});

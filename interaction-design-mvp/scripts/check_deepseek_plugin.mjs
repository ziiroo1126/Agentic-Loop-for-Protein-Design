#!/usr/bin/env node
// Real published Cordis/ToolRuntime/Bash modules drive saved scientific results.
// No LLM provider, Web UI, profile installation, or new GPU inference is used.
import assert from 'node:assert/strict';
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { resolve, dirname } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: {
  runtime: { type: 'string' }, monomer: { type: 'string' }, feedback: { type: 'string' },
  output: { type: 'string' }, plugin: { type: 'string' },
} });
for (const key of ['runtime', 'monomer', 'feedback', 'output']) {
  if (!values[key]) throw new Error(`Missing --${key}`);
}
const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const requireRuntime = createRequire(resolve(values.runtime, 'package.json'));
const load = (name) => import(pathToFileURL(requireRuntime.resolve(`@deepseek-ai/${name}`)).href);
const { Context } = await load('cordis');
const { ToolRuntime, defineTool } = await load('dsh-tools');
const { SystemPrompt } = await load('dsh-system-prompt');
const { LocalSubprocessRuntime } = await load('dsh-subprocess-local');
const { LocalBashExecutor } = await load('dsh-bash-local');
const output = resolve(values.output);
await mkdir(output, { recursive: true });
const pluginPath = resolve(values.plugin ?? resolve(projectRoot, 'plugins/molclaw/deepseek/index.mjs'));
const plugin = await import(pathToFileURL(pluginPath).href);
const ctx = new Context();
const checks = [];
const dispatches = [];
let denyBash = false;
let nextCall = 0;
let sessionDir;
const save = (name, value) => writeFile(resolve(output, name), JSON.stringify(value, null, 2) + '\n');
const invoke = (name, args, signal = new AbortController().signal) => ctx.tools.execute({
  callId: `molclaw-dsh-check-${++nextCall}`, name, arguments: args, signal,
});
const success = async (name, args) => {
  const result = await invoke(name, args);
  assert.equal(result.isError, false, JSON.stringify(result));
  return result.value;
};

try {
  ctx.plugin(SystemPrompt, {});
  ctx.plugin(ToolRuntime, { mode: 'native' });
  ctx.plugin(LocalSubprocessRuntime);
  ctx.plugin(LocalBashExecutor, { maxOutputBytes: 1024 * 1024, timeoutMs: 120000 });
  ctx.plugin(await load('dsh-shell-env'), { dshHome: resolve(output, 'isolated-home') });
  ctx.plugin(await load('dsh-tool-bash'), {});
  const mounted = ctx.plugin(plugin, { projectRoot });
  await new Promise((done) => setImmediate(done));
  ctx.on('tools/pre-execute', (exec, next) => {
    dispatches.push({ name: exec.name, call_id: exec.callId, root_call_id: exec.rootCallId,
      nested: exec.parent !== undefined, blocked: denyBash && exec.name === 'bash' });
    return denyBash && exec.name === 'bash'
      ? { kind: 'deny', reason: 'Synthetic host policy denial for integration verification' }
      : next();
  });
  const help = await success('molclaw_help', {});
  assert.equal(help.workflow.length, 4);
  checks.push('Cordis mounts native plugin and ToolRuntime validates help output');

  denyBash = true;
  assert.equal((await invoke('molclaw_screen_observe', { session_dir: '/unexecuted' })).isError, true);
  denyBash = false;
  checks.push('nested bash honors host pre-execution denial');
  const cancelled = new AbortController();
  cancelled.abort();
  const beforeCancellation = dispatches.length;
  assert.equal((await invoke('molclaw_screen_observe', { session_dir: '/unexecuted' }, cancelled.signal)).isError, true);
  assert.equal(dispatches.length, beforeCancellation);
  checks.push('already cancelled call never reaches bash');

  const badOutput = ctx.tools.register(defineTool({
    name: 'molclaw_synthetic_bad_output', description: 'Synthetic output validation check',
    parameters: {}, output: { schema: { type: 'string' }, render: (_args, value) => [{ type: 'text', text: value }] },
    async execute() { return 42; },
  }));
  const invalid = await invoke('molclaw_synthetic_bad_output', {});
  assert.equal(invalid.isError, true);
  assert.equal(invalid.error.info.code, 'INVALID_TOOL_OUTPUT');
  badOutput();
  checks.push('official registry rejects invalid canonical output');

  const prepared = await success('molclaw_screen_prepare', {
    monomer_job: resolve(values.monomer), feedback: resolve(values.feedback), batch_size: 2, max_evaluations: 2,
  });
  sessionDir = prepared.session_dir;
  const request = await success('molclaw_screen_observe', { session_dir: sessionDir });
  assert.equal(request.payload.visible_state.candidates.length, 16);
  assert(request.payload.visible_state.candidates.every((row) => !Object.hasOwn(row, 'post')));
  await save('request.json', request);
  const chosen = request.payload.visible_state.candidates.slice(0, 2);
  const submission = {
    request_sha256: request.request_sha256,
    decision: {
      action: 'evaluate', candidate_ids: chosen.map((row) => row.candidate_id),
      reason: 'Deterministic transport validation: first two visible candidates; no decision model called.',
      evidence: chosen.map((row) => ({ candidate_id: row.candidate_id, field: 'pre.monomer_plddt', value: row.pre.monomer_plddt })),
    },
    actor: { harness: 'deepseek-harness-scripted-validation', model: null, agent_id: null },
  };
  await save('submission.json', submission);
  checks.push('native prepare and observe validate real saved monomers without revealing future complex metrics');
  const rejected = await invoke('molclaw_screen_apply', { session_dir: sessionDir,
    submission: { ...submission, request_sha256: '0'.repeat(64) } });
  assert.equal(rejected.isError, true);
  checks.push('stale submission is rejected through native tool and Python executor');
  const applied = await success('molclaw_screen_apply', { session_dir: sessionDir, submission });
  assert.equal(applied.status, 'applied');
  assert.equal(applied.observations.length, 2);
  assert.equal((await success('molclaw_screen_apply', { session_dir: sessionDir, submission })).status, 'already_applied');
  checks.push('stdin apply reveals only selected results and repeated apply is idempotent');
  const completed = await success('molclaw_screen_observe', { session_dir: sessionDir });
  assert.equal(completed.status, 'completed');
  assert.equal(completed.evaluated_count, 2);
  assert.equal(completed.stop_reason, 'candidate_limit_reached');
  assert.equal(completed.new_model_inference, false);
  assert.equal(completed.binding_validated, false);
  await save('report.json', completed);
  checks.push('quota finalizes report without new inference or model decisions');
  assert(dispatches.filter((row) => row.name === 'bash').every((row) => row.nested));
  checks.push('every bash call retains nested dispatch identity');
  await mounted.dispose();
  assert.equal((await invoke('molclaw_help', {})).isError, true);
  checks.push('plugin disposal unregisters its tools');
} finally {
  await ctx.fiber.dispose();
}

const modules = ['cordis', 'schemastery', 'dsh-tools', 'dsh-system-prompt', 'dsh-tool-bash', 'dsh-bash-local', 'dsh-subprocess-local'];
const runtimeVersions = {};
for (const name of modules) {
  const packageFile = resolve(values.runtime, 'node_modules/@deepseek-ai', name, 'package.json');
  runtimeVersions[`@deepseek-ai/${name}`] = JSON.parse(await readFile(packageFile, 'utf8')).version;
}
const validation = {
  status: 'passed', checks, dispatches, session_dir: sessionDir, runtime_versions: runtimeVersions,
  node: process.version, model_calls: 0, new_gpu_inference: false,
  mode: 'published Cordis + ToolRuntime + official local Bash + real MolClaw CLI saved-result replay',
  filesystem_sandbox_tested: false, profile_or_web_ui_started: false,
  full_harness_model_session_tested: false,
};
await save('validation.json', validation);
console.log(JSON.stringify({ status: validation.status, checks: checks.length, session_dir: sessionDir, output }));

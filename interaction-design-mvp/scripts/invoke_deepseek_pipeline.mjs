#!/usr/bin/env node
// Invoke one real ALPD tool through cached official DSH services.
// This is a transport integration runner, not a DeepSeek model client/session.
import assert from 'node:assert/strict';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { parseArgs } from 'node:util';

const { values } = parseArgs({ options: {
  runtime: { type: 'string' }, tool: { type: 'string' },
  arguments: { type: 'string' }, output: { type: 'string' },
} });
for (const key of ['runtime', 'tool', 'arguments', 'output']) {
  if (!values[key]) throw new Error(`Missing --${key}`);
}
assert(['prepare', 'run', 'observe', 'apply', 'export'].includes(values.tool));
const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const requireRuntime = createRequire(resolve(values.runtime, 'package.json'));
const load = (name) => import(pathToFileURL(requireRuntime.resolve(`@deepseek-ai/${name}`)).href);
const { Context } = await load('cordis');
const { ToolRuntime } = await load('dsh-tools');
const { SystemPrompt } = await load('dsh-system-prompt');
const { LocalSubprocessRuntime } = await load('dsh-subprocess-local');
const { LocalBashExecutor } = await load('dsh-bash-local');
const plugin = await import(pathToFileURL(resolve(projectRoot, 'plugins/alpd/deepseek/index.mjs')).href);
const args = JSON.parse(await readFile(resolve(values.arguments), 'utf8'));
const output = resolve(values.output);
await mkdir(output, { recursive: false });
const ctx = new Context();
const dispatches = [];
const started = performance.now();
let result;
try {
  ctx.plugin(SystemPrompt, {});
  ctx.plugin(ToolRuntime, { mode: 'native' });
  ctx.plugin(LocalSubprocessRuntime);
  ctx.plugin(LocalBashExecutor, { maxOutputBytes: 1024 * 1024, timeoutMs: 1800000 });
  ctx.plugin(await load('dsh-shell-env'), { dshHome: resolve(output, 'isolated-home') });
  ctx.plugin(await load('dsh-tool-bash'), {});
  ctx.plugin(plugin, { projectRoot, timeoutMs: 1800000 });
  await new Promise((done) => setImmediate(done));
  ctx.on('tools/pre-execute', (exec, next) => {
    dispatches.push({ name: exec.name, call_id: exec.callId, root_call_id: exec.rootCallId,
      nested: exec.parent !== undefined });
    return next();
  });
  result = await ctx.tools.execute({
    callId: `alpd-pipeline-${values.tool}`, name: `alpd_pipeline_${values.tool}`,
    arguments: args, signal: new AbortController().signal,
  });
} finally {
  await ctx.fiber.dispose();
}
const receipt = {
  status: result.isError ? 'failed' : 'passed', tool: `alpd_pipeline_${values.tool}`,
  arguments: args, dispatches, elapsed_seconds: (performance.now() - started) / 1000,
  runtime: 'official DSH 0.1.2-rc.1 Cordis, ToolRuntime, local Bash and ALPD plugin',
  model_client_calls: 0, deepseek_model_session_tested: false, result,
};
await writeFile(resolve(output, 'receipt.json'), JSON.stringify(receipt, null, 2) + '\n');
if (result.isError) {
  console.error(JSON.stringify(result));
  process.exitCode = 1;
} else {
  assert(dispatches.some((row) => row.name === 'bash' && row.nested));
  await writeFile(resolve(output, 'value.json'), JSON.stringify(result.value, null, 2) + '\n');
  console.log(JSON.stringify(result.value, null, 2));
}

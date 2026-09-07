// This file exercises the installed official SDK; bridge.test.mjs needs only Node.
import assert from 'node:assert/strict';
import test from 'node:test';
import { Config, apply, inject, name } from './index.mjs';

test('Config implements the actual Schemastery Standard Schema contract', () => {
  assert.equal(name, 'molclaw');
  assert.deepEqual(inject, ['tools']);
  assert.equal(typeof Config['~standard'].validate, 'function');
  assert.deepEqual(Config['~standard'].validate({ projectRoot: '/repo', timeoutMs: 1000 }).value,
    { projectRoot: '/repo', timeoutMs: 1000 });
  assert.ok(Config['~standard'].validate({ projectRoot: 42 }).issues.length);
  assert.ok(Config['~standard'].validate({ timeoutMs: 0 }).issues.length);
});

test('official defineTool compiles every registration, nested requiredness and output rendering', () => {
  const registered = [];
  apply({ tools: { register: (tool) => registered.push(tool) } }, { projectRoot: '/repo' });
  assert.deepEqual(registered.map((tool) => tool.name),
    ['molclaw_screen_prepare', 'molclaw_screen_observe', 'molclaw_screen_apply',
      'molclaw_pipeline_prepare', 'molclaw_pipeline_run', 'molclaw_pipeline_observe',
      'molclaw_pipeline_apply', 'molclaw_pipeline_export', 'molclaw_help']);
  const schema = registered[2].parameters;
  assert.deepEqual(schema.required, ['session_dir', 'submission']);
  assert.equal(schema.properties.submission.additionalProperties, false);
  assert.deepEqual(schema.properties.submission.required, ['request_sha256', 'decision', 'actor']);
  assert.deepEqual(schema.properties.submission.properties.actor.required, ['harness', 'model', 'agent_id']);
  assert.deepEqual(schema.properties.submission.properties.actor.properties.model.oneOf, [{ type: 'string' }, { type: 'null' }]);
  const pipeline = Object.fromEntries(registered.map((tool) => [tool.name, tool]));
  assert.deepEqual(pipeline.molclaw_pipeline_prepare.parameters.required, ['task', 'runtime']);
  assert.deepEqual(pipeline.molclaw_pipeline_prepare.parameters.properties.strategy.enum, ['harness', 'fixed', 'heuristic']);
  assert.deepEqual(pipeline.molclaw_pipeline_apply.parameters.required, ['pipeline_dir', 'submission']);
  assert.deepEqual(pipeline.molclaw_pipeline_apply.parameters.properties.submission, schema.properties.submission);
  assert.deepEqual(pipeline.molclaw_pipeline_export.parameters.required, ['pipeline_dir', 'output']);
  for (const tool of registered) {
    assert.deepEqual(tool.output.render({}, { checked: true }), [{ type: 'text', text: '{"checked":true}' }]);
  }
});

test('official defineTool rejects malformed submissions before reaching the host bash boundary', async () => {
  const registered = [];
  let calls = 0;
  apply({ tools: { register: (tool) => registered.push(tool), execute: () => { calls += 1; } } }, { projectRoot: '/repo' });
  await assert.rejects(registered[2].execute({ session_dir: '/session', submission: { request_sha256: 'a'.repeat(64) } }, {}),
    /decision|actor/);
  assert.equal(calls, 0);
});

test('invalid configuration registers no tools', () => {
  let calls = 0;
  assert.throws(() => apply({ tools: { register: () => { calls += 1; } } }, { projectRoot: 'relative' }), /absolute/);
  assert.equal(calls, 0);
});

test('official defineTool validates pipeline arguments before host execution', async () => {
  const registered = [];
  let calls = 0;
  apply({ tools: { register: (tool) => registered.push(tool), execute: () => { calls += 1; } } }, { projectRoot: '/repo' });
  const named = Object.fromEntries(registered.map((tool) => [tool.name, tool]));
  await assert.rejects(named.molclaw_pipeline_prepare.execute({ task: '/task' }, {}), /runtime/);
  await assert.rejects(named.molclaw_pipeline_prepare.execute({ task: '/task', runtime: '/runtime', strategy: 'other' }, {}), /strategy|enum/);
  await assert.rejects(named.molclaw_pipeline_apply.execute({ pipeline_dir: '/pipeline', submission: { request_sha256: 'a'.repeat(64) } }, {}), /decision|actor/);
  await assert.rejects(named.molclaw_pipeline_export.execute({ pipeline_dir: '/pipeline' }, {}), /output/);
  assert.equal(calls, 0);
});

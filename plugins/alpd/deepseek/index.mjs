import Schema from '@deepseek-ai/schemastery';
import { defineTool } from '@deepseek-ai/dsh-tools';
import { createToolOptions, resolveConfig } from './bridge.mjs';

export const name = 'alpd';
export const inject = ['tools'];

export const Config = Schema.object({
  projectRoot: Schema.string().description(
    'Absolute ALPD checkout path. Defaults to ALPD_PROJECT_ROOT.',
  ),
  cli: Schema.string().description(
    'CLI executable path, absolute or relative to interaction-design-mvp. Defaults to .venv/bin/alpd.',
  ),
  timeoutMs: Schema.number().min(Number.MIN_VALUE).description(
    'Optional foreground bash timeout in milliseconds; the host still applies its timeout cap.',
  ),
});

export function apply(ctx, config = {}) {
  const resolved = resolveConfig(config);
  for (const options of createToolOptions(ctx, resolved)) {
    ctx.tools.register(defineTool(options));
  }
}

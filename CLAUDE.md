# MolClaw project context

MolClaw consists of a Python scientific execution core and host skill/plugin
adapters. Start with [README.md](README.md) and [CONTRIBUTING.md](CONTRIBUTING.md).

## Maintained code

- `interaction-design-mvp/src/interaction_design/`: scientific task schemas,
  generation, evaluation, screening, benchmarks and persistent execution.
- `interaction-design-mvp/tests/`: Python contract and regression tests.
- `plugins/molclaw/`: shared skill, Codex/Claude metadata and DeepSeek tool bridge.
- `docs/RESEARCH_GOAL.md`: current research question and completion criteria.
- `docs/M1_STATUS.md` and `docs/evidence/`: implementation and experiment records.

## Local checks

From `interaction-design-mvp/`, use the existing environment:

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/interaction-design --help
```

From the repository root, the dependency-free bridge check is:

```bash
node --test plugins/molclaw/deepseek/bridge.test.mjs
```

With the plugin's declared SDK dependencies available, run `npm test` from
`plugins/molclaw/` for the bridge and official SDK checks.

## Working conventions

Preserve existing model assets, environments, experiment outputs and frozen
protocols. Keep historical experiment evidence intact; label new runs separately.
Distinguish synthetic tests, saved-result replay, live model execution and
experimental binding evidence when describing results.

For downloads and dependency installation, reuse caches and try direct networking
first. Scope proxy overrides to the transfer command and its children, and check
the tool's own proxy configuration. Diagnose direct failures before enabling a
proxy for that transfer. Do not change Clash or the agent's network environment.

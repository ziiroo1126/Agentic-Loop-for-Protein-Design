# Contributing to MolClaw

Scientific execution belongs in `interaction-design-mvp/`. Host integrations
belong in `plugins/molclaw/`. Keep task validation, persistent state and scientific
execution in the shared Python core so hosts use the same behavior.

## Local development

Reuse the existing development environment when available. From the repository
root:

```bash
cd interaction-design-mvp
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
```

For a fresh environment, follow the [package setup guide](interaction-design-mvp/README.md).
Python CI uses the locked dependencies, runs Ruff and pytest, and builds the package.

The DeepSeek transport tests require only Node and run from the repository root:

```bash
node --test plugins/molclaw/deepseek/bridge.test.mjs
```

When the SDK dependencies declared in the plugin's `package.json` are available,
run the complete plugin checks:

```bash
cd plugins/molclaw
npm test
```

## Changes and evidence

- Preserve existing task, protocol and result identities when resuming work.
- Add focused regression coverage for behavior changes and failure handling.
- Keep model weights, external datasets, credentials, environments and generated
  outputs out of Git. Preserve upstream licenses and asset provenance.
- Record new scientific runs separately from existing evidence. State whether
  validation used synthetic fixtures, saved results, real inference or experimental
  measurements, and report the limits of each result.
- Update the relevant package guide and project status when behavior changes.

## Downloads

Reuse local caches and existing environments first. Try direct access before a
subscription proxy, applying environment overrides to the download command and
its children and checking the tool's own proxy settings. Distinguish unreachable
networks from authentication and address errors before retrying through a proxy.
Do not modify Clash or the agent's global network environment.

# Contributing to ALPD

Scientific execution belongs in `interaction-design-mvp/`. Host integrations
belong in `plugins/alpd/`. Keep task validation, persistent state and scientific
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
node --test plugins/alpd/deepseek/bridge.test.mjs
```

When the SDK dependencies declared in the plugin's `package.json` are available,
run the complete plugin checks:

```bash
cd plugins/alpd
npm ci --ignore-scripts --legacy-peer-deps
npm test
```

The lock includes the SDK's actual runtime imports. `--legacy-peer-deps` keeps the
test setup from installing a complete host profile; those peers are provided by
the real host in a deployed plugin. The CI validates this setup in a new directory.

From the repository root, after activating the core environment:

```bash
python tools/build_site.py --output /tmp/alpd-site
python tools/verify_release.py --site /tmp/alpd-site
python tools/browser_smoke.py --site /tmp/alpd-site --output /tmp/alpd-browser
```

Browser verification needs Firefox and Xvfb (or a running display). Use `--xvfb`
and `--firefox` for existing binaries in nonstandard locations. Output paths must
be new. The static verifier checks real file hashes, local HTML links, complete
archive membership and byte equality; browser checks exercise the actual pages.

The main-branch CI also runs for plugin/docs/example changes. On success the Pages
workflow builds and deploys the static gallery. The repository's Pages source must
be set to **GitHub Actions** by an administrator once. A version tag runs the same
gates before publishing a GitHub prerelease with assets and checksums.

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

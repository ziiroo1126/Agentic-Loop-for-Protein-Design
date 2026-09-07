# ALPD scientific core

The Python package `agentic-loop-protein-design` provides the `alpd` command.
It executes protein binder tasks, validates agent actions and exports traceable
results. The import namespace is `interaction_design`; `interaction-design` is
retained as a CLI alias for existing scripts.

Start with the repository's [three quick-start paths](../docs/QUICKSTART.md),
[Codex guide](../docs/CODEX.md) and [runtime guide](../docs/RUNTIME.md).

## Commands

| Command | Purpose |
| --- | --- |
| `task init/review/build` | Prepare incomplete requirements and validate structure mapping |
| `pipeline preflight/prepare/run/observe/apply/export` | Complete task-to-results workflow |
| `viewer export` | Build an offline 3D viewer from an existing result bundle |
| `adaptive` | Acquire cached observations, plan, reflect and export a replay |
| `screen` | Select evaluations for an existing candidate pool |
| `benchmark`, `benchmark-repeat` | Recorded-data selection comparisons and repeat analysis |

The current full path is ODesign/OInvFold → ESMFold v1 monomer checks → host
selection → ESMFold2 complex prediction/interface feedback → further selection or
stop → portable output. AF3/PyRosetta and broader generation-schema modalities
remain separate adapters outside this complete protein protocol.

## Development

From this directory, with existing uv:

```bash
uv sync --locked --extra dev
uv run --no-sync ruff check .
uv run --no-sync pytest -q
uv build
```

For downloads use the repository's `bash tools/setup.sh --extra dev` from the root,
which scopes direct networking to that installation. CPU tests use synthetic
fixtures and do not require model weights. The repository CI also checks the
installed wheel, host metadata, CPU example and browser experience.

## References

- [Input format](docs/TASK_INPUT.md)
- [Complete pipeline](docs/PIPELINE.md)
- [Adaptive cached evaluation](docs/ADAPTIVE.md)
- [Structure viewer](docs/STRUCTURE_VIEWER.md)
- [Example and gallery build](docs/DEMO.md)
- [ODesign setup](docs/ODESIGN_SETUP.md), [monomer](docs/ESMFOLD.md), [complex](docs/ESMFOLD2.md)
- [All user guides and historical research records](../docs/README.md)

Model weights, environments and full experiment directories remain local.
Package resources include fixed protocol files, worker scripts and vendored 3Dmol.js
so installed distributions can export viewers without a CDN.
The core uses [Apache-2.0](LICENSE); see [third-party notices](../THIRD_PARTY_NOTICES.md).

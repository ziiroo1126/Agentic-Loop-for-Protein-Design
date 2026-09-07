# MolClaw

[简体中文](README.zh-CN.md)

MolClaw provides reproducible molecular interaction-design workflows and tools for
host agents. The Python core manages scientific tasks, model execution, bounded
candidate selection, persistent records and portable result bundles. Host adapters
are packaged as skills and plugins for Codex, Claude Code and DeepSeek Harness.

The current workflow connects ODesign generation, ESMFold monomer checks and
ESMFold2 complex evaluation. Public-data benchmarks and repeated host decisions
have been recorded; a stable LLM selection advantage has not been demonstrated.
The next research question concerns adaptive allocation of additional evaluations.

Sequential reevaluation is available through `adaptive`: host plans, paid observations,
reflections and an offline replay page. Start with the
[adaptive guide](interaction-design-mvp/docs/ADAPTIVE.md), or open the included
[PD-L1 case](docs/evidence/adaptive-loop-pdl1/index.html) locally in a browser.
This is a development-data demonstration, not independent validation.

## Repository

```text
interaction-design-mvp/      Python package, tests, protocols and examples
  src/interaction_design/   Scientific workflow and command-line interface
  config/                   Pinned assets and evaluation protocols
  docs/                     Runtime and benchmark guides
  artifacts/                Local experiment outputs (ignored by Git)
  models/, data/            Local model assets and external data (ignored by Git)
plugins/molclaw/            Shared skill and host adapters
docs/                      Project plan, research goal and experiment evidence
.github/workflows/         Python continuous integration
```

## Start here

With the existing Python development environment, run from the repository root:

```bash
cd interaction-design-mvp
.venv/bin/interaction-design --help
.venv/bin/interaction-design validate examples/ligand_binder.json
.venv/bin/interaction-design pipeline --help
```

For environment preparation and a CPU-only example, see the
[Python package guide](interaction-design-mvp/README.md).
For host integration, see the [plugin guide](plugins/molclaw/README.md).
The [pipeline guide](interaction-design-mvp/docs/PIPELINE.md) covers task preparation,
execution, candidate decisions and export.

## Development and research

- [Contributing and local checks](CONTRIBUTING.md)
- [Project plan](docs/PROJECT_PLAN.md)
- [Implementation and validation records](docs/M1_STATUS.md)
- [Current research goal](docs/RESEARCH_GOAL.md)
- [Recorded experiments](docs/evidence/)

Model weights, environments and full experiment outputs are local resources. They
are not included in a fresh clone. Reuse caches and existing environments before
downloading or installing dependencies.

## Project history and licenses

MolClaw originated from the BioClaw/NanoClaw chat application. The legacy chat
application and its deployment assets have been removed from the current working
tree; their source remains in Git history. The maintained execution core lives in
`interaction-design-mvp/`, and host integration lives in `plugins/molclaw/`.

The original [repository license](LICENSE), [Python package license](interaction-design-mvp/LICENSE)
and upstream notices under `interaction-design-mvp/config/` are retained.

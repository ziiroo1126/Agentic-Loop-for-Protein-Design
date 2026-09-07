# Agentic Loop for Protein Design (ALPD)

[中文](README.zh-CN.md) · [Interactive gallery](https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/) · [Quick start](docs/QUICKSTART.md) · [Releases](https://github.com/ziiroo1126/Agentic-Loop-for-Protein-Design/releases)

**Give an agent a protein binder task. Inspect its decisions, tool results and 3D structures.**

ALPD connects supplied requirements to ODesign candidate generation, ESMFold v1
monomer checks, agent-guided ESMFold2 complex evaluation, and portable result export.
The Python core executes scientific tools and checks actions; your host supplies
the decision model. Codex is the default skill entry point.

**0.1.0 Research Preview.** The complete workflow currently supports one fixed-length
linear protein binder and one fixed protein target. Computational metrics do not
establish experimental binding, and a stable LLM selection advantage has not been
shown. See [scope and known limits](docs/LIMITATIONS.md).

[![ALPD structure viewer: a recorded complex prediction, chain controls and metrics](docs/images/alpd-structure-viewer.png)](https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/demo/structures.html)

## Start here

| I want to… | Input | Start | Output |
| --- | --- | --- | --- |
| Explore immediately | A browser | [Online gallery](https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/) / [offline ZIP](https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/downloads/alpd-demo.zip) | Recorded decisions and interactive 3D |
| Run without models | Python 3.12/3.13, uv | [CPU quick start](docs/QUICKSTART.md#2-run-the-cpu-example) | Synthetic session and HTML report |
| Run a real task | Target structure, constraints, configured GPU environments | [Runtime setup](docs/RUNTIME.md) + [Codex skill](docs/CODEX.md) | New candidates, evaluations and result bundle |

From a checkout, with uv available:

```bash
bash tools/setup.sh --python 3.12
source interaction-design-mvp/.venv/bin/activate
python tools/cpu_demo.py --output /tmp/alpd-cpu-demo
```

Open `/tmp/alpd-cpu-demo/view/index.html`. This example uses synthetic data and a
fixed policy, with no model downloads or LLM calls. Use a fresh output directory.
The [full guide](docs/QUICKSTART.md) includes cloning, requirements and expected results.

## Use the host skill

After CPU setup, install into your working project:

```bash
export ALPD_PROJECT_ROOT="$PWD"
mkdir -p /tmp/alpd-work
python tools/alpd.py install --host codex --project-dir /tmp/alpd-work
python tools/alpd.py doctor --host codex --project-dir /tmp/alpd-work
cd /tmp/alpd-work
codex
```

Then invoke `$alpd:alpd` with your supplied task and runtime. For an initial no-GPU host
session, use the complete [example prompt](docs/CODEX.md#first-host-run-without-a-gpu).
Skill installation reuses the core and links the full skill directory. Codex uses
its configured account; ALPD does not add a second LLM API client.
Claude Code and DeepSeek adapters have separate [verification boundaries](docs/CODEX.md#host-support).

## The loop

```mermaid
flowchart LR
    A[User goals and available files] --> B[Clarify and validate task]
    B --> C[Preflight local runtime]
    C --> D[Generate candidates and check monomers]
    D --> E[Agent selects evaluations]
    E --> F[Complex prediction and feedback]
    F -->|Continue| E
    F -->|Stop| G[Report, evidence and 3D export]
```

Incomplete input can stay in a brief. `task review` identifies missing requirements;
`task build` checks structure/residue mapping before execution. Current live actions
select evaluations or stop. Automatic redesign from feedback is future work.
The separate `adaptive` workflow reveals cached predictions and records plans and
reflections; it does not launch fresh GPU inference.

## Worked examples

- **[Complete binder case](examples/pdl1-binder/README.md):** original task, two generated
  candidates, real host selection, complex feedback, budget stop and 3D structures.
  The portable records, software provenance and hashes are included in the repository.
- **[Separate adaptive case](https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/demo/replay/index.html):**
  three recorded queries and reflections on a different, existing 90-candidate pool.
- **[Synthetic CPU example](docs/QUICKSTART.md#2-run-the-cpu-example):** an installable
  workflow demonstration with fictional measurements and a fixed baseline.

Rebuild the public gallery without model inference:

```bash
python tools/build_site.py --output /tmp/alpd-site
```

Open `/tmp/alpd-site/index.html`; offline ZIPs and checksums are under `downloads/`.
The browser supports candidate/structure switching, rotation, chain visibility,
residue inspection, PNG export and original structure downloads. Every standalone
3D page embeds 3Dmol.js and its data, with no CDN dependency.

## Documentation and development

[User guides](docs/README.md) · [Validation](docs/RELEASE_VERIFICATION.md) · [Contributing](CONTRIBUTING.md) · [Changelog](CHANGELOG.md) · [Research objective](docs/RESEARCH_GOAL.md)

```text
interaction-design-mvp/   Python core, model adapters and scientific tests
plugins/alpd/            Shared ALPD skill and host adapters
tools/                   Setup, CPU example, gallery and release checks
examples/pdl1-binder/     Portable original case and reproduction instructions
docs/                    User guides, release notes and historical evidence
.github/                 CI, publication workflows and issue templates
```

Models, environments and full local experiment directories are not tracked.
Original research records remain in `docs/evidence/`; current user instructions
are indexed separately. Downloads reuse caches and try direct connections first.

## Licenses

The repository retains its [MIT license](LICENSE); the Python core is
[Apache-2.0](interaction-design-mvp/LICENSE). Model and dataset terms apply
separately. See [third-party sources and notices](THIRD_PARTY_NOTICES.md).

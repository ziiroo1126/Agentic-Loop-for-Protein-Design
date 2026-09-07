# ALPD quick start

ALPD 0.1.0 is a research preview for protein binder workflows. Choose one path:

| Path | What you supply | What you get |
| --- | --- | --- |
| Browser demo | A browser with JavaScript and WebGL | Recorded decisions, 3D structures, downloads |
| CPU example | Python 3.12/3.13 and uv | A fresh synthetic session and an offline report |
| Real design | Supplied target/constraints, supported GPU model environments, host account for Agent mode | New sequences, structures, diagnostics and decisions |

## 1. Browse the demo

Open the [interactive gallery](https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/).
For offline use, download [alpd-demo.zip](https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/downloads/alpd-demo.zip),
extract it, and open `demo/index.html`. Keep the extracted files together.
If local iframe loading is restricted, use the standalone page links.
All results are saved records; browsing makes no model calls.

## 2. Run the CPU example

Use Linux with Python 3.12 or 3.13, Git and [uv](https://docs.astral.sh/uv/getting-started/installation/).
These instructions assume `uv` is already available. Reuse your checkout when updating.
The setup script uses local caches first and directs its downloads without changing global proxies.

```bash
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  git -c http.proxy= -c https.proxy= clone \
  https://github.com/ziiroo1126/Agentic-Loop-for-Protein-Design.git
cd Agentic-Loop-for-Protein-Design
bash tools/setup.sh --python 3.12
source interaction-design-mvp/.venv/bin/activate
alpd --help
python tools/cpu_demo.py --output /tmp/alpd-cpu-demo
```

Open `/tmp/alpd-cpu-demo/view/index.html`. Four synthetic candidates are processed
using a fixed uniform policy, with 12 cached prediction observations. No weights,
GPU or LLM account are needed. Scores and labels are fictional software fixtures.
Use a different output directory if that path already exists.

To use only cached packages, add `--offline` to `tools/setup.sh`. If direct access
fails, distinguish an unavailable address, authentication and network reachability
before enabling a proxy for that individual download.

To view real structures on CPU:

```bash
alpd viewer export examples/pdl1-binder/run --output /tmp/alpd-structures.html
```

## 3. Execute a real design

Prepare local model environments using [RUNTIME.md](RUNTIME.md), then install the
[Codex skill](CODEX.md). Supply a local target PDB/mmCIF, its chain/residue intervals,
hotspots, a fixed binder length and a generation seed pool. An incomplete brief can
be reviewed before those choices are resolved; ALPD does not invent missing requirements.

For the checked-in [recorded case](../examples/pdl1-binder/README.md):

```bash
alpd task review examples/pdl1-binder/brief.json
alpd task build examples/pdl1-binder/brief.json --output /tmp/alpd-task.json
alpd pipeline preflight /tmp/alpd-task.json --runtime /path/to/runtime.json
```

The first two commands validate inputs without inference. Preflight checks local
resources. A passing preflight does not test GPU execution. In Codex, invoke `$alpd:alpd`
with the task, runtime and explicit evaluation budget as shown in [CODEX.md](CODEX.md).
The host follows prepare → run → observe/apply → export. The output bundle contains
`index.html`, FASTA, structures, CSV/JSON metrics, decisions and checksum records.

For a CLI-only fixed baseline, use the original case task:

```bash
alpd pipeline prepare examples/pdl1-binder/run/task.json \
  --runtime /path/to/runtime.json --strategy fixed --batch-size 1 --max-evaluations 1
alpd pipeline run /path/to/returned/pipeline_dir
alpd pipeline export /path/to/returned/pipeline_dir --output /path/to/new/results
```

Replace the printed directory placeholder. `fixed` is not an LLM policy. Complex
evaluation budgets do not cap candidate generation or monomer checking. Runtime
timeouts apply separately. See [limits](LIMITATIONS.md) and [the pipeline reference](../interaction-design-mvp/docs/PIPELINE.md).

## Update

Update with `git pull --ff-only`, then rerun `bash tools/setup.sh`. Project-local
skills link to the full checkout and update with it. Keep that checkout in place.
Restart the host if the skill list is stale; do not overwrite another project's skill.

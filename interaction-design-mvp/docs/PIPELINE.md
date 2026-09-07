# Complete protein binder pipeline

`pipeline` connects a biological task to a portable result bundle:

```text
Task + local runtime
    → preflight and frozen inputs
    → ODesign / OInvFold candidate generation
    → ESMFold v1 monomer checks for the whole pool
    → host selection from visible diagnostics
    → ESMFold2 complex prediction and interface feedback
    → next selection or stop
    → FASTA, structures, metrics, decisions and report
```

The host is Codex, Claude Code or DeepSeek Harness. Its model supplies candidate
decisions through the shared [skill](../../plugins/molclaw/skills/molclaw/SKILL.md).
MolClaw does not create a second LLM client or require LLM API credentials.

## Prepare a task

Use a local reference PDB/mmCIF, target chain/residue intervals, hotspot residues,
binder length and an explicit seed pool. Relative reference paths resolve against
the task JSON location. [pipeline_pdl1_2.json](../examples/pipeline_pdl1_2.json) is a
two-candidate PD-L1 development example, not a universal target specification.

This first complete pipeline supports one fully generated, fixed-length protein
binder and one fixed protein target, both linear, with one candidate per distinct
seed. It checks reference coordinates and hotspot mapping before inference.
Other modalities, cyclic designs, motifs, MSA and partial diffusion still use the
separate generation interfaces; they are outside this complete PPI protocol.

Copy [pipeline-runtime.example.json](../config/pipeline-runtime.example.json) into
a local configuration and provide cached paths for all three environments:

- `generation`: ODesign checkout, chemical components, checkpoints and Python.
- `monomer`: ESMFold v1 Python, snapshot, GPU and process timeout.
- `complex`: pinned ESMFold2 and ESMC-6B snapshots, CCD, Python and GPU settings.

Preparation checks missing files, complete ESMFold2 weight shards, the ODesign Git
revision and checkpoint provenance. It runs no models and downloads nothing.

```bash
interaction-design pipeline prepare examples/pipeline_pdl1_2.json \
  --runtime .cache/pipeline-runtime.local.json \
  --strategy harness --batch-size 1 --max-evaluations 1
```

The JSON result contains `pipeline_dir`. The executor copies the reference, task,
runtime and controller configuration into that directory and records their hashes.
Original target files may then move without invalidating this prepared task.

## Run and select

```bash
interaction-design pipeline run /absolute/path/to/pipeline
interaction-design pipeline observe /absolute/path/to/pipeline
```

`run` performs generation and monomer checks, prepares screening, then returns
`status: awaiting_selection` with a `request`. Use only
`request.payload.visible_state` for the decision and follow
`request.response_schema`. This is the existing strict
[screening submission protocol](SCREENING.md):

```bash
interaction-design pipeline apply /absolute/path/to/pipeline --decision decision.json
# stdin is also supported: --decision -
```

`apply` returns the next request or a completed report. Candidate identities, exact
numeric evidence, current request hash and quotas are validated before evaluation.
Identical accepted submissions can be repeated safely. A stale request requires a
fresh observation. `fixed` and `heuristic` strategies finish through `pipeline run`
without a host decision model; they are baselines, not LLM Agent runs.

`max_evaluations`, `batch_size` and optional `tool_wall_budget_seconds` govern
**complex screening only**. Generation uses the task's seed pool; monomer checks
cover that whole pool. Generation and monomer process limits are separate runtime
settings. Reports distinguish stage wall times from complex tool time. Host token
usage is unknown unless separately measured.

## Resume and export

Reusing `pipeline run` validates and skips completed stages. Each stage has a
durable receipt, hashes and elapsed time. A failed or interrupted stage retains
its files and is not automatically resubmitted. Inspect its process logs and
receipts before arranging recovery; there is no blanket retry switch that can
silently duplicate model work. The pipeline and screening implementation must
match their saved source snapshots when resuming.

```bash
interaction-design pipeline export /absolute/path/to/pipeline \
  --output /absolute/path/to/new-result-bundle
```

Export requires a completed screening session and a fresh output directory. It
validates source identities, receipts and structures, and copies portable artifacts
with a checksum manifest. The bundle includes all candidate metadata and monomer
diagnostics, evaluated candidate FASTA and complex structures, protocol/task
information, decision trace, metrics CSV/JSON and a Markdown report. Unselected
candidates remain explicitly unevaluated. Runtime paths are omitted from portable
provenance; source hashes identify the original records.

AF3 and PyRosetta are not invoked. Legacy metric rules in a supplied task are not
used as ESMFold2 acceptance thresholds. All results are computational diagnostics:
`acceptance: null`, `binding_validated: false`. This pipeline establishes an
executable workflow, not experimental binding or an Agent advantage over baselines.

## Host integrations

Codex and Claude Code use the shared skill and relocatable CLI launcher. DeepSeek
Harness additionally exposes `molclaw_pipeline_prepare`, `run`, `observe`, `apply`
and `export` tools via the official nested bash transport. Configure a host bash
timeout long enough for the requested model work. Host cancellation propagates to
the scientific worker process group and preserves failure records. See the
[plugin guide](../../plugins/molclaw/README.md) for discovery and local configuration.

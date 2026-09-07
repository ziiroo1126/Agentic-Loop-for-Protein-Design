# Interaction Design MVP

[![Interaction Design CI](https://github.com/ziiroo1126/MolClaw/actions/workflows/interaction-design-mvp.yml/badge.svg)](https://github.com/ziiroo1126/MolClaw/actions/workflows/interaction-design-mvp.yml)

This is a domain-first, reproducible biomolecular interaction-design workflow built on
the **evedesign core abstractions** and connected to **ODesign** as its first generative
backend. It is the maintained scientific execution core of MolClaw, with host
integration in [plugins/molclaw](../plugins/molclaw/README.md). The package owns its
dependencies, tests and build configuration and can be distributed independently.

The project is more than an ODesign launcher:

The complete protein-binder entry point is now `pipeline prepare/run/observe/apply/export`:
task → ODesign generation → ESMFold v1 monomer checks → host candidate selection →
ESMFold2 complex feedback → portable sequences, structures and report. It validates
local assets before inference and resumes completed stages without regenerating.
See the [complete workflow guide](docs/PIPELINE.md).

The `adaptive` entry point supports sequential acquisition of cached predictions,
evidence-grounded plans and reflections, and offline HTML export. The
[adaptive guide](docs/ADAPTIVE.md) includes a synthetic example and a recorded host case.
`pipeline preflight` checks an existing task/runtime without starting inference.

The independent `benchmark prepare/observe/apply/report` mode evaluates host selection
on a pinned public wet-lab dataset, with anonymous feature-only requests, committed
decisions and common baselines. See the [external benchmark guide](docs/EXTERNAL_BENCHMARK.md).
It uses published scores and measurements; it does not establish new MolClaw binders.
The [repetition coordinator](docs/BENCHMARK_REPETITIONS.md) reuses those frozen inputs
to measure host decision stability, with all decisions committed before reporting.

```text
InteractionDesignSpec
        │
        ├──> evedesign System ──> ODesignGenerator ──> SystemInstance[]
        │                              │
        │                    mock / local / container
        │
        └──> ESMFold2 complex evaluation ──> confidence + design consistency
                                                        │
                                             report + run manifest
                                                        │
                                        interface geometry feedback
```

The stable public boundary is the scientific task and the evedesign contracts. ODesign's
chain-string syntax, Hydra invocation, filesystem layout, and CUDA process are adapter
details that can be replaced without changing the task or evaluation layer.

## What the MVP already proves

- A strict `InteractionDesignSpec` expresses proteins, ligands, RNA/DNA, generated and
  reference-fixed regions, residue hotspots, atom constraints, cyclic chains, motif
  scaffolding, and partial diffusion.
- `ODesignGenerator` implements evedesign's `BaseModel + Generator` contract and converts
  generated CIF/sequence output back to `SystemInstance` objects.
- The executor boundary supports deterministic CPU-only tests, a local ODesign checkout,
  or an OCI container. Commands are argument arrays; no shell rewriting is used.
- AF3 confidence and PyRosetta interface metrics are independent evedesign `Scorer`
  implementations. Threshold comparisons are inclusive (`>=`/`<=`) and ranking records
  every weighted contribution.
- Successful generations emit the resolved task, seeds, command, software versions,
  candidate artifacts, SHA-256 checksums, and JSON/Markdown reports. Local execution
  also captures the inference Python environment. Failed generations retain a manifest
  and error record; launched processes retain stdout/stderr, including on timeout.
- Evaluation can resume from saved candidates. Each attempt has a separate manifest and
  report, with input checksums and a link to the immutable generation manifest.
- No model weight, chemical-component dataset, or run artifact is tracked by Git.

**ESMFold2 is the current protein-complex evaluation backend.** The `complex` CLI
prepares two-chain requests, runs cached ESMFold2/ESMC-6B offline, validates outputs,
and reports interface confidence and consistency with the generated design. All 16
PD-L1 complex predictions have completed on one L20. See [ESMFOLD2.md](docs/ESMFOLD2.md).
The CPU-only `complex feedback` command adds mapped hotspot contacts, cross-chain
heavy-atom clashes and contact retention for all 16 candidates. It emits structured
observations with no automatic acceptance threshold. See
[INTERFACE_FEEDBACK.md](docs/INTERFACE_FEEDBACK.md).
The separate `assessment` CLI retains the original optional AF3/PyRosetta protocol;
those models have not run here. See [EVALUATION.md](docs/EVALUATION.md).
The `campaign` CLI now runs a sample/observe/stop controller with fixed and non-LLM
feedback strategies. Both use the same frozen seed schedule, tools, action validation
and persistent step records. Saved observations can be replayed without model inference.
See [CAMPAIGNS.md](docs/CAMPAIGNS.md).

The delivery target is a **Codex / Claude Code / DeepSeek-host skill or plugin**.
The host supplies the decision model; MolClaw supplies scientific tools and persistent
state through `screen prepare/observe/apply`. Candidate decisions cite exact visible
metrics and are checked before evaluation. Fixed and heuristic policies use the same
boundary through `screen run`. See the [harness package](../plugins/molclaw/README.md)
and [screening protocol](docs/SCREENING.md). An independent web application or LLM HTTP
service is not required. The official [DeepSeek Harness adapter](../plugins/molclaw/skills/molclaw/references/deepseek-harness.md)
now provides a Cordis/npm bundle with native prepare/observe/apply tools, tested against
published `0.1.2-rc.1` modules through their real Bash executor and this Python CLI.
This library integration test uses saved results and no decision model or Web UI.

The [offline ESMFold workflow](docs/ESMFOLD.md) now prepares, runs and reports a
complete saved generation batch, loading cached weights once. A frozen PD-L1 pilot
produced and refolded all **16 candidates**: seed 49 had mean CA pLDDT 81.99 and
design CA RMSD 0.948 Å, while the highest-confidence candidate had RMSD 9.418 Å.
The [complete pilot report](../docs/evidence/PDL1_16_baseline.md) includes every
candidate, diversity, failed-attempt costs and the fixed selection for later complex
evaluation. These monomer checks do not establish binding or Agent performance.
The earlier [single-candidate/control check](../docs/evidence/ESMFOLD_smoke.md)
remains available.

## Quick start without a GPU or model weights

Python 3.12 or newer is required because evedesign requires it. The dependency is pinned
to the reviewed evedesign 0.0.6 Git commit because that version is not yet on PyPI.

```bash
uv sync --extra dev
uv run interaction-design validate examples/ligand_binder.json
uv run interaction-design run examples/ligand_binder.json \
  --executor mock \
  --evaluation-backend af3-sidecars \
  --artifacts artifacts
uv run pytest
```

The mock backend writes valid CIF structures and deterministic AF3/PyRosetta-shaped
metrics, so the complete generation → evaluation → ranking → report path is executable
in CI. It is a contract test, not a scientific predictor.

To inspect only the translation into upstream ODesign JSON:

```bash
uv run interaction-design materialize examples/ligand_binder.json \
  --output /tmp/odesign_input.json
```

## Real ODesign generation

Use the revisions in `config/assets.lock.toml`. The lock currently pins the ODesign Git
checkout and two Hugging Face repositories to full commit hashes.

```bash
git clone https://github.com/OTeam-AI4S/ODesign.git /path/to/ODesign
git -C /path/to/ODesign checkout 43944e930aea7dd74d2762f6cce7855471edae7b

uv run interaction-design assets download --destination /path/to/model-assets \
  --only odesign-prot-flex \
  --only oinvfold-protein
uv run interaction-design assets verify --destination /path/to/model-assets
```

The downloader invokes `hf download --revision <40-char-commit>`, checks the downloaded
revision with `hf cache verify`, and creates an `asset-manifest.json` containing a SHA-256
and size for every local file. Subsequent `assets verify` checks are offline and never
deserialize untrusted checkpoints. Each task needs one diffusion checkpoint and one
modality-specific inverse-folding checkpoint; omit `--only` only when you deliberately
want to fetch all eight model variants (about 17 GB for ODesign alone).

The source pin deliberately uses the OInvFold-compatible revision. Upstream commit
[`9982361`](https://github.com/OTeam-AI4S/ODesign/commit/998236111b5f1f5cd81d0c08507e60be3bf5def9)
changed protein/RNA inverse folding to ProteinMPNN/LigandMPNN/gRNAde. Using that version
with this adapter's OInvFold asset lock would require different checkpoints. Do not update
the source pin independently of the runtime and asset contract.

ODesign also requires these two chemical-component files in a separate data directory:

- `components.v20240608.cif`
- `components.v20240608.cif.rdkit_mol.pkl`

Obtain them using ODesign's own data-preparation instructions. Keep that directory outside
Git. After installing ODesign's environment, run generation through its Python executable:

```bash
uv run interaction-design run examples/ligand_binder.json \
  --executor local \
  --generation-only \
  --odesign-repo /path/to/ODesign \
  --data-root /path/to/odesign-data \
  --checkpoint-root /path/to/model-assets/ckpt \
  --python-executable /path/to/odesign-env/bin/python \
  --cuda-visible-devices 0 \
  --timeout 900
```

For the lowest-cost end-to-end GPU check, use
`examples/real_smoke_ligand_binder.json`; it requests exactly one 30-residue backbone,
one seed, and one inverse-folded sequence.

For a protein binder/target task, replace `--generation-only` with
`--complex-config .cache/esmfold2-runtime.local.json` to run ESMFold2 after generation.
Alternatively, evaluate a saved generation using `complex prepare/run/report`, as
described in [ESMFOLD2.md](docs/ESMFOLD2.md). The original AF3/PyRosetta sidecar evaluation
remains available explicitly for its legacy protocol. A CUDA-enabled,
prebuilt image can instead be selected with
`--executor container --container-image ... --container-gpus all`.

Both real executors require `asset-manifest.json` in the parent of `--checkpoint-root`.
Before launching, they check the selected pins and actual checkpoint checksums/sizes.
Local manifest integrity and independent Hub verification evidence are recorded separately.

### Evaluate saved candidates

For a protein binder, run the independent monomer check on a saved generation:

```bash
uv run interaction-design monomer prepare artifacts/<run-id> \
  --protocol config/baseline-pdl1-16.protocol.json
uv run interaction-design monomer run artifacts/<run-id>/monomer_batches/<job-id> \
  --python /path/to/esmfold-environment/bin/python \
  --model-dir /path/to/local/esmfold_v1/snapshot --gpu 0 --timeout 900
uv run interaction-design monomer report artifacts/<run-id>/monomer_batches/<job-id>
```

The example protocol requires the exact 16 seeds and length in
`examples/baseline_pdl1_16.json`. Omit `--protocol` for a general descriptive batch;
freeze the scientific task and analysis choices before starting an experiment.
See [ESMFOLD.md](docs/ESMFOLD.md) for integrity checks, cost accounting and retry scope.

After placing the real evaluator sidecars beside each generated CIF:

```bash
uv run interaction-design evaluate artifacts/<run-id>
```

This command does not launch ODesign. It rejects changed generated structures and saves
each scoring attempt under `evaluations/<attempt-id>/`. If a sidecar is missing or invalid,
correct it and repeat the command; failed attempts remain available for inspection.

```text
artifacts/<run-id>/
  task.json, request.json, odesign_input.json
  execution.json, runtime-environment.json   # real local executor
  odesign.stdout.log, odesign.stderr.log    # real executors
  output/.../predictions/*.cif
  manifest.json, report.json, report.md     # generation snapshot
  status.json                              # latest stage outcome
  evaluations/<attempt-id>/
    manifest.json
    report.json, report.md                 # successful evaluation
    failure.json                           # failed evaluation
```

`run` now defaults to ESMFold2 evaluation and reads `--complex-config` or
`.cache/esmfold2-runtime.local.json`. Use `--evaluation-backend af3-sidecars` to
explicitly select this original sidecar evaluation path. A failed evaluation returns
a nonzero exit code and the preserved run location.
The timeout applies to the generation process, excluding preflight checksum verification;
timed-out local process groups are killed. This is a CLI workflow, not yet a durable job
queue with remote cancellation or GPU budget accounting.

The tested environment and the PD-L1 target setup are documented in
[ODESIGN_SETUP.md](docs/ODESIGN_SETUP.md).

## Evaluator artifact contract

For a prediction named `candidate.cif`, the current adapters look for:

```text
candidate.cif
candidate.af3.json
candidate.rosetta.json
```

The AF3 JSON uses the standard summary keys `iptm`, `ptm`, `chain_iptm`, `chain_ptm`, and
`chain_pair_pae_min`. The PyRosetta JSON contains numeric `ddg`, `sap_score`, and
`contact_molecular_surface`. Metric weights and pass thresholds live in the declarative
task under `evaluation.metric_rules`. These defaults are example settings, not a validated
screening protocol for every target. Protein–protein and protein–ligand tasks require
separate evaluator suitability checks and thresholds. Parsing sidecars does not execute
AF3/PyRosetta or establish experimentally confirmed binding.

## Code map

- `specs.py`: user-facing scientific schema and validation.
- `conversion.py`: domain task ↔ evedesign `System` ↔ upstream ODesign JSON.
- `generator.py` and `outputs.py`: evedesign generator contract and CIF parsing.
- `runtime/`: mock, local-process, and container execution backends.
- `scoring/`: AF3/PyRosetta artifact scorers and transparent ranking.
- `evaluation/monomer*.py`: saved binder preparation, offline batch execution,
  full-candidate reporting and descriptive diversity.
- `evaluation/complex.py`, `scripts/esmfold2_batch.py`: offline ESMFold2 complex
  execution, chain/sequence verification, interface confidence and design RMSDs.
- `evaluation/interface.py`, `evaluation/feedback.py`: mapped hotspot contacts,
  cross-chain clashes, geometry controls and immutable derived observations.
- `decisions.py`, `campaigns.py`, `campaign_tools.py`: fixed/feedback stopping policies,
  checked action execution, local model batches and saved-observation replay.
- `assets.py`, `manifest.py`, `report.py`: pinned external assets and provenance.
- `workflow.py`, `cli.py`: end-to-end orchestration and a thin CLI.

## Next engineering milestones

1. Add held-out targets and controls after the complete 16-candidate ESMFold2
   development evaluation, before making quality or Agent comparisons.
2. Round-trip evedesign `Interaction` objects into task constraints so more backends can
   share the same intent model.
3. Add structural diversity clustering and Pareto-front reporting instead of relying only
   on a weighted scalar rank.
4. Extract this directory into its own repository, choose the final project name, and add
   contribution docs plus an architecture decision log. CI already runs lint, tests, and
   package builds on Python 3.12 and 3.13.

ODesign and evedesign remain separate upstream projects and retain their own licenses and
citations. This package does not vendor either codebase or redistribute their weights.

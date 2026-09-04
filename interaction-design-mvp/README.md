# Interaction Design MVP

[![Interaction Design CI](https://github.com/ziiroo1126/MolClaw/actions/workflows/interaction-design-mvp.yml/badge.svg)](https://github.com/ziiroo1126/MolClaw/actions/workflows/interaction-design-mvp.yml)

This is a domain-first, reproducible biomolecular interaction-design workflow built on
the **evedesign core abstractions** and connected to **ODesign** as its first generative
backend. It is intentionally isolated from the existing MolClaw v1 application so the
package can be renamed, extracted, or published without rewriting the old product.

The project is more than an ODesign launcher:

```text
InteractionDesignSpec
        │
        ├──> evedesign System ──> ODesignGenerator ──> SystemInstance[]
        │                              │
        │                    mock / local / container
        │
        └──> AF3ConfidenceScorer ──> PyRosettaScorer ──> filter + rank
                                                        │
                                             report + run manifest
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
- Every run emits the resolved task, seeds, command, software versions, pinned model
  revisions, candidate artifacts, SHA-256 checksums, a JSON report, and a Markdown report.
- No model weight, chemical-component dataset, or run artifact is tracked by Git.

Not in this phase: an LLM agent, remote task queue, multi-user service, full web UI, or
launching AF3/PyRosetta themselves. The scorer boundary currently consumes their JSON
artifacts, which keeps those expensive tools independently deployable.

## Quick start without a GPU or model weights

Python 3.12 or newer is required because evedesign requires it. The dependency is pinned
to the reviewed evedesign 0.0.6 Git commit because that version is not yet on PyPI.

```bash
uv sync --extra dev
uv run interaction-design validate examples/ligand_binder.json
uv run interaction-design run examples/ligand_binder.json \
  --executor mock \
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
git -C /path/to/ODesign checkout 87b67dec1a26c0915286bf47c3dc7102635ea0cc

uv run interaction-design assets download --destination /path/to/model-assets
uv run interaction-design assets verify --destination /path/to/model-assets
```

The downloader invokes `hf download --revision <40-char-commit>`, checks the downloaded
revision with `hf cache verify`, and creates an `asset-manifest.json` containing a SHA-256
and size for every local file. Subsequent `assets verify` checks are offline and never
deserialize untrusted checkpoints.

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
  --cuda-visible-devices 0
```

`--generation-only` is explicit because upstream ODesign produces structures but not AF3
or PyRosetta JSON. Once evaluator services create the sidecars described below, the same
scorers and ranking policy can be applied without rerunning generation. A CUDA-enabled,
prebuilt image can instead be selected with
`--executor container --container-image ... --container-gpus all`.

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
task under `evaluation.metric_rules`; the defaults mirror the ODesign-pipeline binder
protocol while fixing its documentation/code boundary ambiguity.

## Code map

- `specs.py`: user-facing scientific schema and validation.
- `conversion.py`: domain task ↔ evedesign `System` ↔ upstream ODesign JSON.
- `generator.py` and `outputs.py`: evedesign generator contract and CIF parsing.
- `runtime/`: mock, local-process, and container execution backends.
- `scoring/`: AF3/PyRosetta artifact scorers and transparent ranking.
- `assets.py`, `manifest.py`, `report.py`: pinned external assets and provenance.
- `workflow.py`, `cli.py`: end-to-end orchestration and a thin CLI.

## Next engineering milestones

1. Add evaluator-runner plugins for AF3 and PyRosetta without coupling them to generation.
2. Round-trip evedesign `Interaction` objects into task constraints so more backends can
   share the same intent model.
3. Add structural diversity clustering and Pareto-front reporting instead of relying only
   on a weighted scalar rank.
4. Extract this directory into its own repository, choose the final project name, and add
   contribution docs plus an architecture decision log. CI already runs lint, tests, and
   package builds on Python 3.12 and 3.13.

ODesign and evedesign remain separate upstream projects and retain their own licenses and
citations. This package does not vendor either codebase or redistribute their weights.

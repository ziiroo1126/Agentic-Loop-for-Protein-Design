# AF3 / PyRosetta assessment

This is the retained optional AF3/PyRosetta protocol. The active protein-complex
workflow now uses [ESMFold2](ESMFOLD2.md), including real local GPU verification.
ESMFold2 results have their own reports and do not satisfy the old AF3/PyRosetta thresholds.

The `assessment` commands prepare and run a separate evaluation of **saved, two-protein
binder candidates**. They preserve generation records and keep refolded structures
separate from generated structures. Local execution adapters and import validation have
contract tests; real AF3/PyRosetta execution still requires an available deployment.

The initial protocol uses binder A and target B, one AF3 seed/sample per candidate,
followed by the unchanged ODesign-pipeline PPI RosettaScripts protocol. Source and
license attribution are packaged with `config/ppi.xml`. Ligands, cyclic binders, and
complexes with more than two chains are rejected by this protocol; they need their own
validated scoring setup.

## Prepare the real PD-L1 candidate

From `interaction-design-mvp/`:

```bash
uv run interaction-design assessment prepare \
  artifacts/20260905T031949Z-042eb8af --budget-seconds 1800
```

The command prints a job directory under `<generation-run>/assessments/`. It stores an
immutable request manifest, candidate/chain mapping, native AF3 JSON, the PPI protocol,
and input checksums. AF3 inputs contain sequences, not the generated binder coordinates.
The binder's MSA and templates are explicitly empty.

Select the target features deliberately:

| `--msa-mode` | Target features | AF3 data pipeline |
| --- | --- | --- |
| `search` (default) | Search AF3's local databases | Enabled |
| `provided --target-data target_data.json` | Reuse the target's AF3 `_data.json` with inline MSAs/templates | Disabled |
| `none` | Empty MSA and templates | Disabled |

The provided target sequence and MSA query must match the candidate's target exactly.
External MSA/template paths are not accepted in this first importer; supply the native
AF3 processed JSON with inline contents. No automatic downgrade to `none` occurs when
databases are unavailable. Results from different feature policies should be compared
as different evaluation protocols.

## Configure and run

Copy `config/evaluation-runtime.example.json` to a local ignored location, such as
`.cache/evaluation-runtime.local.json`. Replace its paths and all-zero Git revision with
the actual AF3 deployment revision. Set unused runtime sections to `null` if results
will be imported from elsewhere.

```bash
uv run interaction-design assessment check <job-directory> \
  --config .cache/evaluation-runtime.local.json
uv run interaction-design assessment run <job-directory> \
  --config .cache/evaluation-runtime.local.json
```

Preflight checks executable paths, module availability, AF3 source revision/cleanliness,
parameter presence, and database-directory presence when required. It records missing
requirements in `preflight.json`; these are basic deployment checks, not validation of
all database contents or CUDA compatibility. The run command records a blocked stage
without launching it if prerequisites are missing.

The local AF3 command calls `run_alphafold.py` with explicit input/model/output paths,
data-pipeline mode, and `--num_diffusion_samples=1`. Before execution it captures the
Python environment and actual AF3 parameter hashes. The PyRosetta worker uses a separate
Python executable with `pyrosetta` and `biotite` installed; it converts the accepted AF3
model to PDB and invokes the upstream XML through JD2. It records ddG, SAP, contact
molecular surface, random seed, software version, protocol hash, and input-model hash.

Both tools are external installations; this package does not download parameters,
databases, or proprietary distributions. Reuse local caches and use command-scoped
direct connections for any required setup downloads, following the workspace rules.

## Failure recovery and cost accounting

Successful stage receipts are reused when the same job is run again. If AF3 completes
and Rosetta fails, fixing Rosetta and repeating `assessment run` does not refold again.
Each process attempt retains its command, environment, logs, elapsed time, and failures.
A per-job file lock prevents concurrent invocations from duplicating that job's work.

`--budget-seconds` caps the accumulated **local subprocess wall time**, including failed
attempts. Each process gets the smaller of the stage timeout and the remaining budget.
This excludes preflight, hashing, report construction, and externally imported compute;
it is not GPU utilization accounting. The runtime's `cpu_threads` controls OMP threads
and AF3 MSA-tool threads, not a global CPU quota. Different jobs are not globally scheduled.

A hard process/host crash can leave an unfinished execution record. Retrying stops and
asks the operator to inspect the old process; it does not assume that the GPU worker
has stopped. A completed job's accepted results cannot be overwritten through the
import command. Prepare a new assessment to evaluate another protocol or replace results.

## Import results from another deployment

Use the prepared `inputs/candidateNNNN.json` on the external AF3 installation, with one
diffusion sample. Import the native output folder using the original candidate ID
recorded in the job manifest:

```bash
uv run interaction-design assessment import-af3 <job-directory> \
  --candidate <candidate-id> --source /path/to/af3-output
```

Required files are the job's `_model.cif`, `_summary_confidences.json`, `_data.json`, and
native ranking CSV (`ranking_scores.csv` or `<job-name>_ranking_scores.csv`). The importer
checks job name, seed/sample count, chains, sequences, explicit feature policy, coordinate
finiteness, and confidence metrics. It handles AF3's conversion of an empty MSA to a
query-only alignment. Output terms are retained when present. Ambiguous output folders
and choosing the best of more samples than requested are rejected.

Run `src/interaction_design/evaluation/rosetta_worker.py` under the external PyRosetta
Python environment, using the accepted AF3 model and the job's `ppi.xml`:

```bash
/path/to/pyrosetta-python src/interaction_design/evaluation/rosetta_worker.py \
  --input-cif /path/to/accepted-model.cif --protocol-xml /path/to/job/ppi.xml \
  --output /path/to/metrics.json --seed 1
uv run interaction-design assessment import-rosetta <job-directory> \
  --candidate <candidate-id> --source /path/to/metrics.json
uv run interaction-design assessment report <job-directory>
```

Rosetta imports must name the accepted AF3 structure hash, protocol hash, binder/target
chain IDs, and the requested seed. Scores for the original ODesign geometry are not
interchangeable with scores for the refold. The final report uses the AF3 chain mapping
and retains both structures. Imported results are labelled `external-import`; matching
files establish consistency with the request, not independent authentication of the
external predictor or its runtime cost.

## Format references

- [AF3 inputs and explicit MSA semantics](https://github.com/google-deepmind/alphafold3/blob/main/docs/input.md).
- [AF3 native outputs and confidence definitions](https://github.com/google-deepmind/alphafold3/blob/main/docs/output.md).
- [ODesign-pipeline PPI scoring protocol](https://github.com/OTeam-AI4S/ODesign-pipeline/blob/644b5ddfa25c395e84c527eb460be3552c307a04/filter/rosetta_cmds/ppi.xml).

These checks establish computational evaluation provenance. They do not establish
experimental binding. Ranking scores are relative scores within the candidate set,
not probabilities of experimental success.

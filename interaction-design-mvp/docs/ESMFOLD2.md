# Offline ESMFold2 complex evaluation

The current protein binder workflow is **ODesign → optional ESMFold v1 monomer
check → ESMFold2 binder/target co-fold → confidence and design-consistency report →
interface geometry feedback**.
The model is Biohub's standard **ESMFold2**, with ESMC-6B embeddings. It is distinct
from ESMFold v1, ESM-2 and ESMFold2-Fast. AF3 and PyRosetta remain optional future
evaluations; neither is required by this path.

The first protocol supports two linear, canonical protein chains: binder A and
target B, remapped from the generation task. It supplies only their sequences to
the predictor, with no external MSA or structural template. Generated coordinates
are retained solely for downstream comparison. Ligands, modified/cyclic proteins
and larger assemblies need a separately validated adapter.

## Run saved candidates

From `interaction-design-mvp/`, configure the existing external Biohub Python
environment using `config/esmfold2-runtime.example.json`. No installation or model
download occurs during execution. The working environment used for the development
pilot was `/nvme-data3/yusen/micomamba/envs/esm2/bin/python` (Python 3.12, Torch
2.5.1+cu124, esm 3.3.0, Biohub Transformers 4.57.6).

```bash
.venv/bin/alpd complex prepare artifacts/<generation-id> \
  --budget-seconds 1800
.venv/bin/alpd complex run <printed-job-directory> \
  --config .cache/esmfold2-runtime.local.json
.venv/bin/alpd complex report <printed-job-directory>
```

Repeat `--candidate <exact-candidate-id>` at preparation to evaluate a specified
subset. The default selects all candidates in the saved generation. Inputs and
results are written under `artifacts/complex-assessments/`, leaving the original
generation and previously sealed experiments unchanged. `--artifacts` changes this
destination. Preparation checks generation hashes, candidate identities and exact
chain sequences against the original CIF.

For a new protein generation, the existing `run` command also accepts
`--complex-config <runtime.json>` in place of `--generation-only`. This runs the
same saved-candidate evaluation after successful generation. If evaluation fails,
the generation and printed complex job remain available for retry.
The default evaluation backend is now `esmfold2`; omitting `--complex-config`
uses `.cache/esmfold2-runtime.local.json`. Missing runtime configuration fails before
generation. The former sidecar path requires `--evaluation-backend af3-sidecars`.

## Frozen protocol and runtime

`config/esmfold2-ppi.protocol.json` is copied into each request manifest before
inference. It pins both model snapshot revisions, seed 1, 20 folding loops, 100
diffusion steps, one sample per candidate, LM-embedding dropout 0.3, and no MSA.
Folding weights use float32 with the upstream bfloat16 autocast; ESMC uses bfloat16.
TF32 is disabled. The folding kernel backend is the reference implementation;
upstream attention kernels retain their own dispatch logic. The protocol's ipTM
sorting is descriptive, with candidate ID as a deterministic tie-break and no
acceptance thresholds. Changing this protocol requires a new job.

The runtime JSON supplies Python, both snapshot paths, the local `ccd.pkl`, GPU,
CPU threads and a process timeout. Snapshot directory revisions must match the
protocol. The worker hashes all actual checkpoint shards and verifies HF LFS blob
hashes when available, records the CCD hash, package versions, source revisions,
precision and GPU. Cached CCD loading uses the upstream pickle reader; use the
trusted local Biohub asset. Folding checkpoint key mismatches are fatal.

The pilot required `unset_ld_library_path: true`: an inherited CUDA library path
caused a `libnvJitLink` symbol error. The launcher removes that variable **only
from this worker's environment**. It also sets offline Hugging Face flags and
writable process-local Matplotlib/temporary directories. Shared environments,
Clash and global proxy settings are unchanged. No downloads were needed.

## Outputs and validation

- `complex.cif`: native ESMFold2 two-chain structure, with CIF pLDDT on 0–100.
- `confidence.npz`: raw residue-averaged pLDDT on 0–1, PAE in Å, chain labels,
  pairwise chain ipTM and unrounded coordinates.
- `metrics.json`: pTM/ipTM, binder and target mean pLDDT, mean PAE in both
  cross-chain directions, synchronized fold timing and allocator memory peaks.
- Job reports: `report.json`, `report.md`, `candidates.csv`, including binder
  all-CA RMSD and binder RMSD after alignment on the target, relative to the
  generated design. These are not DockQ scores or comparisons to an experimental complex.
- Request/output manifests, archived worker, runtime configuration, loading
  diagnostics, provenance, stdout/stderr and completion receipts.

Before accepting results the application checks the complete candidate set,
request hashes, policy, exact chain sequences, finite coordinates/confidences,
raw-versus-CIF coordinates and pLDDT scale, and recomputes the summary pLDDT/PAE
and structural alignments independently using Biotite. Confidence values are not
binding probabilities, and AF3 thresholds cannot simply be transferred to ESMFold2.

## Recovery and limits

A job lock prevents duplicate concurrent execution. Re-running a completed job
verifies its artifacts and regenerates the report without loading a model. Failed
attempts retain their logs and outputs and consume the job's cumulative subprocess
wall-time budget; the current retry reruns the entire batch. It does not reuse
individual candidates from a partially failed batch. An unfinished prior process
must be inspected before retrying.

The worker loads each model once and predicts candidates serially. Timing includes
input preparation, folding and decoding; per-prediction memory peaks include resident
model allocation but exclude other processes and the CUDA context. The job budget
also includes model hashing/loading and failed subprocess attempts, and excludes
report generation. The parent ODesign job has a separate budget. There is no global
GPU scheduler or LLM budget manager yet.

## Verified development run

The preselected high/middle/low monomer-confidence representatives (generation
seeds 46/54/50) have all completed ESMFold2 co-folding with the same 114-residue PD-L1
fragment. No candidates were reselected after seeing complex results. The run used
one L20, peaked at 13.44 GiB of PyTorch allocation, and took 168.31 seconds including
checkpoint hashing, loading and all three predictions. Individual folds took
8.62–13.49 seconds. See [the recorded results](../../docs/evidence/ESMFOLD2_complex_pilot.md).

The remaining 13 candidates have since completed under the same protocol, without
rerunning the original three. All 16 now have complex metrics. The two batches used
370.01 seconds of external process time in total, with 13.44 GiB peak allocation.
`scripts/summarize_complex_batches.py` validates disjoint batches, identical model and
software provenance, and full generation coverage before writing a combined JSON/CSV.
The separate CPU-only `complex feedback` command now adds hotspot contacts, cross-chain
clashes and contact retention without modifying these completed model reports. See
[the geometry protocol and usage](INTERFACE_FEEDBACK.md). The application test suite
has 93 passing tests, including geometry, feedback integrity and campaign controller checks.

References: [Biohub ESM](https://github.com/Biohub/esm),
[ESMFold2 model card](https://huggingface.co/biohub/ESMFold2).
The pilot uses cached source revisions recorded in provenance, not an unpinned
installation of today's upstream default branch.

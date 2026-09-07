# Offline ESMFold monomer check

`scripts/esmfold_smoke.py` runs a saved binder sequence through the Transformers
implementation of ESMFold v1. It is a standalone Python 3.10-compatible worker for
an existing model environment. It does not import or alter the application's
AF3/PyRosetta assessment protocol.

The worker accepts a local model snapshot containing `config.json` and
`pytorch_model.bin`. Loading uses `local_files_only=True`, `weights_only=True`, and
explicit offline mode. The current worker requires a PyTorch version supported by
Transformers' safe checkpoint loader. It does not install dependencies or download
weights. It hashes the actual files and compares HF LFS blob hashes when available;
this is a local consistency check, not a fresh verification against the Hub.

## Input and execution

A minimal request JSON contains a candidate identifier and one canonical protein
sequence:

```json
{
  "candidate_id": "real_smoke_pdl1_binder_seed_42_bb_0_seq_0",
  "sequence": "MVLTYVGNESDADGQEFKEDAEELALANGNGDINEEDTGVGERVVTAEIGDHVLVIILVD"
}
```

To compare against a generated design, include `reference_ca`, a list of one
three-dimensional C-alpha coordinate per input residue, and `reference`, a
provenance object containing the original structure path, SHA-256, chain and atom
selection. The caller must verify that the reference sequence exactly matches the
request; coordinates alone do not establish sequence identity. For the real PD-L1
check, the binder chain was identified by exact sequence in the immutable generation
CIF, and its hash was checked against the original generation manifest.

Run under an existing compatible model environment, using a new output directory:

```bash
env HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  PYTHONDONTWRITEBYTECODE=1 CUDA_VISIBLE_DEVICES=0 \
  OMP_NUM_THREADS=4 MKL_NUM_THREADS=4 \
  /path/to/model-environment/bin/python scripts/esmfold_smoke.py \
  --model-dir /path/to/local/esmfold_v1/snapshot \
  --input /path/to/request.json \
  --output-dir /path/to/new/prediction \
  --cpu-threads 4 --chunk-size 128
```

Keep temporary files and package caches on a filesystem with sufficient space.
For the recorded run, the application's `run_logged` wrapper also saved the exact
command, stdout/stderr, process status and elapsed time, and imposed a 900-second
process timeout. The worker itself does not implement a scheduler or timeout.

## Outputs and interpretation

- `binder.pdb`, `binder.fasta`: predicted monomer structure and input sequence.
- `prediction.npz`: unrounded coordinates, atom masks, original atom confidence
  values and predicted aligned error.
- `metrics.json`: confidence, timing and PyTorch allocator memory peaks. An optional
  design comparison uses all sequence-matched C-alpha atoms and one rigid
  superposition, without trimming residues or rejecting outliers.
- `alignment.npz`: original reference C-alpha coordinates and aligned refold
  coordinates, when a reference was supplied.
- `provenance.json`, `loading.json`, `manifest.json`: input/model/software hashes,
  loader consistency checks and output checksums. Failures retain `failure.json`.

The chosen precision is float32 for both the language model and folding trunk, with
TF32 disabled, batch size one and a 128-residue attention chunk. The saved model
configuration controls the number of trunk passes. Checkpoint differences cause
failure, with two audited exceptions for Transformers 4.52.4 and rotary embeddings:
the legacy absolute-position tensor is unused, and the missing ESM auxiliary contact
head is not part of the folding forward pass. A hook raises an error if that head is
ever invoked. These exceptions and all loader messages are recorded; missing folding
weights remain fatal.

Transformers 4.52.4 returns atom-level pLDDT on a **0–1** scale. The worker checks
that range, preserves the raw values in NPZ and exports metrics/PDB B-factors on the
conventional **0–100** scale. It reports both the mean over existing atoms and the
mean over C-alpha atoms; these averages have different weightings.

Loading and each inference are timed separately with CUDA synchronization. Current
metrics use `timing.inference_seconds`; historical smoke artifacts retain their
original `first_inference_seconds` key. Each prediction resets the PyTorch peak
counters with the model resident. Peaks include resident model allocation and
inference but exclude CUDA context memory and other processes. A single short
prediction is not a throughput benchmark.

This check receives **only the binder sequence**. It can measure confidence and
structural self-consistency, but does not evaluate binding to PD-L1, provide an AF3
interface score or satisfy the original PPI acceptance criteria. No threshold is
chosen after seeing the result. A useful next experiment would test whether these
monomer metrics help allocate the budget for subsequent complex evaluations.

References: [ESMFold v1 model card](https://huggingface.co/facebook/esmfold_v1),
[Transformers 4.52.4 implementation](https://github.com/huggingface/transformers/blob/v4.52.4/src/transformers/models/esm/modeling_esmfold.py).

## Saved generation batches

The application CLI extracts the protein binder according to the task's binder
index, verifies the original CIF checksum and exact sequence/CA correspondence,
and saves immutable requests without changing the generation manifest:

```bash
uv run interaction-design monomer prepare artifacts/<generation-id> \
  --protocol config/baseline-pdl1-16.protocol.json
uv run interaction-design monomer run <printed-job-directory> \
  --python /path/to/existing/model-environment/bin/python \
  --model-dir /path/to/local/esmfold_v1/snapshot \
  --gpu 0 --timeout 900
uv run interaction-design monomer report <printed-job-directory>
```

`--protocol` is optional. The supplied PD-L1 development protocol fixes 16 seeds,
60 residues, model versions, metrics, representative selection and process time
caps. Preparation checks candidate count, seeds and length; execution also checks
the snapshot directory revision and applies the chunk size, CPU threads and time
cap. This is not a general validator of arbitrary scientific protocols. Freeze
and check the generation task before launching ODesign, as in the recorded pilot.

`scripts/esmfold_batch.py` loads one model and processes sequences serially, with
batch size one. Both worker sources are archived per attempt and included in the
wheel. The application provides a job lock, process timeout, logs and checksums.
The timeout is cumulative external-process wall time across attempts within this
monomer job. It excludes preflight hashes and report generation, and does not
account for a separate ODesign run. In the pilot, failed and successful generation
attempts are additionally accounted for in the experiment-level evidence.

Calling `run` on a completed job verifies and returns its stored output; it does
not recompute with newly supplied runtime options. Create a new job to compare
models or settings. A failed batch retains completed candidate outputs and the
failure location, but a retry currently reruns the whole batch within the remaining
budget. Candidate-level partial reuse is not implemented.

`report` verifies request identities, output hashes, PDB sequence/coordinate and
confidence scales, and independently recomputes the all-CA RMSD using Biotite.
It emits `report.json`, `report.md`, `candidates.csv`, `candidates.fasta` and
`diversity.npz`. Diversity requires equal-length sequences and describes
same-position identity and pairwise rigidly aligned **generated** CA backbones;
it does not establish evolutionary diversity or binding diversity.

For a single protein target, `complex_followup/` contains native AF3 requests for
the fixed high/middle/low confidence representatives. The target MSA is left for
AF3 search, the binder is query-only, and no generated structure is supplied as a
template. These files are prepared inputs; AF3 has not run and its sampling/runtime
protocol must still be configured.

## Real 16-candidate development pilot

All 16 PD-L1 candidates (seeds 43–58) were generated and refolded on one L20 using
cached models and existing environments. See the
[full results and cost record](../../docs/evidence/PDL1_16_baseline.md).
The earlier seed 42 was excluded from this batch. No pass threshold was introduced
after seeing results, and this single-target development set is not a held-out
benchmark or evidence of Agent gains.

The first generation attempt exposed an environment issue: a long `TMPDIR` caused
PyTorch's `torch_shm_manager` to fail with `Invalid argument`. A one-element shared
CPU tensor reproduced the failure; a short temporary directory succeeded. The
recorded retry used a command-scoped short `TMPDIR`, retained the scientific
configuration, and deducted the failed attempt's time from the generation budget.
No upstream code, shared environment, model or proxy setting was changed.

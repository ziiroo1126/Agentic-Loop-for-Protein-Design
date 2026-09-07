# External wet-lab candidate benchmark

`benchmark prepare/observe/apply/report` evaluates selection on a public, already
tested candidate pool. It uses published complex scores and experimental calls.
It does not generate proteins, run a folding model, or request an LLM API key.
The host supplies the decision model, as in the main pipeline.

This is a separate feature contract from `screen`: ESMFold2 complex ipTM/ipSAE and
DockQ cannot be substituted for native monomer confidence, hotspot coverage or
binder RMSD. Results assess retrospective ranking on an external pool, not the
validity of MolClaw's entire generator or native screening heuristic.

## Data and protocol

The source is [Anthropic/claude-protein-binder-design](https://huggingface.co/datasets/Anthropic/claude-protein-binder-design),
pinned to `9e1b81696da46835e9e9cde9a3da976e0abc92ab`. Data/docs are CC BY 4.0;
upstream scripts are MIT. Retain attribution and the source license when sharing.
The importer projects only summary identifiers, targets, composite/vendor calls,
and selected score columns. It never reads or emits sequence columns.

`config/external-benchmark-v1.json` freezes seven targets, feature aggregation,
baselines, label handling and uncertainty. The implementation rejects unsupported
rule changes; only quota is configurable within this version. PD-L1 is the
development target. BBF-14, EGFR, IL-7Ra, MBP, TREM2 and TrkA are evaluation targets.
Each has 90 candidates in the pinned release. The default quota is ten per target.

All features use primary-target 1:1 predictions and exactly seeds 0–4. The fields
are five-seed ESMFold2 full medians of ipTM, minimum directional ipSAE and scDockQ;
the fourth is the median of the ef2full, ef2fast and ptxv2 ipSAE medians. These are
post-hoc uniform predictions, not the source campaigns' original selection-time
scores. A missing score in any seed makes that aggregate unknown. Missing whole
seed records, duplicate joins, invalid numbers and incomplete model groups fail.

The composite `binder_final` is preserved without recoding: it is the source
release's combined vendor/refit assessment with sensorgram review. It is not a
single vendor's binary assay or a new measurement. Missing composite calls are
excluded and counted. Separate vendor sensitivity results use only `binder` and
`non_binder`; expression failures, unmeasured and inconclusive calls remain
unknown for those endpoints. Selections are never changed for sensitivity analyses.

Fixed order uses anonymous IDs derived from a stored random salt and source UUID.
This prevents source rank, model name and row order from determining selection.
Single-score baselines rank each available value above missing values. The strongest
development single field is chosen by full-pool PD-L1 AP, missing scores tied last.
The heuristic averages four within-target midrank percentiles with missing=0 and
equal feature weights. Correlated metrics are not treated as independent evidence.
All policies use the identical six-decimal public scores and pool; ties use ID order.

## Run

Reuse a cached snapshot first. If downloading is needed, inspect individual file
sizes and use direct connections with command-scoped removal of uppercase and
lowercase proxy variables. Download only required tables/docs. Multi-file HF CLI
requests can enumerate the entire large repository: single-file requests avoid it.
The importer itself has no network operation. It requires an existing Python with
PyArrow; the benchmark executor uses the main project environment.

From `interaction-design-mvp`:

```bash
"$PARQUET_PYTHON" scripts/import_anthropic_benchmark.py \
  --snapshot "$PINNED_SNAPSHOT" --output "$NEW_IMPORTED_JSON"
.venv/bin/interaction-design benchmark prepare \
  --imported "$NEW_IMPORTED_JSON" --protocol config/external-benchmark-v1.json \
  --output "$NEW_SESSION"
.venv/bin/interaction-design benchmark observe "$NEW_SESSION" --pool pool_01
.venv/bin/interaction-design benchmark apply "$NEW_SESSION" --pool pool_01 \
  --submission "$HOST_SUBMISSION"
# Repeat observe/apply for all six pools, then reveal once:
.venv/bin/interaction-design benchmark report "$NEW_SESSION"
```

`--submission -` accepts JSON on stdin. The submission has exactly:

```json
{
  "request_sha256": "copy from request",
  "candidate_ids": ["selected visible IDs, exactly quota"],
  "reason": "Explain the selection using available evidence",
  "evidence": [{"candidate_id": "selected ID", "field": "ef2_ipsae", "value": 0.5}],
  "actor": {"harness": "codex", "model": null, "agent_id": null}
}
```

Every selected ID needs at least one exact finite evidence value. Unknown model
metadata stays null. A valid submission is committed once; identical repeats are
idempotent and replacement is rejected. Report requires all six valid commitments.
The manifest binds requests, normalized input, private labels, baseline selections,
protocol and implementation copies. Receipt commitments bind each pool to its file.
Changed implementations cannot resume a frozen session; preserve the archived code.
An interrupted write leaves evidence for inspection and fails closed.

## Interpretation and access

Give each decision host only its request and selection instructions. During selection,
do not inspect `private.json`, `imported.json`, `development.json`, `baselines.json`,
source tables, other requests or reports. The shared-filesystem restriction is an
experimental access protocol, not enforced OS isolation. Public feature vectors can
still identify records and training contamination cannot be excluded.

Report contains per-target hits/quota, prevalence, random expected hits, enrichment,
AUROC/AP with observed-score sample sizes, and vendor-specific coverage. AUROC/AP
are null when undefined, including a zero-positive target. Score-validity metrics
exclude missing scores and explicitly report coverage; development feature choice
instead uses the whole pool with missing ranks last. Macro values weight targets
equally. Descriptive 95% bootstrap intervals resample targets, not candidates, with
2,000 replicates and fixed seed. Paired precision differences compare the host with
the heuristic and development-selected single score.

This is exploratory, retrospective and target-disjoint. Published target-level
results were already known when the protocol was developed; it is not a pristine
holdout or a preregistered study. The candidate pool was selected upstream, related
designs are correlated, and six targets give limited independent replication.
Do not infer prospective success, protein function, GPU savings, adaptive feedback
benefit, or new MolClaw wet-lab hits from these results.

# Repeating host decisions on a frozen benchmark

`benchmark-repeat prepare/observe/apply/report` measures decision variability on
an already completed external benchmark. It preserves the original source,
copies exact requests and baseline selections, and creates two to five fresh
repetitions (default two). Each decision should use a fresh host context with
only the assigned request and common instruction file.

These are decision repetitions, not new biological candidates, targets or assays.
Previously disclosed targets remain development evidence. This mode does not
fit score calibration or create an independent holdout.

From `interaction-design-mvp`:

```bash
.venv/bin/alpd benchmark-repeat prepare \
  --source-session "$COMPLETED_BENCHMARK" \
  --instructions "$REFERENCE_HOST_INSTRUCTIONS" \
  --repetitions 2 --output "$NEW_STUDY"
.venv/bin/alpd benchmark-repeat observe "$NEW_STUDY" \
  --trial repeat_01 --pool pool_01
.venv/bin/alpd benchmark-repeat apply "$NEW_STUDY" \
  --trial repeat_01 --pool pool_01 --submission "$HOST_SUBMISSION"
# Commit every pool in both repetitions, then reveal together:
.venv/bin/alpd benchmark-repeat report "$NEW_STUDY"
```

`--submission -` accepts the same JSON envelope as the
[external benchmark](EXTERNAL_BENCHMARK.md). Requests and their hashes are identical
across repetitions; `--trial` routes each submission to its state. The v1 validator
enforces exact evidence, IDs, quota, receipt checksums and idempotence. Reusing a
prior submission or a non-null actor ID for another decision is rejected, including
on report validation. Unknown actor metadata remains null and unauthenticated.

The original completed run is copied under `historical/` and its copied report
recomputed for consistency, without writing to the source. New repetitions contain
frozen v1 inputs and empty commitment stores. Input, instruction and implementation
checksums are pinned. Absolute and parent-traversing snapshot paths are rejected.

The coordinator verifies every new submission before computing trial outcomes.
Do not call nested v1 reports or inspect private files to obtain partial feedback.
This is a protocol, not shared-filesystem isolation.

The report separates four quantities:

- **Primary new runs:** total hits per run, mean/range/sample SD and paired precision
  differences from frozen baselines. Per-target precision is averaged over new
  runs first; descriptive bootstrap resamples targets. Repeated decisions do not
  increase the independent target count.
- **Historical reference:** shown separately and excluded from the primary mean.
  Pairwise Jaccard overlap is reported for new runs alone and for all runs, with
  equal pair weights within each target.
- **Endpoint sensitivity:** composite and separate measured vendor calls retain
  their own denominators; selected IDs stay fixed.
- **Random reference:** exact hypergeometric counts for uniform sampling without
  replacement within each pool, convolved across independent selection draws.
  The discrete central 95% interval conditions on observed labels. It is not a
  host confidence interval, prospective success interval, or superiority test.

The caller supplies the intended original instruction file; all new runs use that
same copy. Unknown model versions, temperature, seeds and token usage remain null.
Fresh contexts do not establish stochastic independence. Public-data contamination
and upstream candidate selection remain limitations. This module runs no protein
model, wet-lab experiment or external LLM service.

# Retrospective public-data selection

Use this mode when the user asks to evaluate selection against existing public
experimental outcomes. The project executor supports `benchmark prepare/observe/apply/report`.
Use the shared CLI launcher through the host's shell tool; existing native DSH
`molclaw_pipeline_*` tools are for the design pipeline, not this benchmark contract.
No separate LLM client or credentials are needed.

Prepare with a normalized local import, the supported
`interaction-design-mvp/config/external-benchmark-v1.json`, and a fresh directory.
The source importer and full methodology are in
`interaction-design-mvp/docs/EXTERNAL_BENCHMARK.md` in the project checkout.
Preparation returns six anonymous pool request paths.

For each pool, run `benchmark observe SESSION --pool POOL_ID`. Select exactly the
visible quota using only `payload.candidates` and the supplied feature definitions.
These are external precomputed complex scores; do not reinterpret them as native
monomer confidence, hotspot coverage, or new MolClaw predictions. Missing is unknown.
No calibrated cutoff is supplied. Explain how the evidence supports your choice.

Submit `benchmark apply SESSION --pool POOL_ID --submission FILE` (or `-` for stdin).
The complete JSON envelope is:

```json
{
  "request_sha256": "request hash",
  "candidate_ids": ["exactly quota selected IDs"],
  "reason": "selection rationale",
  "evidence": [{"candidate_id": "selected ID", "field": "ef2_ipsae", "value": 0.5}],
  "actor": {"harness": "actual host", "model": null, "agent_id": null}
}
```

Include at least one exact finite score for every selected ID. Do not invent actor
metadata. Keep original requests and responses. A submitted selection is final;
identical repeats are allowed, conflicting replacements are rejected.

Do not read labels, private identity mappings, imported tables, baseline selections,
development metrics, other pool inputs, or archived outcomes to make a decision.
Use fresh decision sessions/subagents with only assigned requests when evaluating
independent pools. This is a protocol boundary; it does not isolate the filesystem.
After all six submissions are committed, the coordinator runs `benchmark report`.

Report per-target selections and measured-label outcomes with the source endpoint,
coverage, baselines and limitations. This retrospective comparison does not establish
new generation success, GPU savings, prospective hits, or superiority of an LLM.

For repeated decisions on a completed benchmark, the coordinator uses
`benchmark-repeat prepare --source-session SESSION --instructions FILE --repetitions 2
--output NEW_STUDY`. Read the project's `docs/BENCHMARK_REPETITIONS.md` for details.
Observe/apply additionally take `--trial repeat_01` (or the returned trial ID).
Use fresh decision contexts with only the assigned request and common instructions;
report only after every pool in every new repetition commits. Keep the historical
run separate from primary new-run averages. Repeated decisions are not new targets
or assays. This mode also uses the shared CLI through the host's shell tool.

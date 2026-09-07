# Harness-driven candidate screening

`screen` separates candidate selection from scientific execution. Codex, Claude Code,
or another host reads the visible request and returns a JSON submission; ALPD checks
it and evaluates only the selected candidates. No second model provider or API client
is configured inside ALPD.

Prepare from a completed ESMFold monomer job, then choose saved-result replay or a
local ESMFold2 runtime. Run from the `interaction-design-mvp` directory:

```bash
alpd screen prepare "$MONOMER_JOB" --strategy harness \
  --feedback "$COMPLETED_FEEDBACK" --batch-size 2 --max-evaluations 4
alpd screen observe "$SESSION_DIR" > /tmp/alpd-request.json
# The host reads this request and writes its response according to response_schema.
alpd screen apply "$SESSION_DIR" --decision /tmp/alpd-decision.json
alpd screen observe "$SESSION_DIR"
```

`screen apply SESSION_DIR --decision -` accepts the full submission on standard input
for native host adapters. The [DeepSeek Harness bundle](../../plugins/alpd/skills/alpd/references/deepseek-harness.md)
registers prepare/observe/apply tools and sends this JSON through the host's bash tool.

Replace `--feedback` with `--complex-config "$ESMFOLD2_RUNTIME"` to run new complex
predictions. Live mode also supports `--tool-wall-budget-seconds`; replay rejects that
option because CPU retrieval does not simulate GPU inference costs. Generation and
monomer checks occurred before this stage and must be included separately in a full
pipeline cost comparison. A failed live step preserves its cost and artifacts and
requires inspection before further execution.

The initial request exposes mean monomer CA pLDDT, monomer/design aligned RMSD, original
generated-structure hotspot coverage and clash residue counts, plus pairwise sequence
identity. It does not expose raw sequences, paths, or unseen complex results. After a
selection, observed complex ipTM, hotspot coverage, clash residue counts and binder RMSD
after target alignment become visible. These are diagnostics, without binding labels or
calibrated pass thresholds.

The wrapper is `{request_sha256, payload, response_schema}`. Its schema describes the
entire response, including `decision` and `actor`. `actor.model` and `actor.agent_id` may
be null when the host does not expose them. This metadata is self-reported, not authenticated;
unknown token usage remains null. The request hash binds each decision to a session,
step and visible state. Numeric evidence must match the serialized value exactly.

`apply` returns `applied` or `already_applied`; always call `observe` again. The executor
records its own stop at the candidate/time limit, and `observe` then returns
`status: completed` plus the report. An identical repeated submission does not rerun a
completed evaluation; conflicting or stale submissions are rejected. Failed or ambiguous
in-flight work is never automatically retried. Input snapshots, receipt checksums and
history reconstruction catch accidental changes; these checks are not a security
boundary against a process with full write access to the same files.

For automatic baselines, prepare the same pool with `--strategy fixed` or `heuristic`
and call `screen run "$SESSION_DIR"`. Fixed uses seed order. The heuristic chooses the
pre-metric Pareto front, then favors sequence diversity relative to already evaluated
and current-batch selections. Both get the same visible fields and limits. With
`--strategy harness`, `run` returns the pending request for an external host to decide.

In replay, the host must not inspect `replay.json`, old summaries, or unselected complex
outputs. This is an experimental information-access rule, not filesystem isolation.
For stronger separation, give a decision subagent only the observed JSON and record its
input and actual response. A coding host that already read the full outcomes should not
be treated as a blinded decision policy.

See the [portable skill and host adapters](../../plugins/alpd/README.md). This first
skill specializes in selecting existing candidates; generation remains available through
the core CLI and the earlier `campaign` controller. A complete natural-language task to
generation, selection, export workflow and independent policy experiments remain further work.

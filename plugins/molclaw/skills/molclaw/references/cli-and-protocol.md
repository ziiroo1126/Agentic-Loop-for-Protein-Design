# CLI and decision protocol

The shared skill supplies instructions and a thin launcher; the DeepSeek bundle also
exposes native tools for this protocol. It needs an existing MolClaw
checkout and installed `interaction-design` CLI with the `pipeline` and `screen` commands. Live mode
also needs a configured runtime and locally available model assets. Dependencies,
models and provider credentials are managed separately; this package downloads none.

## Locate the executor

Set `MOLCLAW_PROJECT_ROOT` to the repository root containing `interaction-design-mvp/`.
For a source checkout, run this Bash setup from the repository root:

```bash
MOLCLAW_PROJECT_ROOT="$PWD"
MOLCLAW_SKILL_DIR="$MOLCLAW_PROJECT_ROOT/plugins/molclaw/skills/molclaw"
molclaw() {
  python3 "$MOLCLAW_SKILL_DIR/scripts/molclaw.py" \
    --project-root "$MOLCLAW_PROJECT_ROOT" -- "$@"
}
molclaw pipeline --help
```

When using a cached plugin or standalone skill, assign `MOLCLAW_PROJECT_ROOT` to the
actual checkout and `MOLCLAW_SKILL_DIR` to the directory containing the loaded
`SKILL.md`, then use the same function. These are shell-local variables; this setup
does not change global configuration.

Executable selection is explicit `--cli`, then `MOLCLAW_CLI`, then
`interaction-design-mvp/.venv/bin/interaction-design`, then an existing
`interaction-design` on `PATH`. `--cli` and `MOLCLAW_CLI` are executable paths, not shell
command strings. The launcher preserves the caller's working directory and arguments.
Use absolute input/session paths to make host relocation unambiguous. It forwards exit
status and performs no installation, model download or provider call.

The examples below use this function so the CLI does not need to be on `PATH`.
The equivalent direct invocation starts with `interaction-design`.

## Full design pipeline

For natural-language design intent, first read [task authoring](task-authoring.md).
For incomplete information, use `task init/review/build` through the launcher to
record a brief, clarify missing choices and compile a checked task. `needs_input`
returns exit code 2 with a JSON report; do not proceed to model execution in that state.
The host pairs the strict task JSON with a runtime JSON pointing to installed ODesign,
ESMFold v1 and ESMFold2 assets. AF3 is not used. Start with validation and preparation:

```bash
TASK=/absolute/path/to/task.json
RUNTIME=/absolute/path/to/pipeline-runtime.json
molclaw validate "$TASK"
molclaw pipeline prepare "$TASK" --runtime "$RUNTIME" --strategy harness \
  --batch-size 2 --max-evaluations 8 --artifacts /absolute/path/to/pipelines
```

Preparation returns JSON with `status: prepared` and `pipeline_dir`; it does not print
only a path as `screen prepare` does. Read the returned directory and use it below.
`--strategy` defaults to `harness`; `fixed` and `heuristic` run deterministic selection.
The default artifacts parent is `artifacts/pipelines` relative to the CLI's working
directory. Optional `--tool-wall-budget-seconds N`, the evaluation quota and batch size
apply to complex screening only. Generation uses the task's full seed pool, and monomer
checks run before complex selection.

```bash
PIPELINE_DIR=/absolute/path/returned/by/prepare
molclaw pipeline run "$PIPELINE_DIR"
```

`run` performs ODesign generation and ESMFold v1 monomer checks, then prepares live
ESMFold2 complex screening. In harness mode it returns:

```json
{
  "pipeline_dir": "/absolute/path/to/pipeline",
  "status": "awaiting_selection",
  "screening_session": "/absolute/path/to/screening-session",
  "request": {
    "request_sha256": "CURRENT_HASH",
    "payload": {"visible_state": {}},
    "response_schema": {}
  }
}
```

This is an abbreviated shape, not a decision-ready observation. Use the actual
`request.payload.visible_state` and complete `request.response_schema`. The Submission
format in [Decision envelope](#decision-envelope) is unchanged; copy its hash from
`request.request_sha256`. Keep host decision files outside executor-managed directories.

```bash
DECISION_JSON="$PIPELINE_DIR.host-decision.json"
molclaw pipeline apply "$PIPELINE_DIR" --decision "$DECISION_JSON"
```

For a host JSON pipe, `pipeline apply PIPELINE_DIR --decision -` accepts the full
Submission on stdin. `pipeline apply` already returns the next pipeline state, including
the next pending request or completed report. This differs from `screen apply`, which
needs a subsequent `screen observe`. `pipeline observe PIPELINE_DIR` obtains the current
state when resuming: `prepared` or `ready` needs `run`, an awaiting-selection pipeline
needs a decision, and a completed pipeline can be exported. Preserve evidence and
inspect failed or interrupted work; the executor does not authorize blind retries.

For deterministic strategies, `pipeline run` completes the bounded selection itself.
Once the pipeline returns `status: completed`, export a local result bundle:

```bash
molclaw pipeline export "$PIPELINE_DIR" --output /absolute/path/to/result-bundle
```

Export returns JSON with `status: exported`, `pipeline_dir` and `export_dir`. Report the
returned export directory and its results. It packages computational diagnostics and
traceable artifacts; it does not establish experimental binding. Use the supplied
output path and preserve existing result artifacts.

## Prepare and observe an existing monomer screening session

Replace the input paths with completed jobs, then prepare an offline replay session:

```bash
MONOMER_JOB=/absolute/path/to/completed-monomer-job
COMPLETED_INTERFACE_FEEDBACK=/absolute/path/to/completed-interface-feedback
SESSION_DIR="$(molclaw screen prepare "$MONOMER_JOB" --strategy harness \
  --feedback "$COMPLETED_INTERFACE_FEEDBACK" --batch-size 2 --max-evaluations 4)"
```

Alternatively, prepare a live session using an existing local runtime:

```bash
MONOMER_JOB=/absolute/path/to/completed-monomer-job
RUNTIME=/absolute/path/to/complex-runtime.json
SESSION_DIR="$(molclaw screen prepare "$MONOMER_JOB" --strategy harness \
  --complex-config "$RUNTIME" --batch-size 2 --max-evaluations 4)"
```

`prepare` prints the absolute session path. Add `--artifacts /absolute/path/to/new-runs`
to select its parent directory; otherwise the parent is `artifacts/screening` relative
to the caller's working directory. Live mode can additionally set
`--tool-wall-budget-seconds 600`; replay rejects live wall-time budgets.

For a new session, or after assigning `SESSION_DIR` to an existing session, obtain its
current request. Keep host-created files outside executor-managed step directories:

```bash
REQUEST_JSON="$SESSION_DIR.host-request.json"
DECISION_JSON="$SESSION_DIR.host-decision.json"
molclaw screen observe "$SESSION_DIR" > "$REQUEST_JSON"
```

`observe` prints and persists either a decision request or a terminal report whose
`status` is `completed`. Check for completion before asking the model for a decision.
A request contains `request_sha256`, `payload` and `response_schema`. The payload
contains `session_id`, `step`, `configuration`, `visible_state` and `instructions`.
`response_schema` describes the **whole Submission envelope**, including `actor` and
the current request hash, not just its nested `decision`. Read it on every request;
the executor also enforces constraints beyond the portable JSON Schema subset.

Make decisions using only `payload.visible_state`. Its `candidates` include IDs,
`pre` features and `evaluated`; only evaluated rows include `post`. The state also
provides `remaining_evaluations`, `batch_size`, sequence similarities, consumed tool
time and `stop_required`. Choose at most the minimum of remaining quota, batch size
and number of unevaluated candidates.
Do not open the replay source or session's hidden replay data, even though the executor
can access them. Never reconstruct unselected outcomes from older experiment reports.

`pre` features describe the existing monomer/design geometry observations; `post`
features become available only after evaluation. Missing or null measurements are
unknown, not zero. Choose candidates using the user's diagnostic priorities, account
for competing metrics, and cite the actual fields that support the choice. A good
monomer score alone cannot establish a good interface. Evidence permits these fields:

| Pre-evaluation fields | Observed post-evaluation fields |
| --- | --- |
| `pre.monomer_plddt` | `post.iptm` |
| `pre.monomer_design_rmsd` | `post.hotspot_coverage` |
| `pre.generated_hotspot_coverage` | `post.clash_residue_pairs` |
| `pre.generated_clash_residue_pairs` | `post.binder_rmsd_after_target_alignment` |

Sequence similarities can inform selection but are not accepted evidence field names.
Post values are rounded by the executor before disclosure: copy the disclosed values
without further rounding or replacing them with raw result precision.

## Decision envelope

Write a UTF-8 JSON object to `DECISION_JSON`. Its shape is:

```json
{
  "request_sha256": "COPY_THE_CURRENT_REQUEST_HASH",
  "decision": {
    "action": "evaluate",
    "candidate_ids": ["COPY_AN_UNEVALUATED_VISIBLE_CANDIDATE_ID"],
    "reason": "Explain why the visible diagnostic evidence supports this selection.",
    "evidence": [
      {
        "candidate_id": "COPY_AN_UNEVALUATED_VISIBLE_CANDIDATE_ID",
        "field": "pre.monomer_plddt",
        "value": 0.0
      }
    ]
  },
  "actor": {
    "harness": "codex",
    "model": null,
    "agent_id": null
  }
}
```

This is a structural template, not runnable evidence. Replace every placeholder and
the illustrative `0.0` with the cited candidate's visible `pre.monomer_plddt` value.
Evaluation requires at least one valid evidence item. Do not round evidence numbers,
convert their units, cite hidden fields, or claim an unavailable measurement. The
request's schema defines valid field names and value types. `actor.harness` identifies
the actual host (for example `codex` or `claude-code`); use the known configured model
and agent ID, otherwise `null`. In an automated bridge, host code should attach these
values. These labels record provenance; they are not authentication or proof of which
model generated the decision.

For `stop`, set `candidate_ids` to `[]`, give the actual stopping reason, and include
visible evidence when that reason depends on measurements. An empty evidence list is
accepted for a stop. Do not submit an `evaluate` action with zero candidates.

```bash
molclaw screen apply "$SESSION_DIR" --decision "$DECISION_JSON"
molclaw screen observe "$SESSION_DIR" > "$REQUEST_JSON"
```

`apply` returns `status: applied` plus selected IDs and disclosed observations. An
identical, previously completed submission returns `status: already_applied` without
rerunning evaluation. A different submission using that old hash is rejected. Always
call `observe` after either successful response; it emits the next request or completes
the session and writes `$SESSION_DIR/report.json`. This includes after applying `stop`.
Once `observe` returns `status: completed`, stop requesting model decisions.

Repeat only while the executor permits decisions. The executor bounds each batch and
total evaluations and rejects unknown, repeated, already evaluated or over-budget
candidate selections, stale request hashes and invalid evidence. After a stale-request
rejection, obtain a fresh observation and reconsider the decision. Inspect other
validation failures before correcting the envelope. A failed evaluation is not a
reason to blindly resubmit the same action; preserve its records and inspect recovery
instructions. Do not alter records to force a retry.

To run a baseline, prepare a separate session using `--strategy fixed` or
`--strategy heuristic` with the same inputs and budget, then call
`molclaw screen run "$SESSION_DIR"`. For a harness session, `screen run` returns the
current request for the host. The harness strategy uses the `observe`/`apply` loop.
On the next observation after a limit is reached, the executor records its own stop
and completes the session without an extra model decision.

## Reporting boundaries

For a host that sends JSON through a pipe, `screen apply SESSION_DIR --decision -`
reads the same full submission from standard input. This preserves the request hash
and validation and does not create a temporary decision file. The native DeepSeek
adapter uses this form through its registered bash tool.

Use executor-produced reports and validation records. State the mode, selected IDs,
observed diagnostic tradeoffs, consumed evaluation count and stopping reason. Replay
measures saved-result selection behavior and local replay cost; it does not measure
new inference savings. Live tool time does not include host model/API cost unless the
host measures that separately. Preserve report/request hashes for traceability.

Do not describe any candidate as experimentally binding, biologically accepted, or
validated by a diagnostic frontier. Comparisons on already inspected replay data are
engineering checks; claims of strategy benefit require independent controlled evidence.

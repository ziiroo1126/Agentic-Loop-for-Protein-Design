# Sequential development reevaluation

Use this mode for a supplied adaptive input/session, or a clearly labelled synthetic
demonstration. It acquires the next cached prediction of an existing candidate; it
does not generate proteins or launch inference. Only `iptm`, `ipsae`, `sc_dockq`, seed
indices, candidate IDs and query counts in public observations are decision inputs.
The budget includes the initial one-seed-per-candidate acquisition. Null scores remain
unknown and still cost one query. No score is a calibrated binding probability.

## Entry and resume

`CLI` below means the existing `interaction-design` executable or:

```bash
python3 /path/to/skill/scripts/molclaw.py --project-root /path/to/MolClaw -- adaptive --help
```

Prepare only a fresh session. Use the user's quota and budget; new host loops normally
enable `--require-review`. That switch requires a plan on every evaluation; every
planned evaluation requires reflection before any further action in that pool.

```bash
CLI adaptive prepare --imported INPUT.json --output SESSION \
  --policy harness --quota 10 --budget-per-candidate 3 --require-review
CLI adaptive observe SESSION --pool POOL
CLI adaptive history SESSION --pool POOL
```

The prepare response lists pool IDs. `observe` returns the current sealed observation.
`history` returns public initial observations, accepted events, revealed deltas,
reviews and `pending_reflection`; read it when resuming to recover the pending plan.
Both may be used during decisions. Do not open `private-input.json`, source datasets,
earlier experimental reports or a completed exported viewer while choosing actions.
This is an application protocol, not filesystem isolation.

## One round

Choose the allowed query that can address a concrete uncertainty in the current
selection. Write a short, checkable decision summary, not a private reasoning trace.
For `evaluate`, submit this flat object; copy hashes, IDs, fields and numbers from the
actual observation. Values below are illustrative and must not be reused blindly.

```json
{
  "request_sha256": "CURRENT_OBSERVATION_HASH",
  "action": "evaluate",
  "candidate_id": "c_example",
  "reason": "This candidate is close to the current selection boundary.",
  "plan": {
    "question": "Does another seed contradict the initial high ipSAE?",
    "expectation": "A lower observation would weaken the current ranking.",
    "evidence": [
      {"candidate_id": "c_example", "seed": 0, "field": "ipsae", "value": 0.6}
    ]
  }
}
```

```bash
CLI adaptive apply SESSION --pool POOL --submission action.json
CLI adaptive observe SESSION --pool POOL
```

Apply returns a receipt, not the next observation. Read observe again before deciding.
Compare the new paid prediction with the recorded expectation and submit a reflection:

```json
{
  "request_sha256": "NEW_OBSERVATION_HASH",
  "outcome": "inconclusive",
  "summary": "The new score is lower, but two observations do not establish reliability.",
  "evidence": [
    {"candidate_id": "c_example", "seed": 1, "field": "ipsae", "value": 0.5}
  ]
}
```

```bash
CLI adaptive reflect SESSION --pool POOL --submission reflection.json
```

Outcome is `supported`, `refuted` or `inconclusive`. The executor checks evidence against
the public state, including at least one field of the newly acquired prediction.
It does not certify the host's interpretation. Plan question/expectation and reflection
summary are each limited to 1200 characters; each evidence list contains 1–12 entries.
`--submission -` reads JSON from stdin. Reflection does not spend prediction budget
or change the observation hash. It attaches once to the original event; no replacement
or retrospectively inserted review is allowed after a later action. Identical retries
return `already_applied`/`already_reflected` without charging again.

Repeat observe → plan → apply → observe → reflect until ready to commit or the query
budget is exhausted. Missing scores and contradictory results remain in the record.
Select exactly `quota` unique visible IDs, with a reason and the latest hash:

```json
{
  "request_sha256": "CURRENT_OBSERVATION_HASH",
  "action": "select",
  "candidate_ids": ["c_first", "c_second"],
  "reason": "Final selection from the paid observations under the declared stopping rule."
}
```

The selection list must match the session quota; the two IDs above illustrate a
quota of two. A select action costs zero queries. Early stopping preserves unused
budget. Complete every pool before calling `adaptive report SESSION` to reveal
experimental outcomes. Rejections require inspection, a fresh observation if stale,
and correction of the request; do not edit the protocol or ledger to force progress.

## Export and deterministic comparison

```bash
CLI adaptive report SESSION
CLI adaptive export SESSION --output VIEW
```

Open `VIEW/index.html` directly in a browser. It contains pool/round controls, paid
metrics, action summaries, reflections, budget and links to original JSON records.
Export is also allowed before completion: it never calls report or reveals labels.
Only an already-revealed report is included. The completed bundle is retrospective;
its embedded data can be inspected, so it is not a blinded decision UI. Raw unacquired
predictions are omitted even from completed exports. No web server is required.

For separate baselines, prepare a fresh session with `--policy uniform`, `fixed_top`,
`boundary` or `single`, then `adaptive run SESSION` and report/export. `run` rejects
`harness`; do not describe a scripted baseline as an LLM run. The public apply route
also enforces the declared deterministic policy. For controlled allocation studies,
use the same fixed final ranker and comparable budgets. The built-in ipSAE median and
boundary heuristic are development rules, not a trained reliability model.

Record the actual host/model identity, stopping rule, dataset scope and model-call
cost availability in the case report. Do not infer a specific model from a host name.
Previously exposed public data is development data; this interface alone cannot
certify a blinded independent test or a stable agent advantage.

New sessions use `molclaw-adaptive-development-v2`, with code snapshots including the
review validator. Existing v1 sessions and reports are preserved, not migrated or
resumed by v2. Continue a frozen old session only in its corresponding archived code
environment; prepare a fresh output directory when changing executor versions.

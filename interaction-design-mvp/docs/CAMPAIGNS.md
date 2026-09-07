# Sample, observe and stop

The first controller supports two deterministic baselines through the same action and
execution boundary. A `sample` action generates one fixed seed batch, runs ESMFold2,
and reads interface feedback. A `stop` action ends the campaign. The target, hotspots,
binder length and model settings remain fixed throughout a campaign.

This is a development controller with working local model adapters and saved-result
replay. It does not yet contain an LLM provider, active parameter search or a validated
biological acceptance policy. The current feedback strategy tests a stopping rule;
it uses the same frozen seed schedule as the fixed baseline.

## Strategies and observations

The `fixed` strategy samples until the round limit or an optional tool wall-time limit.
The `feedback` strategy additionally stops after `patience` consecutive rounds without
extending the previously observed diagnostic Pareto frontier. A candidate extends that
frontier when no previously observed candidate is at least as good on every objective:

| Objective | Direction |
| --- | --- |
| ESMFold2 ipTM | Higher |
| Fraction of requested hotspots contacted | Higher |
| Cross-chain clash residue pairs below 2 Å | Lower |
| Binder C-alpha RMSD after target alignment | Lower |

Exact objective ties do not count as improvements. Tradeoffs can extend the frontier
even when some metrics worsen. No weighted scalar score, numerical improvement margin
or biological pass cutoff is introduced. These continuous diagnostic metrics are not
calibrated for this stopping rule; small numerical differences can affect decisions.
The frontier is not a set of experimentally validated binders, and a new tradeoff is
not necessarily a better binder. `acceptance` stays `null`.

Each decision receives only the already observed candidate IDs, seeds, objective vectors
of the current frontier, recent frontier changes, round count and consumed tool time.
It cannot inspect future replay observations through this interface. The executor
independently validates actions and the exact next seed batch. Future decision adapters
can use `Decision` and the same validation boundary; no LLM connection is implied by
the injectable Python policy interface.

## Replay existing observations

Run from `interaction-design-mvp/`:

```bash
.venv/bin/alpd campaign replay <completed-interface-feedback-directory> \
  --strategy fixed --batch-size 4 --max-rounds 4
.venv/bin/alpd campaign replay <completed-interface-feedback-directory> \
  --strategy feedback --batch-size 4 --max-rounds 4 --patience 1
```

Replay reveals the saved observations in ascending, contiguous seed batches. It performs
no model inference and labels every result `saved_observation_replay`. The complete
dataset is available to the replay tool, while the decision function sees only returned
batches. This separation and the absence of future candidates in each recorded context
are covered by tests. This is an application boundary, not a sandbox for arbitrary
untrusted Python policies.

Replay measures the controller's actual CPU tool-call time, not hypothetical generation,
model startup or GPU savings. Live wall-time budgets are therefore rejected for replay.
The original 16 PD-L1 observations are development data already examined before this
controller was designed. Results on them are engineering checks, not held-out evidence
or proof of strategy superiority.

## Run local models

Use `config/campaign-feedback.example.json` for the decision settings. Select `fixed`
to run the other baseline with the same batch and seed schedule. Copy
`config/campaign-runtime.example.json` to an ignored local file and supply absolute
paths to the already available runtimes, weights and chemical components.

```bash
.venv/bin/alpd campaign prepare examples/baseline_pdl1_16.json \
  --strategy config/campaign-feedback.example.json \
  --runtime .cache/campaign-runtime.local.json \
  --initial-feedback <completed-interface-feedback-directory>
.venv/bin/alpd campaign run <printed-campaign-directory>
```

The original task's seed list is replaced by the frozen campaign schedule. Optional
initial feedback must match the task constraints, generation settings and evaluation
protocols, and its seeds must not overlap the new schedule. Initial candidates remain
visible but do not count as newly sampled rounds. Without `--initial-feedback`, the
campaign starts with no observations.

Version 1 requires two linear canonical protein chains, a fixed-length generated binder,
a target made of fixed segments, requested hotspots and one generated candidate per
seed (`samples_per_seed=1`, `inverse_fold_topk=1`). The local action runs generation,
complex folding and geometry feedback as one recorded step. Monomer checks are not
part of this first controller action. Model and geometry protocols are frozen, and
runtime paths are checked before generation. Execution uses local assets; no installer
or downloader is called. Use command-scoped offline/proxy settings according to the
server's rules, as in the recorded development run.

`tool_wall_budget_seconds` is optional; `max_rounds` always bounds the campaign. When
specified, the budget counts actual sample-action wall time, including input checks,
model startup, generation, folding and feedback. Prior initial-feedback computation
is excluded and must be accounted for separately in comparisons. Per-process timeouts
are capped by the remaining campaign allowance. Preflight work, process cleanup and
reporting can cause an overrun, which is recorded; no subsequent sample is launched
once the allowance is exhausted. This is not a GPU scheduler or exact GPU-active-time
accounting. It does not include future LLM/API costs.

## Records and recovery

Preparation freezes the task, runtime, strategies, initial observations, model/geometry
protocols, controller source and input hashes under `artifacts/campaigns/<id>/`.
Each step records its visible context and decision before starting a tool, then stores
the result, elapsed time and artifact checksums. Local generation, folding and feedback
retain their existing logs, validation and manifests within the step directory.

A lock prevents simultaneous execution of the same campaign. Re-running a completed
campaign verifies its report and steps without invoking tools or rewriting the report.
Resuming at a completed-step boundary retains those steps and continues with the next
seed batch. A persisted stop decision can reconstruct a missing final report after a
crash. Changed inputs, protocols or controller implementation are rejected.

Failed or unfinished tool steps retain their logs and measured cost and require
inspection before further execution. They are not automatically retried: a process
could have completed useful work before its receipt was written. Automatic recovery
within a partially completed generation/folding step remains future work. Invalid
external decisions are rejected before tools run and saved separately; a later valid
decision can resume from the completed history.

For scientific comparisons, predefine the objectives, stopping rule, seed schedule,
initial evidence and resource accounting, then evaluate on independent targets and
repeats. The current development traces establish that the controller runs and that
the baselines share an interface; they do not establish an Agent advantage.

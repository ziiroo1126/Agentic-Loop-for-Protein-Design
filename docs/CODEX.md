# Use ALPD in Codex

The default release path is a project-local skill and an existing ALPD CPU installation.
Codex supplies its configured model session; ALPD does not require another LLM API key.
Install and sign in to Codex using its [official guide](https://developers.openai.com/codex/quickstart/).

## Install and check

From the ALPD checkout, after [CPU setup](QUICKSTART.md):

```bash
export ALPD_PROJECT_ROOT="$PWD"
mkdir -p /tmp/alpd-work
python tools/alpd.py install --host codex --project-dir /tmp/alpd-work
python tools/alpd.py doctor --host codex --project-dir /tmp/alpd-work
cd /tmp/alpd-work
codex
```

This creates `.agents/skills/alpd`, a symlink to the entire shared skill. Codex
[discovers project skills and follows symlinks](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).
The installer preserves existing conflicting files. Reinstalling the same link is
idempotent. `doctor` checks paths and the CLI; its report distinguishes these from
actual host discovery, a model session and GPU execution.

Codex 0.153.4 reports the plugin-contained skill as **`alpd:alpd`** in its native
skill list (`scope: repo`, `enabled: true`). Use `$alpd:alpd` or select the ALPD
entry in the skill picker. This qualification comes from the plugin and skill
names; the installation folder remains `.agents/skills/alpd`.

Use the checkout's `.venv/bin/alpd`, or provide another installed executable through
`ALPD_CLI`. Keep `ALPD_PROJECT_ROOT` set when starting Codex from another project.
It identifies the checkout, while outputs stay in your working project.

## First host run without a GPU

Invoke the installed skill:

```text
$alpd:alpd Use the supplied examples/pdl1-binder/brief.json in ALPD_PROJECT_ROOT
to review the task and build a checked task in this project. Then prepare a new
adaptive session from interaction-design-mvp/examples/adaptive-synthetic.json,
quota 2, budget-per-candidate 2, policy harness, with required review.
Use only the visible observation and response schemas. Make at least one allowed
query, reflect on its result, select the final two candidates and export an HTML
report here. Also export a 3D viewer from examples/pdl1-binder/run. This is a host
integration demonstration using synthetic predictions and separate saved structures;
do not run GPU inference or download models. Do not inspect unrevealed outcomes.
```

The agent should read the skill's task and adaptive references, validate the supplied
brief, follow observe → plan/apply → observe → reflect → select, and report output
paths. The CPU policy is now the host model, so normal host usage costs apply.
The separate saved structure export does not imply synthetic candidates have real structures.

## Real GPU task

Once the [runtime](RUNTIME.md) is prepared, provide actual absolute paths:

```text
$alpd:alpd Use /absolute/path/to/task.json and /absolute/path/to/runtime.json.
Preflight both, then run the full pipeline with strategy harness, batch size 1
and at most 1 complex evaluation. Continue the visible observe/apply loop until
completion and export to /absolute/path/to/new/results. Report the evidence,
stopping reason and recorded costs. Keep unmeasured outcomes unknown.
```

The task's seed pool controls generation. The complex evaluation allowance does
not limit the earlier generation and monomer stages. Unsupported or missing
scientific requirements are reported before execution.

## Host support

| Host | Release scope |
| --- | --- |
| Codex | Default shared-skill path; release verification records discovery and the complete CPU host exchange |
| Claude Code | Shared skill and plugin metadata; no claim of a complete live Claude model run |
| DeepSeek Harness | Native tool transport and CLI integration tested; full model/profile session remains experimental |

See [release verification](RELEASE_VERIFICATION.md) for actual versions, commands and
results. ALPD does not install or reconfigure your host globally. More adapter details
are in [the plugin guide](../plugins/alpd/README.md).

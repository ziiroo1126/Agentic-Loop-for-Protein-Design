# PD-L1 binder workflow: a complete recorded case

This case contains two generated candidates, two ESMFold v1 monomer checks, a real
Codex candidate selection, one ESMFold2 complex evaluation, an executor stop, and
the portable report. It is a development run, with no experimental binding result.
The selected candidate's complex ipTM is approximately 0.184; this is not a success claim.

## Files and provenance

- `run/`: the original portable result export, copied without changes. Its manifest
  lists SHA-256 hashes for structures, metrics, task, protocol and decision records.
- `brief.json`: a new intake example expressing the original task's supplied requirements.
  It demonstrates current input validation; it was not the input authoring record of the old run.
- `runtime.example.json`: a template for your existing local model environments.
- `software.json`: software records extracted from the original exported provenance.

The reference coordinates come from the pinned ODesign-pipeline PD-L1 example.
Attribution and licenses are in [third-party notices](../../THIRD_PARTY_NOTICES.md).
The [original run description](../../docs/evidence/PIPELINE_DEVELOPMENT.md) describes
execution through DSH tools with a Codex decision model. It is not a DeepSeek model run.
Saved actor model identifiers that were not recorded remain null.

## Reproduce the presentation on CPU

From the repository root, after [installing the CPU core](../../docs/QUICKSTART.md):

```bash
alpd task review examples/pdl1-binder/brief.json
alpd task build examples/pdl1-binder/brief.json --output /tmp/alpd-case-task.json
alpd viewer export examples/pdl1-binder/run --output /tmp/alpd-case-viewer.html
python tools/build_site.py --output /tmp/alpd-site
```

Use fresh output paths. Open `/tmp/alpd-case-viewer.html` for 3D or
`/tmp/alpd-site/index.html` for the gallery. This uses original results without
running models or asking a host to make a new decision. `run/task.json` retains
the exact original task; the newly compiled intake example uses current default
identifiers and is not a byte-identical replacement for it.

## Execute the original task again

Prepare the three model environments using [the runtime guide](../../docs/RUNTIME.md).
Copy `runtime.example.json` to a local file and replace its asset paths. Then:

```bash
alpd pipeline preflight examples/pdl1-binder/run/task.json --runtime /path/to/runtime.json
alpd pipeline prepare examples/pdl1-binder/run/task.json --runtime /path/to/runtime.json \
  --strategy harness --batch-size 1 --max-evaluations 1
```

Give the returned `pipeline_dir` to the [ALPD skill](../../docs/CODEX.md), which
runs the task, reads each visible request, submits a decision and exports results.
The scientific model settings and original seed pool are included in the task and
protocol; hardware, library differences and a new host decision can change results.
Reproduction means a traceable new execution, not a guarantee of identical GPU bytes
or identical LLM selection. Rebuild the saved presentation for exact record replay.

The separate [adaptive case](../../docs/evidence/adaptive-loop-pdl1/) uses an existing
90-candidate pool and cached predictions. It is not a later stage of this two-candidate run.

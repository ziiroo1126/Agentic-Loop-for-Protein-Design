# DeepSeek Harness

This package integrates with the official
[DeepSeek Harness (`dsh`)](https://github.com/deepseek-ai/deepseek-harness).
Use the shared skill for instructions and the existing CLI, or load the native bundle
to expose named pipeline and screening tools. Both paths use the host model for decisions and the
MolClaw executor for evaluation, budget enforcement and recorded results.

For a new pipeline, the host authors strict task JSON from supplied biological intent
and provides full local generation/monomer/complex runtime JSON. The pipeline runs
ODesign, ESMFold v1 monomer checks and ESMFold2 complex evaluation, then exports its
results. AF3 is unused. Existing screening accepts a completed monomer job and either
saved feedback or a live complex runtime. The adapter does not install assets or add
an LLM API client. Read [task authoring](task-authoring.md) and the [CLI protocol](cli-and-protocol.md) for input
requirements, visible-state decisions, submission envelopes and completion handling.

## Compatibility contract

The native adapter targets the published `@deepseek-ai/dsh-tools@0.1.2-rc.1` and
`@deepseek-ai/dsh-tool-bash@0.1.2-rc.1` contracts. Native module loading and the real
tool chain have been verified against these packages, as recorded below. The complete
DSH application was not started.

The official repository was also inspected at commit
[`d347e703908d0406b7a7ef80e3a0e594d86b2215`](https://github.com/deepseek-ai/deepseek-harness/commit/d347e703908d0406b7a7ef80e3a0e594d86b2215)
dated 2026-09-04. Its source package version, `0.1.3-alpha.1`, is a research reference;
it was not available at the checked npm endpoint on 2026-09-06.
Source inspection does not establish compatibility with that release or all earlier
releases. Check the actual host version and the verification notes below.

The library-level checks ran on Node `v22.17.1`. Launching the pinned upstream source
requires its own [Node engine range](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/package.json),
`^22.19.0 || >=24.0.0`; library-level success on the older runtime does not change
that application requirement.

The relevant upstream contracts are:

- [Filesystem skill discovery](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/packages/skill/skill-filesystem/README.md):
  direct `<name>/SKILL.md` bundles with `name` and `description` frontmatter.
- [Bundle packaging](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/docs/user/develop/basic/publish.md):
  an npm package declares `dsh.bundle.patch`, and a profile composes its patch layer.
- [Native tools](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/docs/user/develop/basic/tool.md):
  a Cordis plugin registers `defineTool` definitions on `ctx.tools` with separate
  canonical output and model-facing rendering.
- [CLI inspection](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/apps/cli/reference/README.md):
  `--dump-config` composes configuration without booting the application.

## Use the shared skill

DSH's default filesystem provider searches these roots in priority order:

| Priority | Skill root |
| --- | --- |
| 100 | `<projectRoot>/.dsh/skills` |
| 200 | `<projectRoot>/.agents/skills` |
| 300 | Configured `customSkillDirs` |
| 400 | `$DSH_HOME/skills`, normally `~/.dsh/skills` |
| 500 | `$DSH_AGENTS_HOME/skills`, normally `~/.agents/skills` |

`projectRoot` is the nearest ancestor containing `.git`, or the session working
directory if none exists. Discovery is one level deep; the checked-in
`plugins/molclaw/skills/molclaw/SKILL.md` is not under a default root. DSH does not
discover this file merely because the repository contains Codex or Claude manifests.
See the pinned [root resolution source](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/packages/skill/skill-filesystem/src/index.ts#L226).

To use the checked-in skill without copying it, create a patch file at a location
you choose, for example `/absolute/path/to/molclaw-skills.patch.yml`:

```yaml
- id: skill-filesystem
  config:
    customSkillDirs:
      - /absolute/path/to/MolClaw/plugins/molclaw/skills
```

The configured path is the parent containing the `molclaw` skill directory. The
`skill-filesystem` row is present in the official base-backed `web` profile. A patch
replaces the row's entire `config`, so retain any existing custom provider settings
you need when composing this example with your configuration.

With DSH already installed, inspect the overlay before starting a session:

```bash
dsh --profile web --patch /absolute/path/to/molclaw-skills.patch.yml --dump-config
```

To use it in the Web host:

```bash
MOLCLAW_PROJECT_ROOT=/absolute/path/to/MolClaw \
  dsh --profile web --patch /absolute/path/to/molclaw-skills.patch.yml --no-open
```

Invoke `/molclaw`, or ask the host to load the `molclaw` skill, and supply the existing
session or preparation inputs. For a standalone installation, copying the complete
`skills/molclaw/` directory into a chosen project's `.agents/skills/molclaw/` also
matches the documented discovery layout. Keep its launcher and references together;
choose one copy to avoid duplicate skill names.

## Install the native bundle into a profile

The bundle lives at the same package root as the other host manifests:

```text
plugins/molclaw/
  package.json
  cordis.patch.yml
  deepseek/index.mjs
  skills/molclaw/SKILL.md
```

`package.json` declares `dsh.bundle.patch`; the referenced patch inserts the native
plugin row for the `dsh-molclaw` package. DSH uses this npm bundle contract, rather than
the sibling Codex or Claude manifest. A profile installation changes that profile's
dependencies and bundle list.
When you choose to install into an existing DSH setup, use:

```bash
dsh plugin --profile web add /absolute/path/to/MolClaw/plugins/molclaw
dsh --profile web --dump-config
```

DSH forwards the first command to pnpm, which must be available on `PATH`. The dump
should include the MolClaw layer and its plugin row; it confirms configuration
composition, not execution of a tool or a model decision. Restart that profile after
adding or updating a bundle. Launch it with `MOLCLAW_PROJECT_ROOT` pointing at the
actual checkout, and load the shared skill using the preceding section. Native bundle
installation alone does not add a filesystem skill root.

These commands are setup instructions, not actions performed when the repository is
built. No installer is run automatically and no user home is modified by constructing
this distribution. `--dump-config` can initialize a missing profile's files even
though it starts no server; set `DSH_HOME` to a temporary or otherwise chosen location
when inspecting an isolated installation.

The native plugin accepts these configuration fields:

| Field | Meaning |
| --- | --- |
| `projectRoot` | Absolute MolClaw checkout path; defaults to `MOLCLAW_PROJECT_ROOT`. |
| `cli` | Existing CLI executable path; a relative path resolves under `<projectRoot>/interaction-design-mvp`. Defaults to that directory's `.venv/bin/interaction-design`. |
| `timeoutMs` | Optional positive timeout passed to the host's `bash` tool. The host retains its default and cap when omitted. |

For example, an additional overlay can configure the installed `molclaw` row:

```yaml
- id: molclaw
  config:
    projectRoot: /absolute/path/to/MolClaw
    cli: /absolute/path/to/existing/interaction-design
```

Pass its path with `--patch` when inspecting or launching the profile. Omit `cli`
when the default executable is already present.

Native calls run with `<projectRoot>/interaction-design-mvp` as their working
directory. Their explicit `cli` configuration is separate from the standalone skill
launcher's executable search described in the [CLI protocol](cli-and-protocol.md).
Use absolute task, monomer-job, feedback, runtime, pipeline and session paths.

## Native pipeline tools

| Tool | Arguments and operation |
| --- | --- |
| `molclaw_pipeline_prepare` | `task`, `runtime` JSON paths; optional `strategy`, `batch_size`, `max_evaluations`, `tool_wall_budget_seconds`, `artifacts`. Validate and prepare; return `status: prepared` and `pipeline_dir`. |
| `molclaw_pipeline_run` | `pipeline_dir`. Run generation and monomer checks, then prepare complex screening. Harness returns `awaiting_selection`; fixed/heuristic complete automatically. |
| `molclaw_pipeline_observe` | `pipeline_dir`. Read prepared state, pending request or completed report. |
| `molclaw_pipeline_apply` | `pipeline_dir`, full `submission`. Evaluate/stop and return the next pipeline state. |
| `molclaw_pipeline_export` | `pipeline_dir`, `output`. Export completed results and return `status: exported`, `pipeline_dir` and `export_dir`. |

`strategy` defaults to `harness`, `batch_size` to 2 and `max_evaluations` to 8.
Selection budgets cover complex screening only. The task's seeds define the candidate
pool; generation and monomer checks precede host selection. Use the actual installed
local runtimes. Native preparation accepts file paths, so task authoring and `validate`
use the host's normal file and CLI capabilities described in the shared skill.

When a pipeline is `awaiting_selection`, its observation envelope is nested under
`request`. Choose only from `request.payload.visible_state`, preserve
`request.request_sha256` and follow the full `request.response_schema`. Apply takes the
same Submission used by existing screening: `request_sha256`, `decision` and `actor`.
Use the actual host/model/agent metadata, with `null` for unknown model or agent IDs.
Pipeline apply already returns the next state. Continue until `completed`, then export.

## Native screening tools

| Tool | Operation |
| --- | --- |
| `molclaw_screen_prepare` | Prepare screening from a completed monomer job and explicit replay or live inputs. |
| `molclaw_screen_observe` | Return the current visible request or the completed report. |
| `molclaw_screen_apply` | Submit a decision against the current request and let the executor validate it. |
| `molclaw_help` | Return workflow guidance without running an evaluation. |

`prepare` takes `monomer_job` and exactly one of `feedback` or `complex_config`, fixes
the strategy to `harness`, and returns `{ "session_dir": "..." }` with an absolute
session path. Its optional
`batch_size` defaults to 2 and `max_evaluations` to 8. `tool_wall_budget_seconds`
applies only to live mode. `observe` takes `session_dir` and returns the CLI's JSON
request or completion report. `apply` takes `session_dir` plus a `submission` object
containing the current `request_sha256`, `decision` and accurate `actor` provenance;
it returns the CLI's JSON result. Identify the host as `deepseek-harness`; use the
known configured model and agent ID, or `null` when unavailable.

Use each loaded tool's parameter schema for the exact arguments. Start by loading the
skill, then prepare or resume a session. Make decisions only from the observation's
`payload.visible_state`, retain its current hash, and follow its full
`response_schema`. Observe again after each successful apply, including a stop;
finish only when observation returns `status: completed`.

The native plugin invokes the host's `bash` tool through the DSH tool registry to
preserve its execution-policy path. It requires a composition with that tool
available. A missing or denied host tool is an integration failure to report; do not
fall back to an unrestricted process launcher. This adapter does not itself isolate
hidden replay data from a model that has other filesystem tools.

The Python CLI retains `screen run` for fixed/heuristic baseline sessions and for
returning the current harness request. The native bundle exposes the three explicit
screening operations above and its help tool; autonomous host decisions use the
observe/apply loop.

## Verification boundary

The following checks passed on 2026-09-06. Transport and SDK checks include the extended
pipeline tools; the archived native execution evidence covers existing screening only:

| Layer | Verified evidence |
| --- | --- |
| Transport boundary | 25 checks in `deepseek/bridge.test.mjs`, using a controlled host boundary. |
| Official tool SDK | 5 checks in `deepseek/plugin.test.mjs`, using the published `defineTool` and configuration schema implementation. |
| Archived native screening execution | 10 checks using official Cordis, ToolRuntime, SystemPrompt, `tool-bash`, `bash-local` and `subprocess-local`, with the real MolClaw Python CLI. |

The archived native integration used `@deepseek-ai/cordis@4.0.2`,
`@deepseek-ai/schemastery@3.18.2` and DSH modules at `0.1.2-rc.1` on Node `v22.17.1`.
It mounted the MolClaw module through `ctx.plugin` and executed a CPU replay of
previously saved monomer and complex results. A deterministic script selected seeds
43 and 44 from 16 visible candidates and reached the two-evaluation quota. The
recorded actor is `deepseek-harness-scripted-validation`, with no model identifier:
these were scripted transport checks, not agent decisions or evidence of selection
quality. There were zero LLM calls and no new GPU inference.

The ten integration checks covered native mounting and help output, nested Bash
policy denial, pre-cancelled calls, rejection of invalid canonical output, preparation
and observation without revealing future complex metrics, stale-submission rejection,
stdin submission and idempotent reapplication, quota completion, nested dispatch
identity, and tool removal when the plugin unloads.

In the MolClaw checkout, the reproducible integration script is
`interaction-design-mvp/scripts/check_deepseek_plugin.mjs`. The checked run's versions,
checks and dispatch records are in
`interaction-design-mvp/artifacts/deepseek-adapter-development/validation.json`, with
its request, submission and completed report beside that file. The script accepts
explicit runtime, completed-monomer, feedback and output paths; its saved-result
inputs are development data, not a held-out benchmark.

The dependency-free transport tests can run from the MolClaw repository root:

```bash
node --test plugins/molclaw/deepseek/bridge.test.mjs
```

With the pinned peer packages already available, `npm test` from `plugins/molclaw`
runs the transport and SDK tests together. A package dry run also confirmed the
runtime modules, bundle metadata, README and complete skill are included without
build output, dependencies or test files.

The integration used the official local Bash provider. It tested registry policy
denial, but **did not test filesystem sandbox confinement**. It also did not start a
complete DSH profile, Web UI or real DeepSeek model session, and did not exercise
automatic filesystem skill discovery in that application. Profile installation and
`--dump-config` commands above follow the cited upstream contract; they are not
reported as executed in this validation. This evidence does not establish a complete
natural-language design workflow or biological binding.

No DSH installation into the user's profile or home was performed. The development
environment's `.agents` directory is read-only, so the shared skill is supplied for
distribution instead of being installed there.

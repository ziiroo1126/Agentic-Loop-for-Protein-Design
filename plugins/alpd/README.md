# ALPD host adapters

The shared skill can start from incomplete user goals and files using
`task init/review/build`, then pass a checked task to the pipeline. See the
[task input guide](../../interaction-design-mvp/docs/TASK_INPUT.md) for draft fields,
clarification reports and the complete backend's requirements.

This package provides host integration for
[Agentic Loop for Protein Design (ALPD)](../../README.md).

ALPD is computational-only. Host workflows produce computational artifacts and
offline evaluations; wet-lab work is outside the project scope and is not a completion gate.

Use a host model to turn supplied protein binder requirements into a validated task,
run ODesign generation and ESMFold v1 monomer checks, select ESMFold2 complex evaluations
within a fixed budget, and export the results. ALPD runs the models and validates
each action; the host supplies the task and evidence-based selection decisions.

The shared skill also supports `adaptive` cached reevaluation, with an optional
required plan/reflection loop and offline HTML export. See the
[adaptive workflow](skills/alpd/references/adaptive-evaluation.md).
For project-local setup, use `python3 tools/local.py install --host codex --project-dir DIR`,
then the same command with `doctor` instead of `install`. `claude` and `deepseek` are
also supported host choices. The [setup reference](skills/alpd/references/host-adapters.md)
describes existing-CLI reuse and optional resource preflight.

| Host | Entry point | Compatibility boundary |
| --- | --- | --- |
| Codex | `.codex-plugin/plugin.json` or the standalone [shared skill](skills/alpd/SKILL.md) | Packaged manifest and skill; marketplace/app installation is separate |
| Claude Code | `.claude-plugin/plugin.json` with `claude --plugin-dir` | Native plugin metadata; a live host session is needed for model decisions |
| DeepSeek Harness (`dsh`) | Shared skill, or npm bundle declared by `package.json` and `cordis.patch.yml` | Native Cordis loading and real CLI tool chain tested at `0.1.2-rc.1`; full profile and model session untested |

Start with the [skill](skills/alpd/SKILL.md),
[task authoring reference](skills/alpd/references/task-authoring.md),
[host loading instructions](skills/alpd/references/host-adapters.md), and
[CLI commands and decision envelope](skills/alpd/references/cli-and-protocol.md).
The complete shared skill directory can also be copied as a standalone skill.
For DSH, follow the [DeepSeek Harness guide](skills/alpd/references/deepseek-harness.md):
the repository's skill directory needs a `customSkillDirs` overlay or a copy into a
discovered root. Installing the native bundle does not itself install the skill.

The package requires a separately prepared ALPD checkout and an installed
`alpd` CLI. Pass the checkout path with the launcher's `--project-root`
option; it can run from a plugin cache and never infers the checkout from its own
location. It uses an
existing executable and does not install dependencies, download models, configure
providers, change proxies or register a marketplace.

The DSH native entry point, `deepseek/index.mjs`, provides tools for pipeline
preparation, execution, observation, decisions and export, as well as screening
completed monomer jobs and workflow help. Tool names and invocation details are
documented in the [DeepSeek Harness guide](skills/alpd/references/deepseek-harness.md).
All execution goes through the host's
`bash` tool. There is no additional LLM API client or ALPD API key; the host uses its
own configured model session. AF3 is not used by the pipeline.

The host authors strict task JSON from supplied target structure, chain/residue
mapping, hotspots, binder constraints and seed pool, then validates it. Missing
biological requirements must come from the host/user. Full runtime JSON points to
existing local generation, monomer and complex assets. Prepare → run → select → export
is documented in the [CLI protocol](skills/alpd/references/cli-and-protocol.md#full-design-pipeline).
Complex selection limits do not cap generation or monomer work. `pipeline run` finishes
deterministic baselines directly; harness mode returns a visible selection request.

Replay reveals saved outcomes only after selection. Live mode evaluates selected
candidates with the configured runtime. The decision model must use only the current
request's visible state, and the executor checks current request hashes, candidate
selections, evidence and evaluation limits. This is an application boundary; the
package itself does not isolate a general coding agent's filesystem access.

Computational diagnostics do not establish experimental binding or an advantage over
baseline selection. Archived model experiments are not modified by this package and
must not be used to look ahead during a replay decision.

Packaging checks on 2026-09-06 passed the bundled Codex `validate_plugin.py` and
`quick_validate.py`, plus Claude Code 2.1.247 strict validation of both manifest and
skill components. The launcher passed Ruff and local checks for relocation, exact
argument/working-directory/exit-status preservation, environment selection and missing
inputs. All `screen` help commands also passed through the launcher, and a synthetic
CLI prepare/observe/apply/completion exchange passed in the executor integration tests.
These checks do not establish plugin loading in a Codex app session or model calls in
Claude Code or DeepSeek Harness.

The extended native DSH adapter passed 25 transport checks and 5 checks using the
official tool SDK, including all five pipeline operations. Those pipeline checks use
controlled CLI responses; they do not establish successful model inference. Earlier
screening validation passed 10 integration checks using Cordis 4.0.2 and the published `0.1.2-rc.1` DSH
tool registry, local Bash and subprocess modules. The integration loaded the plugin
through `ctx.plugin` and drove the real Python CLI through prepare/observe/apply to
completion. A script selected two of 16 existing candidates for saved-result replay;
it made zero model calls and ran no new GPU inference. Policy denial, cancellation,
output validation, request identity, idempotence, quota completion and tool cleanup
were checked. See the [DSH verification record](skills/alpd/references/deepseek-harness.md#verification-boundary)
for versions and evidence paths. No complete DSH profile, Web UI or DeepSeek model
session was started, and filesystem sandbox confinement was not tested. No user
profile or home installation was performed.

The subsequent task-to-export check used all five native pipeline tools with the real
published DSH services and Python CLI: two fresh ODesign designs, two ESMFold v1
monomer checks, one ESMFold2 complex evaluation, feedback and portable export.
A fresh Codex collaboration agent chose the candidate using only visible observations;
DeepSeek supplied the tool transport, with no DeepSeek model call. Resume and repeated
apply added no model processes. See the repository's
`docs/evidence/PIPELINE_DEVELOPMENT.md` for the distinct live execution record.

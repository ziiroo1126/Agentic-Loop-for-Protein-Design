# Host adapters

All hosts use the same [CLI protocol](cli-and-protocol.md). The local executor does not
call an LLM API; the host owns model selection, authentication, tool permissions and
any model/API cost. Provide the ALPD project root explicitly after loading a cached
plugin or copied skill. Keep the full skill directory together, including references
and its launcher.

## Codex

For project-local skill installation and read-only environment checking, the source
checkout supplies `plugins/alpd/tools/local.py`. From the checkout root:

```bash
python3 plugins/alpd/tools/local.py install --host codex --project-dir /path/to/work
python3 plugins/alpd/tools/local.py doctor --host codex --project-dir /path/to/work
```

Use `--host claude` or `--host deepseek` for `.claude/skills` or `.dsh/skills` instead.
The installer links the complete shared skill, is idempotent, and refuses to replace
an existing different file, directory or symlink. It prints `ALPD_PROJECT_ROOT` for
the caller to supply; it does not edit shell profiles. `--project-root` overrides the
source checkout, and doctor accepts `--cli` for an existing executable. The destination
project must already exist. This installs a skill, not the DSH native bundle or a
marketplace plugin. Avoid simultaneously loading another copy of the same skill.

Doctor tests the actual launcher with `adaptive --help`, checks the expected skill
link, and reports host discovery and model sessions as unverified. Optional
`--task TASK.json --runtime RUNTIME.json` invokes `pipeline preflight` using the existing
resource validator. It checks local files and pinned assets without creating a job,
downloading or launching models; GPU availability and inference remain untested.
Missing live-model resources do not prevent cached replay. The default timeout is
120 seconds per CLI probe and can be changed with `--timeout`.

Codex supports symlinked project skills in `.agents/skills`, per
[OpenAI's skill documentation](https://learn.chatgpt.com/docs/build-skills).
Claude supports project skill symlinks in `.claude/skills`, per
[Claude's skill documentation](https://code.claude.com/docs/en/skills).

The distribution manifest is `.codex-plugin/plugin.json`, and its skills are under
`skills/`, following [OpenAI's plugin packaging structure](https://developers.openai.com/plugins/build/plugins).
This source package is not registered in a marketplace or installed by creating it.
Marketplace distribution can be configured separately when requested.

For one session without installation, ask Codex to read the absolute path to
`skills/alpd/SKILL.md` and use it for the supplied session. For automatic local skill
discovery, copy `skills/alpd/` into the selected project's `.agents/skills/alpd/`,
then invoke `$alpd`. These are documented
[Codex skill discovery locations](https://learn.chatgpt.com/docs/build-skills).
Do not load both a plugin copy and a standalone copy of this skill in the same session.

Example prompt:

> Use $alpd with ALPD_PROJECT_ROOT=/absolute/path/to/ALPD. Resume the screening
> session /absolute/path/to/session. Read only its observe output when deciding; continue
> until the executor stops and summarize the diagnostic results.

For a new design, supply the target structure, chain/residue mapping, hotspots, binder
constraints and installed runtime, then ask the host to author and validate a task and
complete the pipeline through export. The [task reference](task-authoring.md) describes
the inputs; the host obtains missing biological choices before making a runnable task.

The manifest and skill can be checked without loading a model. A successful metadata
check does not prove plugin discovery or model behavior in a Codex app session.

## Claude Code

The same package has a separate `.claude-plugin/plugin.json`. Skills live at the plugin
root. With Claude Code installed and configured, load the package for a local session:

```bash
ALPD_PROJECT_ROOT=/absolute/path/to/ALPD \
  claude --plugin-dir /absolute/path/to/ALPD/plugins/alpd
```

Invoke `/alpd:alpd` and supply the session or preparation inputs. Local
`--plugin-dir` loading and the `plugin:skill` namespace are documented in
[Claude Code's plugin guide](https://code.claude.com/docs/en/plugins). Validate the
package metadata with:

```bash
claude plugin validate /absolute/path/to/ALPD/plugins/alpd --strict
```

The separate manifest follows the
[Claude Code plugin reference](https://code.claude.com/docs/en/plugins-reference).
No global settings, hooks, MCP servers, marketplace registration or credentials are
included. Actual model-driven operation still needs a Claude Code session with access
to the local executor and the user-authorized evaluation runtime.

## DeepSeek Harness (`dsh`)

This adapter targets the official
[deepseek-ai/deepseek-harness repository](https://github.com/deepseek-ai/deepseek-harness).
The same `SKILL.md` can be loaded through DSH's filesystem skill provider. Its default
project roots include `.dsh/skills` and `.agents/skills`; the repository's
`plugins/alpd/skills` directory is **not discovered automatically**. Configure that
directory with `customSkillDirs`, or copy the complete skill to a chosen discovery
root. Native bundle installation and skill discovery are separate steps.

The package also supplies an npm bundle: `package.json` declares `dsh.bundle.patch`,
and `cordis.patch.yml` loads `deepseek/index.mjs`. It registers
`alpd_pipeline_prepare`, `alpd_pipeline_run`, `alpd_pipeline_observe`,
`alpd_pipeline_apply`, `alpd_pipeline_export`, and the existing
`alpd_screen_prepare`, `alpd_screen_observe`, and `alpd_screen_apply`, using
the host's `bash` tool to reach the existing Python CLI; `alpd_help` returns workflow
guidance. It does not configure an LLM provider or make its own model calls. DSH owns
its model session, authentication, execution policy and model/API cost.

See [DeepSeek Harness setup and compatibility](deepseek-harness.md) for a skill-only
overlay, profile installation, configuration inspection without starting a service,
and the pinned upstream contract. New pipelines begin with strict task JSON authored
by the host and a full local generation/monomer/complex runtime. Existing screening
sessions begin with a completed monomer job and replay or live complex inputs. See
[task authoring](task-authoring.md) and [pipeline commands](cli-and-protocol.md#full-design-pipeline).

Native screening through official Cordis and the published `0.1.2-rc.1` DSH tool chain
has been tested with the real ALPD CLI and saved-result replay. That validation used
scripted selections and zero model calls. It did not start a complete DSH profile,
Web UI or DeepSeek model session, or test filesystem sandbox confinement. The extended
pipeline tool transport and official SDK registration have separate controlled tests;
they do not establish full model inference through DSH. See the
[verification record](deepseek-harness.md#verification-boundary).

The visibility rule remains an application contract. A general coding agent with
access to replay files could inspect undisclosed outcomes despite using the native
tools. Controlled comparisons need observation-only decision access and executor-only
access to hidden feedback, as described in the [CLI protocol](cli-and-protocol.md).

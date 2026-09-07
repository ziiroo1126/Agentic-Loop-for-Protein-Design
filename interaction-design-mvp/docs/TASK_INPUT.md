# From user intent to a pipeline task

Users can start with a description and whatever target information they already have.
The host Agent records that information in a **design brief**, clarifies missing
scientific choices, then compiles a checked **execution task**. Natural language is
handled by the host; the CLI accepts JSON and does not interpret arbitrary prose.

```text
User goals and available inputs
    → brief.json (incomplete information is allowed)
    → task review (missing choices and backend requirements)
    → task build (task.json with checked target mapping)
    → pipeline preflight (local runtime and model assets)
    → pipeline prepare / run / observe / apply / export
```

## Start with a brief

```bash
alpd task init --name my_design --output artifacts/my-design/brief.json
alpd task review artifacts/my-design/brief.json
```

`init` creates a template with no invented structure, chain, hotspot or binder length.
Edit it directly, or let the host populate it from the user's supplied information.
Both creation and compilation refuse to replace existing files.

| Brief field | What it records | Before compilation |
| --- | --- | --- |
| `name` | Task identifier; letters, digits, `_`, `-`, `.` | Required; starts with a letter or digit |
| `goal` | Natural-language purpose | Descriptive context; not an executable constraint |
| `target.identifier` | Target name or database identifier | Context only; no automatic retrieval or construct selection |
| `target.sequence_file` | Path to an existing FASTA file | Context only; no sequence parsing, structure prediction or identity verification |
| `target.reference_structure` | Local PDB/mmCIF | Required and checked against the chosen residue mapping |
| `target.segments` | Selected chain/residue intervals | Required; use reference author numbering |
| `target.hotspots` | Requested target residues; `null` means unspecified | Required by the current complete ODesign pipeline |
| `binder.length` | `{"min": N, "max": M}` preference | A range is accepted in the brief |
| `binder.selected_length` | Fixed length chosen for this task | Required for a nontrivial range; must fall inside it |
| `binder.cyclic` | Whether a cyclic binder is requested | Only `false` is supported by the complete pipeline |
| `unresolved_requirements` | Scientific requirements not yet represented in task fields | Must be resolved before compilation |
| `model`, `seeds` | Execution settings | Defaults are `odesign_base_prot_flex` and `[42]`; disclosed in the review |

An exact preference (`min == max`) needs no separate `selected_length`. A directly
specified `selected_length` also works without a range. Lengths are residue counts.
The compiler never samples a length, chooses a hotspot, or selects a target construct.
Relative structure and sequence paths resolve against the **brief location**; the
compiled task uses an absolute structure path so a different output directory works.

For example, this is a valid **incomplete brief**, not a runnable task:

```json
{
  "kind": "protein_binder_brief",
  "name": "my_design",
  "goal": "Design a binder against the target I will provide.",
  "target": {"identifier": "user-specified target", "hotspots": null},
  "binder": {"length": null},
  "unresolved_requirements": ["Clarify the intended target construct."],
  "seeds": [42]
}
```

Use `target.segments` entries with `chain`, `start`, `end`; interval endpoints are
inclusive. `target.hotspots` entries use `chain`, `residue`. These share the existing
[execution schema](../src/interaction_design/specs.py). Unsupported JSON keys are
errors in both formats. A brief cannot be passed directly to `pipeline prepare`.

## Review, then compile

`review` returns `status: needs_input`, `task: null` and structured `issues` while
choices are missing or unsupported. Each issue has a field, category and explanation.
Exit code **2** means the host must inspect the report; it does not mean model inference
failed. Malformed JSON/schema errors also exit 2, with an error on stderr.

Once the choices are complete, review validates the actual reference structure,
chain intervals and hotspot mapping using the same validator as the pipeline. Invalid
mapping still prevents compilation. `checks.target_mapping` distinguishes a completed
mapping check from an incomplete brief; mapping is deferred until choices are complete.
The report contains the normalized brief, execution settings and a preview of the task.

```bash
alpd task build artifacts/my-design/brief.json \
  --output artifacts/my-design/task.json
alpd pipeline preflight artifacts/my-design/task.json --runtime runtime.json
```

Successful compilation returns `ready_for_preflight` and exit code 0. It does not
verify runtime assets or imply biological suitability. Incomplete briefs produce no
task file. Keep the original brief to preserve the user's context and length preference.
Rebuild to a new output path after changing scientific choices.

The host should translate concrete requirements in prose into supported task fields,
and record unresolved ones explicitly. Prose in `goal` is retained as the task description;
it is not enforced by a model. Do not erase an unsupported user requirement merely to
make the compiler accept the brief. A supplied complete execution task may continue
directly through the existing validation/preflight path.

Runtime paths remain in a separate reusable `runtime.json`. Candidate count is the
number of distinct seeds, with one candidate per seed. Complex screening budgets are
separate `pipeline prepare` options such as `--max-evaluations`; they do not cap
generation or monomer checks. Follow the [pipeline guide](PIPELINE.md) after preflight.

## Common practice versus current support

Target structures and chain selection are common inputs for structure-based binder
design. Hotspots and fixed lengths are not universal requirements: BindCraft allows
omitted hotspots and a length range; RFdiffusion also accepts generated-length ranges.
See the [BindCraft input documentation](https://github.com/martinpacesa/BindCraft#running-the-script-locally-and-explanation-of-settings)
and [RFdiffusion usage](https://github.com/RosettaCommons/RFdiffusion#usage).

The current ALPD complete pipeline requires explicit hotspots, consistent with the
[ODesign protein-binding protein interface](https://github.com/OTeam-AI4S/ODesign#protein-binding-protein),
and checks a fixed binder length in its own pipeline validator. Supporting a range
or unspecified hotspots in a brief does not add those generation modes to the backend.
JSON is ALPD's task contract, not an industry-wide binder-design file standard.

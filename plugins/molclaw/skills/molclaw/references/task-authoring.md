# Author a pipeline task from intent

The host converts natural language to a local JSON task; the Python executor validates
that task and runs the models. No separate LLM client, provider configuration or API key
is needed by MolClaw. Use the installed executor's `InteractionDesignSpec` contract in
`interaction-design-mvp/src/interaction_design/specs.py` for supported fields, and run
`interaction-design validate TASK.json` after authoring. Unsupported fields are errors.

## Required biological inputs

Before making a runnable task, establish the following from the user or supplied files:

- Local target structure and the intended chain/residue mapping. A target name or a
  sequence alone does not determine a unique structure, construct or residue numbering.
- Target segment(s) to keep fixed and the requested hotspot residues in that reference.
  Verify that the intended chains/residues are present; do not silently renumber them.
- Protein binder length and constraints, and which molecule is the binder.
- ODesign protein model choice and generation seeds, plus the complex screening budget.

Ask for missing biological requirements instead of assigning a familiar demonstration
target. An existing user-supplied task can already settle these choices. Computational
defaults such as selection batch size may follow the CLI defaults. Keep scientific
assumptions explicit, and obtain runtime paths from the local setup or user.

The initial pipeline supports two noncyclic protein molecules: a fixed-length generated
binder and a fixed target, with hotspots and one sample and one inverse-fold result per
seed. More general molecule types accepted by the domain schema are not automatically
supported by this pipeline. MSA input/generation, motifs, partial diffusion and atom
constraints are outside this initial pipeline. Seeds must be distinct integers from
0 through 2^31−1. Preparation checks actual target/hotspot mapping and local model assets
before inference. `generation.seeds` defines the generated candidate pool;
`max_evaluations` limits only the candidates sent to complex evaluation.

## JSON shape

This example is a structural template. The local path, chain `A`, residue range
`1–100`, hotspot `50`, binder length `60` and seeds are illustrative values, not inferred
biological requirements. Replace them with supplied values before running it.

```json
{
  "schema_version": "0.1",
  "name": "user_protein_binder",
  "description": "Diagnostic design against the user-specified target construct.",
  "model": "odesign_base_prot_flex",
  "design_modality": "protein",
  "reference_structure": "/replace/with/user-supplied-target.pdb",
  "molecules": [
    {
      "id": "binder",
      "type": "protein",
      "role": "design",
      "segments": [{"kind": "generated", "min_length": 60, "max_length": 60}]
    },
    {
      "id": "target",
      "type": "protein",
      "role": "context",
      "segments": [{"kind": "fixed", "chain": "A", "start": 1, "end": 100}]
    }
  ],
  "hotspots": [{"chain": "A", "residue": 50}],
  "center_method": "hotspot_center",
  "generation": {
    "seeds": [42, 43, 44, 45],
    "samples_per_seed": 1,
    "inverse_fold_topk": 1,
    "use_msa": false
  },
  "evaluation": {
    "binder_molecule": "binder",
    "metric_rules": [],
    "require_all_metrics": false
  }
}
```

The `evaluation.binder_molecule` field identifies the binder. The legacy domain schema
also allows AF3/Rosetta metric rules, but this pipeline does not run AF3 or Rosetta and
does not use those rules for selection. Empty `metric_rules` avoids importing legacy
cutoffs into a new task. Pipeline screening discloses ESMFold2/geometry diagnostics
under its own observation protocol. Existing legacy task fields do not imply those
models have run.

Relative `reference_structure` paths resolve against the task file. Prefer absolute
paths for runtime assets and cross-host launches. Schema validation alone does not
prove that the requested biological mapping or model assets are suitable.

## Runtime JSON

The full runtime has exactly `generation`, `monomer` and `complex` sections. Use
available absolute paths and the executor's pinned model revisions. The placeholder
paths below are not runnable; retain the existing installed runtime's valid values.

```json
{
  "generation": {
    "odesign_repo": "/path/to/ODesign",
    "data_root": "/path/to/chemical-components",
    "checkpoint_root": "/path/to/models/ckpt",
    "python_executable": "/path/to/odesign-environment/bin/python",
    "cuda_visible_devices": "0",
    "timeout_seconds": 900
  },
  "monomer": {
    "python": "/path/to/esmfold-v1-environment/bin/python",
    "model_dir": "/path/to/local/esmfold-v1",
    "gpu": 0,
    "timeout_seconds": 900
  },
  "complex": {
    "python": "/path/to/biohub-environment/bin/python",
    "model_dir": "/path/to/ESMFold2/snapshots/1ebf0e3481a5184eb6171d40615c79e384b48796",
    "esmc_dir": "/path/to/ESMC-6B/snapshots/45b0fa5d7fb06faefbd5e3b89bdcef35d564e79a",
    "ccd": "/path/to/trusted/ccd.pkl",
    "gpu": 0,
    "cpu_threads": 4,
    "timeout_seconds": 1800,
    "unset_ld_library_path": true
  }
}
```

Monomer evaluation uses ESMFold v1; complex evaluation uses ESMFold2. Runtime stage
timeouts are distinct from the complex selection wall-time budget. Host tool timeouts
must also allow the requested work. If a stage is cancelled or fails, inspect its
preserved records before proceeding; repeated blind `run` calls are not recovery.

Use local cached assets. This plugin does not download or install the model runtimes.
Once the JSON files are ready, follow [pipeline commands](cli-and-protocol.md#full-design-pipeline)
to validate, prepare, run, select and export.

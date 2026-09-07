"""Synthetic preflight fixtures do not load models or establish binder quality."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from biotite.structure import AtomArray
from biotite.structure.io.pdb import PDBFile

from interaction_design import pipeline_config
from interaction_design.assets import (
    default_asset_lock,
    load_asset_pins,
    load_odesign_revision,
    write_asset_manifest,
)
from interaction_design.evaluation.complex import default_policy
from interaction_design.pipeline_config import validate_pipeline_runtime, validate_pipeline_task
from interaction_design.runtime.local import ODesignExecutionError
from interaction_design.specs import (
    FixedSegment,
    GeneratedSegment,
    InteractionDesignSpec,
    ResidueRef,
)


@pytest.fixture
def task(tmp_path):
    atoms = AtomArray(3)
    atoms.chain_id = np.array(["X", "X", "X"])
    atoms.res_id = np.array([20, 21, 22])
    atoms.res_name = np.array(["ALA", "CYS", "ASP"])
    atoms.atom_name = np.array(["CA"] * 3)
    atoms.element = np.array(["C"] * 3)
    atoms.coord = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
    pdb = PDBFile()
    pdb.set_structure(atoms)
    reference = tmp_path / "synthetic-target.pdb"
    pdb.write(reference)
    return InteractionDesignSpec.model_validate(
        {
            "name": "synthetic_pipeline",
            "model": "odesign_base_prot_flex",
            "design_modality": "protein",
            "reference_structure": str(reference),
            "molecules": [
                {
                    "id": "target",
                    "type": "protein",
                    "role": "context",
                    "segments": [{"kind": "fixed", "chain": "X", "start": 20, "end": 22}],
                },
                {
                    "id": "binder",
                    "type": "protein",
                    "role": "design",
                    "segments": [{"kind": "generated", "min_length": 5, "max_length": 5}],
                },
            ],
            "hotspots": [{"chain": "X", "residue": 21}],
            "generation": {"seeds": [0, 2**31 - 1]},
            "evaluation": {"binder_molecule": "binder"},
        }
    )


@pytest.fixture
def runtime(tmp_path, task, monkeypatch):
    def touch(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic local asset\n", encoding="utf-8")
        return str(path)

    repo = tmp_path / "odesign"
    touch(repo / "scripts" / "inference.py")
    data = tmp_path / "ccd"
    for filename in ("components.v20240608.cif", "components.v20240608.cif.rdkit_mol.pkl"):
        touch(data / filename)
    checkpoints = tmp_path / "models" / "ckpt"
    for filename in (f"{task.model}.pt", "oinvfold_protein.ckpt"):
        touch(checkpoints / filename)
    write_asset_manifest(checkpoints.parent, load_asset_pins(default_asset_lock()))
    monomer = tmp_path / "esmfold-v1"
    for filename in ("config.json", "pytorch_model.bin"):
        touch(monomer / filename)
    policy = default_policy()
    complex_dir = tmp_path / "folding" / "snapshots" / policy["model_revision"]
    esmc = tmp_path / "esmc" / "snapshots" / policy["esmc_revision"]
    for directory in (complex_dir, esmc):
        touch(directory / "config.json")
        touch(directory / "model.safetensors")

    def inspect_revision(path, expected):
        assert path == repo
        assert expected == load_odesign_revision(default_asset_lock())
        return expected

    monkeypatch.setattr(pipeline_config, "_verify_git_revision", inspect_revision)

    def no_inference(*args, **kwargs):
        pytest.fail("preflight must never launch model inference")

    monkeypatch.setattr(
        "interaction_design.runtime.local.LocalODesignExecutor.execute", no_inference
    )
    monkeypatch.setattr("interaction_design.evaluation.monomer.run_monomer_batch", no_inference)
    monkeypatch.setattr("interaction_design.evaluation.complex.run_complex_batch", no_inference)
    monkeypatch.setattr("interaction_design.runtime.process.run_logged", no_inference)
    return {
        "generation": {
            "python_executable": sys.executable,
            "odesign_repo": str(repo),
            "data_root": str(data),
            "checkpoint_root": str(checkpoints),
        },
        "monomer": {"python": sys.executable, "model_dir": str(monomer)},
        "complex": {
            "python": sys.executable,
            "model_dir": str(complex_dir),
            "esmc_dir": str(esmc),
            "ccd": touch(tmp_path / "trusted" / "ccd.pkl"),
        },
    }


def test_accepts_binder_in_either_order_and_fixed_total_length(task):
    validate_pipeline_task(task)
    task.molecules.reverse()
    task.molecules[0].segments[0].max_length = 10
    task.molecules[0].total_length = 7
    validate_pipeline_task(task)


def test_accepts_unambiguous_multiple_fixed_segments_and_normal_centering(task):
    task.molecules[0].segments = [
        FixedSegment(chain="X", start=22, end=22),
        FixedSegment(chain="X", start=20, end=21),
    ]
    for center in ("none", "global_center", "hotspot_center", "usr_provide_center"):
        task.center_method = center
        task.user_center = (0.0, 1.0, 2.0) if center == "usr_provide_center" else None
        validate_pipeline_task(task)


@pytest.mark.parametrize(
    "change,match",
    [
        ("extra_molecule", "exactly two"),
        ("cyclic", "linear protein"),
        ("nonprotein", "linear protein"),
        ("wrong_binder", "fully generated binder"),
        ("two_designs", "fixed target"),
        ("partial_binder", "fully generated binder"),
        ("variable_length", "fixed-length"),
        ("msa", "MSA"),
        ("use_msa", "MSA"),
        ("constraints", "atom constraints"),
        ("partial_diffusion", "partial diffusion"),
        ("motif", "motifs"),
        ("multiple_samples", "one design per seed"),
        ("multiple_inverse_folds", "one design per seed"),
        ("no_hotspots", "hotspots"),
        ("no_reference", "local reference"),
        ("remote_reference", "local reference"),
        ("missing_reference", "unavailable"),
    ],
)
def test_rejects_unsupported_tasks_before_runtime_access(task, change, match):
    if change == "extra_molecule":
        task.molecules.append(task.molecules[0].model_copy())
    elif change == "cyclic":
        task.molecules[1].cyclic = True
    elif change == "nonprotein":
        task.molecules[0].type = "rna"
    elif change == "wrong_binder":
        task.evaluation.binder_molecule = "target"
    elif change == "two_designs":
        task.molecules[0].role = "design"
    elif change == "partial_binder":
        task.molecules[1].segments.append(FixedSegment(chain="X", start=20, end=20))
    elif change == "variable_length":
        task.molecules[1].segments[0] = GeneratedSegment(min_length=5, max_length=6)
    elif change == "msa":
        task.molecules[0].msa = "target.a3m"
    elif change == "use_msa":
        task.generation.use_msa = True
    elif change == "constraints":
        task.atom_constraints = [object()]
    elif change == "partial_diffusion":
        task.partial_diffusion = [object()]
    elif change == "motif":
        task.motif_scaffolding = True
    elif change == "multiple_samples":
        task.generation.samples_per_seed = 2
    elif change == "multiple_inverse_folds":
        task.generation.inverse_fold_topk = 2
    elif change == "no_hotspots":
        task.hotspots = []
    elif change == "no_reference":
        task.reference_structure = None
    elif change == "remote_reference":
        task.reference_structure = "https://example.invalid/target.pdb"
    elif change == "missing_reference":
        Path(task.reference_structure).unlink()
    with pytest.raises(ValueError, match=match):
        validate_pipeline_runtime(task, {})


@pytest.mark.parametrize("seeds", [[1, 1], [-1], [2**31], [True], ["1"], []])
def test_rejects_duplicate_or_invalid_seeds(task, seeds):
    task.generation.seeds = seeds
    with pytest.raises(ValueError, match="seeds must be distinct integers"):
        validate_pipeline_task(task)


@pytest.mark.parametrize("change", ["missing_residue", "missing_chain", "outside", "duplicate"])
def test_checks_actual_target_and_hotspots(task, change):
    if change == "missing_residue":
        task.molecules[0].segments[0].end = 23
    elif change == "missing_chain":
        task.molecules[0].segments[0].chain = "Z"
    elif change == "outside":
        task.hotspots = [ResidueRef(chain="X", residue=23)]
    else:
        task.hotspots *= 2
    with pytest.raises(ValueError, match="target or hotspot mapping"):
        validate_pipeline_task(task)


def test_normalizes_every_stage_without_mutating_input_or_loading_models(task, runtime):
    before = deepcopy(runtime)
    normalized = validate_pipeline_runtime(task, runtime)
    assert runtime == before
    assert normalized["generation"]["timeout_seconds"] == 900.0
    assert normalized["generation"]["cuda_visible_devices"] == "0"
    assert normalized["monomer"]["timeout_seconds"] == 900.0
    assert normalized["monomer"]["gpu"] == 0
    assert normalized["complex"]["timeout_seconds"] == 1800.0
    assert normalized["complex"]["gpu"] == 0
    assert normalized["complex"]["cpu_threads"] == 4
    assert normalized["complex"]["unset_ld_library_path"] is False
    assert validate_pipeline_runtime(task, normalized) == normalized
    json.dumps(normalized, allow_nan=False)


@pytest.mark.parametrize("stage", ["generation", "monomer", "complex"])
@pytest.mark.parametrize("timeout", [True, "900", 0, -1, float("nan"), float("inf")])
def test_rejects_invalid_timeout_in_any_stage(task, runtime, stage, timeout):
    runtime[stage]["timeout_seconds"] = timeout
    with pytest.raises(ValueError, match="timeout"):
        validate_pipeline_runtime(task, runtime)


@pytest.mark.parametrize("gpu", [True, -1, 0.5, "0", None])
def test_requires_integer_monomer_gpu(task, runtime, gpu):
    runtime["monomer"]["gpu"] = gpu
    with pytest.raises(ValueError, match="monomer runtime gpu"):
        validate_pipeline_runtime(task, runtime)


@pytest.mark.parametrize(
    "stage,key",
    [
        ("generation", "odesign_repo"),
        ("generation", "python_executable"),
        ("monomer", "python"),
        ("monomer", "model_dir"),
        ("complex", "python"),
        ("complex", "model_dir"),
        ("complex", "esmc_dir"),
        ("complex", "ccd"),
    ],
)
def test_runtime_requires_available_absolute_paths(task, runtime, stage, key):
    runtime[stage][key] = "relative/path"
    with pytest.raises(ValueError, match="absolute"):
        validate_pipeline_runtime(task, runtime)


@pytest.mark.parametrize("stage", ["root", "generation", "monomer", "complex"])
def test_rejects_unknown_runtime_options(task, runtime, stage):
    (runtime if stage == "root" else runtime[stage])["unknown"] = True
    with pytest.raises(ValueError, match="requires"):
        validate_pipeline_runtime(task, runtime)


@pytest.mark.parametrize("stage", ["generation", "monomer", "complex"])
def test_rejects_missing_or_nonobject_stages(task, runtime, stage):
    del runtime[stage]
    with pytest.raises(ValueError, match="pipeline runtime requires"):
        validate_pipeline_runtime(task, runtime)
    runtime[stage] = None
    with pytest.raises(ValueError, match=f"{stage} runtime requires"):
        validate_pipeline_runtime(task, runtime)


@pytest.mark.parametrize(
    "stage,key,filename",
    [
        ("generation", "odesign_repo", "scripts/inference.py"),
        ("generation", "data_root", "components.v20240608.cif"),
        ("generation", "data_root", "components.v20240608.cif.rdkit_mol.pkl"),
        ("generation", "checkpoint_root", "odesign_base_prot_flex.pt"),
        ("generation", "checkpoint_root", "oinvfold_protein.ckpt"),
        ("monomer", "model_dir", "config.json"),
        ("monomer", "model_dir", "pytorch_model.bin"),
        ("complex", "model_dir", "model.safetensors"),
        ("complex", "esmc_dir", "model.safetensors"),
    ],
)
def test_missing_assets_fail_before_launch(task, runtime, stage, key, filename):
    (Path(runtime[stage][key]) / filename).unlink()
    with pytest.raises(ValueError, match=f"{stage} runtime.*assets are missing"):
        validate_pipeline_runtime(task, runtime)


def test_verifies_checkpoint_provenance_before_launch(task, runtime):
    checkpoint = Path(runtime["generation"]["checkpoint_root"]) / f"{task.model}.pt"
    checkpoint.write_bytes(b"corrupted synthetic checkpoint")
    with pytest.raises(ValueError, match="checkpoint checksum/size mismatch"):
        validate_pipeline_runtime(task, runtime)


def test_requires_generation_git_revision_pin(task, runtime, monkeypatch):
    def wrong_revision(*args):
        raise ODesignExecutionError("ODesign revision mismatch")

    monkeypatch.setattr(pipeline_config, "_verify_git_revision", wrong_revision)
    with pytest.raises(ValueError, match="ODesign revision mismatch"):
        validate_pipeline_runtime(task, runtime)


def test_preserves_virtual_environment_executable_symlinks(task, runtime, tmp_path):
    python = tmp_path / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    runtime["monomer"]["python"] = str(python)
    normalized = validate_pipeline_runtime(task, runtime)
    assert normalized["monomer"]["python"] == str(python)


def test_requires_executable_python(task, runtime, tmp_path):
    python = tmp_path / "not-executable"
    python.write_text("not a python executable")
    runtime["monomer"]["python"] = str(python)
    with pytest.raises(ValueError, match="Python executable is unavailable"):
        validate_pipeline_runtime(task, runtime)


def test_checks_every_complex_shard_before_launch(task, runtime):
    directory = Path(runtime["complex"]["esmc_dir"])
    (directory / "model.safetensors").unlink()
    (directory / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"a": "shard1.safetensors", "b": "shard2.safetensors"}})
    )
    (directory / "shard1.safetensors").write_bytes(b"synthetic shard one")
    with pytest.raises(ValueError, match="shard2.safetensors"):
        validate_pipeline_runtime(task, runtime)
    (directory / "shard2.safetensors").write_bytes(b"synthetic shard two")
    validate_pipeline_runtime(task, runtime)


@pytest.mark.parametrize("mapping", [{}, {"a": "../escape"}, {"a": "/absolute"}, {"a": None}])
def test_rejects_invalid_complex_shard_index(task, runtime, mapping):
    directory = Path(runtime["complex"]["esmc_dir"])
    (directory / "model.safetensors.index.json").write_text(json.dumps({"weight_map": mapping}))
    with pytest.raises(ValueError, match="model shard index is invalid"):
        validate_pipeline_runtime(task, runtime)


def test_preflight_cli_checks_resources_without_creating_jobs(tmp_path, task, runtime, capsys):
    from interaction_design.cli import main
    from interaction_design.persistence import write_json

    task_path = write_json(tmp_path / "task.json", task.model_dump(mode="json"))
    runtime_path = write_json(tmp_path / "runtime.json", runtime)
    before = sorted(str(path) for path in tmp_path.rglob("*"))
    assert main(["pipeline", "preflight", str(task_path), "--runtime", str(runtime_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "status": "passed",
        "inference": "not_run",
        "gpu": "not_probed",
    }
    assert sorted(str(path) for path in tmp_path.rglob("*")) == before
    Path(runtime["monomer"]["model_dir"], "pytorch_model.bin").unlink()
    with pytest.raises(SystemExit) as error:
        main(["pipeline", "preflight", str(task_path), "--runtime", str(runtime_path)])
    assert error.value.code == 2

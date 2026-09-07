"""Local, inference-free preflight for the supported protein interaction pipeline."""

from __future__ import annotations

import json
import math
import os
from copy import deepcopy
from pathlib import Path

from interaction_design.assets import (
    default_asset_lock,
    load_odesign_revision,
    verify_task_checkpoints,
)
from interaction_design.evaluation.feedback import map_target_residues
from interaction_design.evaluation.interface import protein_chain, read_atoms, residue_sequence
from interaction_design.runtime.local import ODesignExecutionError, _verify_git_revision
from interaction_design.selection_catalogue import validate_complex_runtime
from interaction_design.specs import FixedSegment, GeneratedSegment, InteractionDesignSpec


def validate_pipeline_task(spec: InteractionDesignSpec) -> None:
    """Require one fixed target and one fully generated, fixed-length protein binder.

    All reference coordinates and hotspots are checked before any model can run.
    This v1 path supports local PDB/mmCIF targets and ordinary centering options;
    motif design, partial diffusion, atom constraints and MSA are separate workflows.
    """
    if len(spec.molecules) != 2 or any(
        molecule.type != "protein" or molecule.cyclic for molecule in spec.molecules
    ):
        raise ValueError("pipeline v1 requires exactly two linear protein molecules")
    binder = spec.molecules[spec.binder_index]
    target = spec.molecules[1 - spec.binder_index]
    if (
        spec.design_modality != "protein"
        or binder.role != "design"
        or target.role != "context"
        or not binder.segments
        or any(not isinstance(segment, GeneratedSegment) for segment in binder.segments)
        or not target.segments
        or any(not isinstance(segment, FixedSegment) for segment in target.segments)
    ):
        raise ValueError("pipeline v1 requires one fully generated binder and one fixed target")
    if binder.total_length is None and binder.min_length != binder.max_length:
        raise ValueError("pipeline v1 requires a fixed-length binder")
    if spec.atom_constraints or spec.partial_diffusion or spec.motif_scaffolding:
        raise ValueError(
            "pipeline v1 does not support atom constraints, partial diffusion or motifs"
        )
    if spec.generation.use_msa or any(molecule.msa is not None for molecule in spec.molecules):
        raise ValueError("pipeline v1 does not support MSA input or MSA generation")
    if spec.user_center is not None and not all(math.isfinite(x) for x in spec.user_center):
        raise ValueError("pipeline user center must contain finite coordinates")
    if not math.isfinite(spec.generation.inverse_fold_temperature):
        raise ValueError("pipeline inverse-folding temperature must be finite")
    if spec.generation.samples_per_seed != 1 or spec.generation.inverse_fold_topk != 1:
        raise ValueError("pipeline v1 requires exactly one design per seed")
    seeds = spec.generation.seeds
    if (
        not seeds
        or any(type(seed) is not int or not 0 <= seed <= 2**31 - 1 for seed in seeds)
        or len(set(seeds)) != len(seeds)
    ):
        raise ValueError("pipeline seeds must be distinct integers in 0..2^31-1")
    if not spec.hotspots:
        raise ValueError("pipeline v1 requires target hotspots")
    if not spec.reference_structure or "://" in spec.reference_structure:
        raise ValueError("pipeline v1 requires a local reference structure")
    reference = Path(spec.reference_structure)
    if not reference.is_file():
        raise ValueError(f"pipeline reference structure is unavailable: {reference}")
    try:
        atoms = read_atoms(reference, author=True)
        sequence = ""
        for segment in target.segments:
            region = atoms[
                (atoms.chain_id == segment.chain)
                & (atoms.res_id >= segment.start)
                & (atoms.res_id <= segment.end)
            ]
            _, residues, _ = protein_chain(region, segment.chain)
            sequence += residue_sequence(residues)
        map_target_residues(spec, atoms, sequence)
    except Exception as error:
        raise ValueError(f"pipeline target or hotspot mapping is invalid: {error}") from error


def _object(value: object, label: str, required: set[str], optional: set[str]) -> dict:
    if (
        not isinstance(value, dict)
        or not required <= value.keys()
        or value.keys() - (required | optional)
    ):
        raise ValueError(
            f"{label} requires {', '.join(sorted(required))}; "
            f"optional keys: {', '.join(sorted(optional)) or 'none'}"
        )
    return deepcopy(value)


def _path(value: object, label: str, *, directory: bool = False, executable: bool = False) -> str:
    if not isinstance(value, (str, Path)) or not str(value):
        raise ValueError(f"{label} must be an available absolute path")
    path = Path(value)
    if not path.is_absolute() or not (path.is_dir() if directory else path.is_file()):
        raise ValueError(
            f"{label} must be an available absolute {'directory' if directory else 'file'}"
        )
    if executable and not os.access(path, os.X_OK):
        raise ValueError(f"{label} Python executable is unavailable")
    # Preserve a virtual environment's executable path instead of dereferencing its symlink.
    return str(path if executable else path.resolve())


def _seconds(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{label} timeout_seconds must be positive and finite")
    return float(value)


def _required_files(paths: list[Path], label: str) -> None:
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError(f"{label} required local assets are missing: {', '.join(missing)}")


def _complex_weights(folder: Path) -> None:
    """Check the worker's complete safetensors file set without reading tensor data."""
    index = folder / "model.safetensors.index.json"
    if not index.exists():
        _required_files([folder / "model.safetensors"], "complex runtime")
        return
    try:
        mapping = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
        if not isinstance(mapping, dict) or not mapping:
            raise ValueError("empty weight_map")
        names = list(mapping.values())
        if any(
            not isinstance(name, str)
            or not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            for name in names
        ):
            raise ValueError("weight_map must contain local relative shard filenames")
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"complex runtime model shard index is invalid: {index}") from error
    _required_files([folder / name for name in sorted(set(names))], "complex runtime")


def validate_pipeline_runtime(spec: InteractionDesignSpec, runtime: dict) -> dict:
    """Validate every local stage before generation; return independent normalized options.

    ODesign source and checkpoint provenance use the packaged asset lock. ESMFold2
    uses its existing pinned PPI policy. ESMFold v1 accepts a complete local snapshot,
    without imposing the separate historical baseline experiment's model revision.
    This function never imports a model, starts inference, or downloads an asset.
    """
    validate_pipeline_task(spec)
    options = _object(runtime, "pipeline runtime", {"generation", "monomer", "complex"}, set())
    generation = _object(
        options["generation"],
        "generation runtime",
        {"odesign_repo", "data_root", "checkpoint_root", "python_executable"},
        {"cuda_visible_devices", "timeout_seconds"},
    )
    generation["python_executable"] = _path(
        generation["python_executable"], "generation runtime Python", executable=True
    )
    for key in ("odesign_repo", "data_root", "checkpoint_root"):
        generation[key] = _path(generation[key], f"generation runtime {key}", directory=True)
    generation["timeout_seconds"] = _seconds(
        generation.get("timeout_seconds", 900), "generation runtime"
    )
    generation.setdefault("cuda_visible_devices", "0")
    if (
        not isinstance(generation["cuda_visible_devices"], str)
        or not generation["cuda_visible_devices"].strip()
    ):
        raise ValueError("generation runtime cuda_visible_devices must be a nonempty string")
    repo, data, checkpoints = (
        Path(generation[key]) for key in ("odesign_repo", "data_root", "checkpoint_root")
    )
    _required_files(
        [
            repo / "scripts" / "inference.py",
            data / "components.v20240608.cif",
            data / "components.v20240608.cif.rdkit_mol.pkl",
            checkpoints / f"{spec.model}.pt",
            checkpoints / f"oinvfold_{spec.design_modality}.ckpt",
        ],
        "generation runtime",
    )

    monomer = _object(
        options["monomer"], "monomer runtime", {"python", "model_dir"}, {"gpu", "timeout_seconds"}
    )
    monomer["python"] = _path(monomer["python"], "monomer runtime Python", executable=True)
    monomer["model_dir"] = _path(monomer["model_dir"], "monomer runtime model_dir", directory=True)
    monomer.setdefault("gpu", 0)
    if type(monomer["gpu"]) is not int or monomer["gpu"] < 0:
        raise ValueError("monomer runtime gpu must be a nonnegative integer")
    monomer["timeout_seconds"] = _seconds(monomer.get("timeout_seconds", 900), "monomer runtime")
    _required_files(
        [Path(monomer["model_dir"]) / name for name in ("config.json", "pytorch_model.bin")],
        "monomer runtime ESMFold v1",
    )

    complex_options = _object(
        options["complex"],
        "complex runtime",
        {"python", "model_dir", "esmc_dir", "ccd"},
        {"gpu", "cpu_threads", "timeout_seconds", "unset_ld_library_path"},
    )
    for key in ("python", "model_dir", "esmc_dir", "ccd"):
        complex_options[key] = _path(
            complex_options[key],
            f"complex runtime {key}",
            directory=key in {"model_dir", "esmc_dir"},
            executable=key == "python",
        )
    complex_options = validate_complex_runtime(complex_options)
    for key, default in (("gpu", 0), ("cpu_threads", 4), ("unset_ld_library_path", False)):
        complex_options.setdefault(key, default)
    for key in ("model_dir", "esmc_dir"):
        _complex_weights(Path(complex_options[key]))

    try:
        _verify_git_revision(repo, load_odesign_revision(default_asset_lock()))
        verify_task_checkpoints(checkpoints, spec.model, spec.design_modality)
    except (ODesignExecutionError, OSError, ValueError, KeyError, TypeError) as error:
        raise ValueError(f"generation runtime pinned asset verification failed: {error}") from error
    return {"generation": generation, "monomer": monomer, "complex": complex_options}

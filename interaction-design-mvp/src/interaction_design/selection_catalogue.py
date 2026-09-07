"""Validated candidate catalogues and bounded complex evaluation of saved designs."""

from __future__ import annotations

import math
import os
import time
from copy import deepcopy
from pathlib import Path

import numpy as np

from interaction_design.assets import sha256_file
from interaction_design.campaign_tools import load_feedback
from interaction_design.conversion import spec_to_system
from interaction_design.evaluation.complex import (
    default_policy,
    prepare_complex_batch,
    run_complex_batch,
)
from interaction_design.evaluation.feedback import (
    feedback_policy_path,
    map_target_residues,
    write_interface_feedback,
)
from interaction_design.evaluation.interface import (
    interface_geometry,
    protein_chain,
    read_atoms,
    residue_sequence,
)
from interaction_design.evaluation.jobs import checked_path, read_json
from interaction_design.evaluation.monomer import completed_monomer_output, load_monomer_job
from interaction_design.evaluation.monomer_report import read_monomer_batch
from interaction_design.manifest import canonical_sha256
from interaction_design.outputs import _chain_id, parse_odesign_outputs
from interaction_design.persistence import write_json
from interaction_design.selection import post_features, pre_vector
from interaction_design.specs import InteractionDesignSpec


def default_interface_policy() -> dict:
    """Read the same frozen geometry protocol used for interface feedback."""
    return read_json(feedback_policy_path())


def build_catalogue(monomer_job: Path) -> dict:
    """Read and validate a complete monomer batch without rewriting any saved report."""
    job = monomer_job.resolve()
    prepared = load_monomer_job(job)
    output = completed_monomer_output(job)
    rows = read_monomer_batch(job)
    root = Path(prepared["generation_run"]).resolve()
    generation = read_json(root / "manifest.json")
    if generation.get("stage") != "generation" or generation.get("status") != "generated":
        raise ValueError("candidate selection requires a successful saved generation")
    spec = InteractionDesignSpec.model_validate(generation["task"])
    if canonical_sha256(spec.model_dump(mode="json")) != generation["task_sha256"]:
        raise ValueError("generation task checksum mismatch")
    binder_index = spec.binder_index
    if (
        len(spec.molecules) != 2
        or any(x.type != "protein" or x.cyclic for x in spec.molecules)
        or spec.molecules[binder_index].role != "design"
        or spec.molecules[1 - binder_index].role != "context"
        or not spec.hotspots
    ):
        raise ValueError("selection requires a linear protein binder, fixed target and hotspots")
    candidate_ids = [row["candidate_id"] for row in rows]
    if (
        not rows
        or len(set(candidate_ids)) != len(rows)
        or len(set(generation["candidate_ids"])) != len(generation["candidate_ids"])
        or set(candidate_ids) != set(generation["candidate_ids"])
    ):
        raise ValueError("monomer candidates differ from the complete generation")
    if len({row["length"] for row in rows}) != 1:
        raise ValueError("selection diversity requires equal-length candidate sequences")

    sources = {}

    def add_source(path: Path, expected: str | None = None) -> None:
        path = path.resolve()
        actual = sha256_file(path)
        if expected is not None and actual != expected:
            raise ValueError("candidate catalogue source checksum mismatch")
        if str(path) in sources and sources[str(path)] != actual:
            raise ValueError("candidate catalogue source changed during validation")
        sources[str(path)] = actual

    add_source(root / "manifest.json", prepared["generation_manifest_sha256"])
    add_source(job / "manifest.json")
    add_source(job / "completed.json")
    receipt = read_json(job / "completed.json")
    add_source(checked_path(job, receipt["execution"]), receipt["execution"]["sha256"])
    add_source(output / "manifest.json", receipt["output_manifest"]["sha256"])
    for record in read_json(output / "manifest.json")["artifacts"]:
        add_source(checked_path(output, record), record["sha256"])
    if prepared["protocol"]:
        record = prepared["protocol"]
        add_source(checked_path(job, record), record["sha256"])
    records = {row["path"]: row for row in generation["artifacts"]}
    if len(records) != len(generation["artifacts"]):
        raise ValueError("duplicate generation artifact records")
    for record in records.values():
        add_source(checked_path(root, record), record["sha256"])
    original_record = generation["inputs"]["reference_structure"]
    original_path = Path(original_record["path"]).resolve()
    add_source(original_path, original_record["sha256"])
    original = read_atoms(original_path, author=True)
    geometry_policy = default_interface_policy()
    add_source(feedback_policy_path())

    instances = parse_odesign_outputs(root / "output", spec_to_system(spec), spec)
    by_id = {instance.id: instance for instance in instances}
    if len(by_id) != len(instances) or set(by_id) != set(candidate_ids):
        raise ValueError("generated structures differ from the candidate catalogue")
    requests = {}
    for record in prepared["requests"]:
        request_path = checked_path(job, record)
        add_source(request_path, record["sha256"])
        requests[record["candidate_id"]] = read_json(request_path)
    pool = []
    binder_chain, target_chain = _chain_id(binder_index), _chain_id(1 - binder_index)
    target_id = spec.molecules[1 - binder_index].id
    for row in rows:
        key = row["candidate_id"]
        request, instance = requests[key], by_id[key]
        source = Path(instance.metadata["artifacts"]["structure"]).resolve()
        reference = request["reference"]
        if (
            Path(request["generation_run"]).resolve() != root
            or request["generation_manifest_sha256"] != prepared["generation_manifest_sha256"]
            or Path(reference["path"]).resolve() != source
            or reference["chain"] != binder_chain
            or reference["sha256"] != sources.get(str(source))
            or request["odesign"] != instance.metadata["odesign"]
            or row["sequence"] != "".join(instance[binder_index].rep)
        ):
            raise ValueError("monomer request identity differs from the original generation")
        atoms = read_atoms(source)
        binder_atoms, binder_residues, _ = protein_chain(atoms, binder_chain)
        _, target_residues, _ = protein_chain(atoms, target_chain)
        target_sequence = residue_sequence(target_residues)
        reference_ca = np.asarray(request["reference_ca"], dtype=np.float64)
        generated_ca = binder_atoms[binder_atoms.atom_name == "CA"].coord
        if (
            residue_sequence(binder_residues) != row["sequence"]
            or request["context_sequences"] != {target_id: target_sequence}
            or target_sequence != "".join(instance[1 - binder_index].rep)
            or reference_ca.shape != generated_ca.shape
            or not np.array_equal(reference_ca, generated_ca)
        ):
            raise ValueError("monomer reference coordinates or sequences differ from generation")
        mapping = map_target_residues(spec, original, target_sequence)
        geometry = interface_geometry(
            atoms,
            binder_chain,
            target_chain,
            hotspot_positions=[x["target_position"] for x in mapping["hotspots"]],
            contact_distance=geometry_policy["contact_distance_angstrom"],
            clash_distance=geometry_policy["clash_distance_angstrom"],
        )
        candidate = {
            "candidate_id": key,
            "seed": row["seed"],
            "length": row["length"],
            "sequence": row["sequence"],
            "pre": {
                "monomer_plddt": round(row["mean_plddt_ca"], 6),
                "monomer_design_rmsd": round(row["ca_rmsd_angstrom"], 6),
                "generated_hotspot_coverage": round(geometry["hotspot_coverage"], 6),
                "generated_clash_residue_pairs": round(geometry["clash_residue_pair_count"], 6),
            },
        }
        pre_vector(candidate)
        pool.append(candidate)
    return {
        "pool": sorted(pool, key=lambda x: (x["seed"], x["candidate_id"])),
        "generation_run": str(root),
        "sources": [{"path": path, "sha256": digest} for path, digest in sorted(sources.items())],
        "model_policy": default_policy(),
        "geometry_policy": geometry_policy,
    }


def _generation_sha256(catalogue: dict) -> str:
    path = str((Path(catalogue["generation_run"]) / "manifest.json").resolve())
    records = [record for record in catalogue["sources"] if record["path"] == path]
    if len(records) != 1 or sha256_file(Path(path)) != records[0]["sha256"]:
        raise ValueError("catalogue generation manifest is missing, duplicated or changed")
    return records[0]["sha256"]


def _validate_feedback(catalogue: dict, payload: dict, candidate_ids: list[str]) -> None:
    expected = set(candidate_ids)
    rows = payload["observations"]
    actual = [row["candidate_id"] for row in rows]
    if (
        len(actual) != len(set(actual))
        or set(actual) != expected
        or payload["candidate_count"] != len(actual)
    ):
        raise ValueError("complex feedback candidate IDs differ from the requested catalogue")
    if payload["generation_manifest_sha256"] != _generation_sha256(catalogue):
        raise ValueError("complex feedback generation manifest differs from the catalogue")
    if (
        payload["model_policy"] != catalogue["model_policy"]
        or payload["policy"] != catalogue["geometry_policy"]
    ):
        raise ValueError("complex feedback model or geometry policy differs from the catalogue")
    geometry_path = str(feedback_policy_path().resolve())
    geometry_sources = [row for row in catalogue["sources"] if row["path"] == geometry_path]
    if len(geometry_sources) != 1:
        raise ValueError("catalogue must identify the frozen geometry protocol source")
    by_id = {candidate["candidate_id"]: candidate for candidate in catalogue["pool"]}
    for row in rows:
        if (
            row["generation_seed"] != by_id[row["candidate_id"]]["seed"]
            or row["model_policy"] != catalogue["model_policy"]
            or row["geometry_policy_sha256"] != geometry_sources[0]["sha256"]
            or row.get("acceptance") is not None
            or row.get("binding_validated") is not False
        ):
            raise ValueError("complex observation identity, policy or diagnostic scope mismatch")
        post_features(row)


def validate_replay(catalogue: dict, payload: dict) -> None:
    """Require saved observations to cover this exact generation and complete candidate pool."""
    _validate_feedback(catalogue, payload, [x["candidate_id"] for x in catalogue["pool"]])


def _positive_seconds(value, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise ValueError(f"{label} must be finite and positive")
    return float(value)


def validate_complex_runtime(runtime: dict, policy: dict | None = None) -> dict:
    """Validate local paths and options without loading models; return an independent config."""
    policy = default_policy() if policy is None else policy
    options = deepcopy(runtime)
    required = {"python", "model_dir", "esmc_dir", "ccd"}
    allowed = required | {"gpu", "cpu_threads", "timeout_seconds", "unset_ld_library_path"}
    if not required <= options.keys() or options.keys() - allowed:
        raise ValueError("complex runtime requires python, model_dir, esmc_dir and ccd")
    for name in required:
        path = Path(options[name])
        if not path.is_absolute():
            raise ValueError("complex runtime paths must be absolute")
        options[name] = str(path)
    python = Path(options["python"])
    if not python.is_file() or not os.access(python, os.X_OK):
        raise ValueError("complex runtime Python executable is unavailable")
    for name, revision in [("model_dir", "model_revision"), ("esmc_dir", "esmc_revision")]:
        path = Path(options[name]).resolve()
        if path.name != policy[revision] or not (path / "config.json").is_file():
            raise ValueError("complex runtime model snapshot differs from the pinned policy")
    if not Path(options["ccd"]).is_file():
        raise ValueError("complex runtime CCD file is unavailable")
    if (
        type(options.get("gpu", 0)) is not int
        or options.get("gpu", 0) < 0
        or type(options.get("cpu_threads", 4)) is not int
        or options.get("cpu_threads", 4) < 1
        or type(options.get("unset_ld_library_path", False)) is not bool
    ):
        raise ValueError("complex runtime GPU, CPU thread or library-path settings are invalid")
    options["timeout_seconds"] = _positive_seconds(
        options.get("timeout_seconds", 1800), "complex runtime timeout"
    )
    return options


def evaluate_selected(
    catalogue: dict,
    candidate_ids: list[str],
    directory: Path,
    remaining: float | None,
    *,
    replay: dict | None,
    runtime: dict | None,
) -> dict:
    """Reveal selected replay rows or run the selected saved sequences through ESMFold2."""
    started = time.monotonic()
    known = {x["candidate_id"] for x in catalogue["pool"]}
    if (
        not candidate_ids
        or len(candidate_ids) != len(set(candidate_ids))
        or set(candidate_ids) - known
    ):
        raise ValueError("complex selection contains empty, duplicate or unknown candidate IDs")
    if (replay is None) == (runtime is None):
        raise ValueError("provide exactly one saved replay or local complex runtime")
    if replay is not None:
        if remaining is not None:
            raise ValueError("replay does not simulate live tool wall-time budgets")
        validate_replay(catalogue, replay)
        by_id = {row["candidate_id"]: row for row in replay["observations"]}
        return {
            "observations": [deepcopy(by_id[key]) for key in candidate_ids],
            "model_policy": deepcopy(replay["model_policy"]),
            "geometry_policy": deepcopy(replay["policy"]),
            "mode": "saved_complex_replay",
            "new_model_inference": False,
        }
    if remaining is not None:
        remaining = _positive_seconds(remaining, "remaining tool wall budget")
    if (
        catalogue["model_policy"] != default_policy()
        or catalogue["geometry_policy"] != default_interface_policy()
    ):
        raise ValueError("local complex protocols differ from the frozen catalogue")
    _generation_sha256(catalogue)
    options = validate_complex_runtime(runtime, catalogue["model_policy"])
    directory = directory.resolve()
    if directory.exists() and any(directory.iterdir()):
        raise ValueError("complex evaluation requires a fresh tool output directory")
    left = None if remaining is None else remaining - (time.monotonic() - started)
    if left is not None and left <= 0:
        raise TimeoutError("tool wall budget exhausted before complex preparation")
    budget = options["timeout_seconds"] if left is None else min(options["timeout_seconds"], left)
    job = prepare_complex_batch(
        Path(catalogue["generation_run"]),
        candidates=candidate_ids,
        artifacts=directory / "complex",
        budget_seconds=budget,
    )
    left = None if remaining is None else remaining - (time.monotonic() - started)
    if left is not None and left <= 0:
        raise TimeoutError(
            "tool wall budget exhausted after complex preparation; request preserved"
        )
    options["timeout_seconds"] = budget if left is None else min(budget, left)
    config = write_json(directory / "complex-runtime.json", options)
    run_complex_batch(job, config=config)
    feedback = write_interface_feedback([job], artifacts=directory / "feedback")
    payload = load_feedback(feedback)
    _validate_feedback(catalogue, payload, candidate_ids)
    by_id = {row["candidate_id"]: row for row in payload["observations"]}
    return {
        "observations": [deepcopy(by_id[key]) for key in candidate_ids],
        "model_policy": deepcopy(payload["model_policy"]),
        "geometry_policy": deepcopy(payload["policy"]),
        "mode": "local_complex",
        "new_model_inference": True,
        "feedback": {
            "path": str(feedback),
            "observations_sha256": sha256_file(feedback / "observations.json"),
        },
    }

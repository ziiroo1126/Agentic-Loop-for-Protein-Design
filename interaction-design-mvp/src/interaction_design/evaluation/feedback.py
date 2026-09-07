"""Derived interface observations without mutating completed folding experiments."""

from __future__ import annotations

import csv
import importlib.metadata
import platform
import shutil
import time
from importlib.resources import files
from pathlib import Path

import numpy as np

from interaction_design.assets import sha256_file
from interaction_design.evaluation.complex import (
    completed_complex_output,
    load_complex_job,
    validate_complex_output,
)
from interaction_design.evaluation.interface import (
    geometry_flags,
    interface_geometry,
    protein_chain,
    read_atoms,
    residue_sequence,
)
from interaction_design.evaluation.jobs import checked_path, file_record, read_json, timestamp_id
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.specs import FixedSegment, InteractionDesignSpec


def feedback_policy_path() -> Path:
    packaged = Path(
        str(files("interaction_design").joinpath("data", "interface-feedback.protocol.json"))
    )
    return packaged if packaged.is_file() else Path(__file__).parents[3] / "config" / packaged.name


def map_target_residues(spec: InteractionDesignSpec, reference, sequence: str) -> dict:
    target = spec.molecules[1 - spec.binder_index]
    mapping, seen = [], set()
    for segment in target.segments:
        if not isinstance(segment, FixedSegment):
            raise ValueError("target mapping requires fixed reference segments")
        region = reference[
            (reference.chain_id == segment.chain)
            & (reference.res_id >= segment.start)
            & (reference.res_id <= segment.end)
        ]
        _, table, _ = protein_chain(region, segment.chain)
        if any(x["insertion_code"] for x in table) or [x["residue"] for x in table] != list(
            range(segment.start, segment.end + 1)
        ):
            raise ValueError(
                "ambiguous reference mapping: missing residues, insertion codes or order"
            )
        for row in table:
            key = (segment.chain, row["residue"])
            if key in seen:
                raise ValueError("ambiguous reference mapping: repeated target residue")
            seen.add(key)
            mapping.append(
                {
                    "target_position": len(mapping) + 1,
                    "source_chain": segment.chain,
                    "source_residue": row["residue"],
                    "res_name": row["res_name"],
                }
            )
    if residue_sequence(mapping) != sequence:
        raise ValueError("reference target sequence differs from the folded target")
    hotspots = []
    hotspot_ids = [(x.chain, x.residue) for x in spec.hotspots]
    if len(set(hotspot_ids)) != len(hotspot_ids):
        raise ValueError("duplicate requested hotspots")
    lookup = {(x["source_chain"], x["source_residue"]): x for x in mapping}
    for key in hotspot_ids:
        if key not in lookup:
            raise ValueError(f"requested hotspot {key} is absent from the evaluated target")
        hotspots.append(lookup[key])
    return {"residues": mapping, "hotspots": hotspots}


def geometry_controls(source: Path, policy: dict) -> dict:
    """4ZQK observed complex plus derived geometric controls; no model is invoked."""
    if sha256_file(source) != policy["control"]["sha256"]:
        raise ValueError("4ZQK control checksum differs from the frozen geometry protocol")
    atoms = read_atoms(source, author=True)
    kwargs = {
        "contact_distance": policy["contact_distance_angstrom"],
        "clash_distance": policy["clash_distance_angstrom"],
    }
    # Fixed control identity: 4ZQK author chain B is PD-1, author chain A is PD-L1.
    _, target_table, _ = protein_chain(atoms, "A")
    _, binder_table, _ = protein_chain(atoms, "B")
    if (
        len(target_table) != 115
        or len(binder_table) != 106
        or not residue_sequence(target_table).startswith("AFTVTVPKDLYVVEY")
    ):
        raise ValueError("control must be the observed 4ZQK PD-1/PD-L1 A/B complex")
    native = interface_geometry(atoms, "B", "A", **kwargs)
    separated = atoms.copy()
    separated.coord[separated.chain_id == "B"] += np.array([1000, 0, 0], dtype=np.float32)
    negative = interface_geometry(separated, "B", "A", **kwargs)
    collided = atoms.copy()
    ca_a = atoms[(atoms.chain_id == "A") & (atoms.atom_name == "CA")][0].coord
    ca_b = atoms[(atoms.chain_id == "B") & (atoms.atom_name == "CA")][0].coord
    collided.coord[collided.chain_id == "B"] += ca_a - ca_b
    clash = interface_geometry(collided, "B", "A", **kwargs)
    moved = atoms.copy()
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]], dtype=np.float32)
    moved.coord = moved.coord @ rotation + [10, -7, 4]
    rigid = interface_geometry(moved, "B", "A", **kwargs)
    invariant = all(
        native[k] == rigid[k] for k in ["contact_residue_pair_count", "clash_atom_pair_count"]
    )
    passed = (
        native["contact_residue_pair_count"] > 0
        and negative["contact_residue_pair_count"] == 0
        and negative["clash_atom_pair_count"] == 0
        and clash["clash_atom_pair_count"] > 0
        and invariant
    )
    if not passed:
        raise ValueError("experimental-geometry control checks failed")
    return {
        "source": {"path": str(source.resolve()), "sha256": sha256_file(source)},
        "source_page": "https://www.rcsb.org/structure/4ZQK",
        "scope": "geometry implementation controls; not ESMFold2 calibration "
        "or experimental negatives",
        "passed": passed,
        "native_observed_complex": native,
        "binder_translated_1000_angstrom": negative,
        "binder_ca_forced_overlap": clash,
        "rigid_transform_invariant": invariant,
    }


def write_interface_feedback(
    jobs: list[Path],
    *,
    artifacts: Path = Path("artifacts/interface-feedback"),
    control_structure: Path | None = None,
) -> Path:
    if not jobs or len({x.resolve() for x in jobs}) != len(jobs):
        raise ValueError("supply one or more distinct completed complex jobs")
    destination = artifacts.resolve() / timestamp_id()
    destination.mkdir(parents=True)
    shutil.copyfile(feedback_policy_path(), destination / "protocol.json")
    policy = read_json(destination / "protocol.json")
    started = time.monotonic()
    observations, inputs, seen = [], [], set()
    generation_hash, model_policy = None, None
    write_json(destination / "status.json", {"status": "running"})
    write_json(
        destination / "request.json",
        {
            "jobs": [str(x.resolve()) for x in jobs],
            "control_structure": str(control_structure.resolve()) if control_structure else None,
            "policy_sha256": sha256_file(destination / "protocol.json"),
        },
    )
    try:
        for job in [x.resolve() for x in jobs]:
            prepared = load_complex_job(job)
            if generation_hash is not None and (
                prepared["generation_manifest_sha256"] != generation_hash
                or prepared["policy"] != model_policy
            ):
                raise ValueError("feedback batches must share a generation and model protocol")
            generation_hash, model_policy = (
                prepared["generation_manifest_sha256"],
                prepared["policy"],
            )
            generation = read_json(Path(prepared["generation_run"]) / "manifest.json")
            spec = InteractionDesignSpec.model_validate(generation["task"])
            if canonical_sha256(spec.model_dump(mode="json")) != generation["task_sha256"]:
                raise ValueError("generation task checksum mismatch")
            source_record = generation["inputs"]["reference_structure"]
            if (
                not source_record
                or sha256_file(Path(source_record["path"])) != source_record["sha256"]
            ):
                raise ValueError("original target reference is missing or changed")
            original = read_atoms(Path(source_record["path"]), author=True)
            output = completed_complex_output(job)
            validated = validate_complex_output(job, output)
            by_id = {x["candidate_id"]: x for x in validated}
            inputs.append(
                {
                    "job": str(job),
                    "manifest_sha256": sha256_file(job / "manifest.json"),
                    "receipt_sha256": sha256_file(job / "completed.json"),
                    "reference": source_record,
                }
            )
            for record in prepared["requests"]:
                request = read_json(checked_path(job, record))
                candidate_id = request["candidate_id"]
                if candidate_id in seen:
                    raise ValueError("overlapping feedback batches would duplicate a candidate")
                seen.add(candidate_id)
                metrics = by_id[candidate_id]
                mapping = map_target_residues(spec, original, request["chains"][1]["sequence"])
                positions = [x["target_position"] for x in mapping["hotspots"]]
                kwargs = {
                    "hotspot_positions": positions,
                    "contact_distance": policy["contact_distance_angstrom"],
                    "clash_distance": policy["clash_distance_angstrom"],
                }
                predicted_path = job / metrics["structure"]
                predicted = interface_geometry(read_atoms(predicted_path), "A", "B", **kwargs)
                generated = interface_geometry(
                    read_atoms(Path(request["reference"]["path"])),
                    request["chains"][0]["generation_chain"],
                    request["chains"][1]["generation_chain"],
                    **kwargs,
                )
                gen_contacts = {
                    (x["binder_position"], x["target_position"]) for x in generated["contacts"]
                }
                pred_contacts = {
                    (x["binder_position"], x["target_position"]) for x in predicted["contacts"]
                }
                overlap = len(gen_contacts & pred_contacts)
                for geom in [predicted, generated]:
                    for row, hotspot in zip(geom["hotspots"], mapping["hotspots"], strict=True):
                        row.update(
                            source_chain=hotspot["source_chain"],
                            source_residue=hotspot["source_residue"],
                        )
                observations.append(
                    {
                        "schema_version": "1",
                        "candidate_id": candidate_id,
                        "generation_seed": metrics["generation_seed"],
                        "backend": "esmfold2",
                        "model_policy": model_policy,
                        "geometry_policy_sha256": sha256_file(destination / "protocol.json"),
                        "sources": {
                            "job": str(job),
                            "request_sha256": record["sha256"],
                            "prediction": {
                                "path": str(predicted_path),
                                "sha256": sha256_file(predicted_path),
                            },
                            "generated_structure": request["reference"],
                        },
                        "target_mapping": mapping,
                        "confidence": {
                            k: metrics[k]
                            for k in [
                                "iptm",
                                "ptm",
                                "binder_plddt_residue",
                                "target_plddt_residue",
                                "pae_a_to_b_angstrom",
                                "pae_b_to_a_angstrom",
                            ]
                        },
                        "geometry": {"predicted": predicted, "generated": generated},
                        "design_consistency": {
                            "binder_ca_rmsd_angstrom": metrics["binder_ca_rmsd_angstrom"],
                            "binder_rmsd_after_target_alignment_angstrom": metrics[
                                "binder_rmsd_after_target_alignment_angstrom"
                            ],
                            "retained_generated_contact_pairs": overlap,
                            "generated_contact_retention": overlap / len(gen_contacts)
                            if gen_contacts
                            else None,
                            "contact_pair_jaccard": overlap / len(gen_contacts | pred_contacts)
                            if gen_contacts | pred_contacts
                            else None,
                        },
                        "flags": geometry_flags(predicted),
                        "acceptance": None,
                        "binding_validated": False,
                        "prediction_inference_seconds": metrics["inference_seconds"],
                    }
                )
        observations.sort(key=lambda x: x["generation_seed"])
        controls = (
            geometry_controls(control_structure.resolve(), policy) if control_structure else None
        )
        if control_structure:
            shutil.copyfile(control_structure, destination / "control-4ZQK.cif")
        write_json(
            destination / "observations.json",
            {
                "schema_version": "1",
                "analysis_software": {
                    "python": platform.python_version(),
                    **{
                        name: importlib.metadata.version(name)
                        for name in ["numpy", "biotite", "interaction-design-mvp"]
                    },
                },
                "policy": policy,
                "model_policy": model_policy,
                "generation_manifest_sha256": generation_hash,
                "inputs": inputs,
                "candidate_count": len(observations),
                "observations": observations,
                "controls": controls,
                "acceptance": None,
            },
        )
        flat = [
            {
                "candidate_id": x["candidate_id"],
                "seed": x["generation_seed"],
                "iptm": x["confidence"]["iptm"],
                "contact_pairs": x["geometry"]["predicted"]["contact_residue_pair_count"],
                "hotspots_contacted": x["geometry"]["predicted"]["hotspots_contacted"],
                "hotspot_count": x["geometry"]["predicted"]["hotspot_count"],
                "clash_atom_pairs": x["geometry"]["predicted"]["clash_atom_pair_count"],
                "clash_residue_pairs": x["geometry"]["predicted"]["clash_residue_pair_count"],
                "generated_contact_retention": x["design_consistency"][
                    "generated_contact_retention"
                ],
                "flags": ";".join(x["flags"]),
                "acceptance": None,
            }
            for x in observations
        ]
        with (destination / "candidates.csv").open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(flat[0]))
            writer.writeheader()
            writer.writerows(flat)
        lines = [
            "# Protein interface feedback",
            "",
            "Heavy-atom contacts <5 Å; cross-chain clashes <2 Å. Acceptance remains unset.",
            "Hotspots are mapped from the original task/reference to target sequence positions.",
            "",
            "| Seed | ipTM | Contact residue pairs | Hotspots contacted | "
            "Clash atom pairs | Flags |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for row in flat:
            lines.append(
                f"| {row['seed']} | {row['iptm']:.3f} | {row['contact_pairs']} | "
                f"{row['hotspots_contacted']}/{row['hotspot_count']} | "
                f"{row['clash_atom_pairs']} | {row['flags']} |"
            )
        lines += [
            "",
            "These are diagnostics of resolved coordinates, not binding or energy scores.",
            "Contacting hotspots does not establish coverage of the entire intended epitope.",
            "Geometry controls do not calibrate ESMFold2 confidence "
            "or a biological pass threshold.",
            "",
        ]
        (destination / "report.md").write_text("\n".join(lines))
        write_json(
            destination / "status.json",
            {"status": "completed", "analysis_seconds": time.monotonic() - started},
        )
    except BaseException as error:
        write_json(
            destination / "status.json",
            {
                "status": "failed",
                "error": str(error),
                "analysis_seconds": time.monotonic() - started,
            },
        )
        raise
    finally:
        for name in ["feedback.py", "interface.py"]:
            shutil.copyfile(Path(__file__).with_name(name), destination / name)
        write_json(
            destination / "manifest.json",
            {
                "artifacts": [
                    file_record(p, destination)
                    for p in sorted(destination.iterdir())
                    if p.is_file() and p.name != "manifest.json"
                ]
            },
        )
    return destination

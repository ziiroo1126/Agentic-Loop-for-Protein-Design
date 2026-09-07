"""Validated, descriptive reports for a complete monomer batch."""

from __future__ import annotations

import csv
import math
from pathlib import Path

import biotite.structure as bs
import numpy as np
from biotite.structure.io.pdb import PDBFile

from interaction_design.assets import sha256_file
from interaction_design.evaluation.af3 import make_input
from interaction_design.evaluation.jobs import checked_path, file_record, read_json
from interaction_design.evaluation.monomer import completed_monomer_output, load_monomer_job
from interaction_design.outputs import PROTEIN_3_TO_1
from interaction_design.persistence import write_json


def aligned_rmsd(reference: np.ndarray, moving: np.ndarray) -> float:
    fitted, _ = bs.superimpose(reference, moving)
    return float(bs.rmsd(reference, fitted))


def select_representatives(rows: list[dict], positions: list[int]) -> list[dict]:
    ordered = sorted(rows, key=lambda row: (-row["mean_plddt_ca"], row["candidate_id"]))
    if any(index < 0 or index >= len(ordered) for index in positions):
        raise ValueError("representative positions exceed the candidate count")
    return [ordered[index] for index in positions]


def _validated_monomer_batch(job: Path) -> tuple:
    job = job.resolve()
    prepared = load_monomer_job(job)
    output = completed_monomer_output(job)
    status = read_json(output / "status.json")
    if status["requests_sha256"] != sha256_file(job / "manifest.json"):
        raise ValueError("batch execution used a different request manifest")
    by_id = {row["candidate_id"]: row for row in status["candidates"]}
    if len(by_id) != len(status["candidates"]) or set(by_id) != {
        row["candidate_id"] for row in prepared["requests"]
    }:
        raise ValueError("batch output candidate set mismatch")
    rows, references, requests = [], [], {}
    for record in prepared["requests"]:
        request = read_json(checked_path(job, record))
        candidate = by_id[record["candidate_id"]]
        folder = (output / candidate["output_dir"]).resolve()
        if not folder.is_relative_to(output):
            raise ValueError("candidate output path escapes batch directory")
        metrics, provenance = (
            read_json(folder / "metrics.json"),
            read_json(folder / "provenance.json"),
        )
        if (
            candidate["request_sha256"] != record["sha256"]
            or provenance["input_sha256"] != record["sha256"]
            or provenance["request"] != request
            or sha256_file(folder / "metrics.json") != candidate["metrics_sha256"]
        ):
            raise ValueError("candidate output identity does not match its request")
        sequence = request["sequence"]
        atoms = PDBFile.read(folder / "binder.pdb").get_structure(
            model=1, extra_fields=["b_factor"]
        )
        ca = atoms[atoms.atom_name == "CA"]
        if (
            len(set(ca.chain_id)) != 1
            or "".join(PROTEIN_3_TO_1[x] for x in ca.res_name) != sequence
        ):
            raise ValueError("predicted structure sequence mismatch")
        reference = np.asarray(request["reference_ca"], dtype=np.float64)
        with np.load(folder / "prediction.npz", allow_pickle=False) as raw:
            coordinates = raw["ca_coordinates"]
            if coordinates.shape != reference.shape or not np.isfinite(coordinates).all():
                raise ValueError("invalid prediction coordinates")
            if not np.allclose(ca.coord, coordinates, atol=0.00051, rtol=0):
                raise ValueError("PDB coordinates differ from raw prediction")
            if not np.allclose(ca.b_factor, metrics["plddt_ca_per_residue"], atol=0.0051, rtol=0):
                raise ValueError("PDB confidence scale differs from metrics")
            atom_mean = float(raw["plddt_atom37_0_to_1"][raw["atom37_exists"]].mean() * 100)
            if abs(atom_mean - metrics["mean_plddt_atom_weighted"]) > 1e-4:
                raise ValueError("atom-weighted confidence differs from raw prediction")
            rmsd = aligned_rmsd(reference, coordinates)
        if abs(rmsd - metrics["design_comparison"]["ca_rmsd_angstrom"]) > 1e-4:
            raise ValueError("independent alignment disagrees with reported RMSD")
        row = {
            "candidate_id": request["candidate_id"],
            "seed": request["odesign"]["seed"],
            "length": len(sequence),
            "sequence": sequence,
            "mean_plddt_ca": metrics["mean_plddt_ca"],
            "mean_plddt_atom_weighted": metrics["mean_plddt_atom_weighted"],
            "ptm": metrics["ptm"],
            "ca_rmsd_angstrom": rmsd,
            "inference_seconds": metrics["timing"]["inference_seconds"],
            "peak_torch_allocated_gib": metrics["gpu_memory"]["peak_torch_allocated_bytes"]
            / 1024**3,
            "prediction_pdb": str((folder / "binder.pdb").relative_to(job)),
            "threshold_pass": None,
        }
        for key in ["mean_plddt_ca", "mean_plddt_atom_weighted", "ptm", "inference_seconds"]:
            if not math.isfinite(row[key]) or row[key] < 0:
                raise ValueError("invalid metric")
        if row["mean_plddt_ca"] > 100 or row["ptm"] > 1:
            raise ValueError("confidence outside expected range")
        if abs(np.mean(metrics["plddt_ca_per_residue"]) - row["mean_plddt_ca"]) > 1e-4:
            raise ValueError("mean CA confidence differs from per-residue values")
        rows.append(row)
        references.append(reference)
        requests[row["candidate_id"]] = request
    return prepared, output, rows, references, requests, status


def read_monomer_batch(job: Path) -> list[dict]:
    """Independently validate completed monomers without rewriting sealed reports."""
    return _validated_monomer_batch(job.resolve())[2]


def report_monomer_batch(job: Path) -> Path:
    job = job.resolve()
    prepared, output, rows, references, requests, status = _validated_monomer_batch(job)
    n = len(rows)
    identity, backbone_rmsd = np.eye(n), np.zeros((n, n))
    if len({len(row["sequence"]) for row in rows}) != 1:
        raise ValueError("same-position diversity comparison requires equal lengths")
    for i in range(n):
        for j in range(i):
            identity[i, j] = identity[j, i] = sum(
                a == b for a, b in zip(rows[i]["sequence"], rows[j]["sequence"], strict=True)
            ) / len(rows[i]["sequence"])
            backbone_rmsd[i, j] = backbone_rmsd[j, i] = aligned_rmsd(references[i], references[j])

    def distribution(values):
        values = np.asarray(values)
        return (
            {
                "min": float(values.min()),
                "median": float(np.median(values)),
                "max": float(values.max()),
            }
            if values.size
            else None
        )

    protocol = read_json(checked_path(job, prepared["protocol"])) if prepared["protocol"] else None
    positions = (
        protocol["analysis"]["representative_selection"]["zero_based_positions"]
        if protocol
        else sorted({0, (n - 1) // 2, n - 1})
    )
    representatives = select_representatives(rows, positions)
    report = {
        "scope": "complete monomer batch; descriptive development results, not binding validation",
        "candidate_count": n,
        "generation_run": prepared["generation_run"],
        "request_manifest_sha256": sha256_file(job / "manifest.json"),
        "batch_output_manifest_sha256": sha256_file(output / "manifest.json"),
        "protocol": protocol,
        "rows": rows,
        "summary": {
            key: distribution([row[key] for row in rows])
            for key in ["mean_plddt_ca", "ptm", "ca_rmsd_angstrom", "inference_seconds"]
        },
        "diversity": {
            "exact_unique_sequences": len({row["sequence"] for row in rows}),
            "pairwise_same_position_identity": distribution(identity[np.tril_indices(n, -1)]),
            "pairwise_design_ca_rmsd_angstrom": distribution(backbone_rmsd[np.tril_indices(n, -1)]),
        },
        "representatives": [row["candidate_id"] for row in representatives],
        "shared_model_load_seconds": status["shared_model_load_and_gpu_transfer_seconds"],
        "summed_inference_seconds": sum(row["inference_seconds"] for row in rows),
        "validation": (
            "all output hashes, request identities, PDB sequences/scales "
            "and independent RMSDs checked"
        ),
        "threshold_pass": None,
    }
    report_path = write_json(job / "report.json", report)
    with (job / "candidates.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (job / "candidates.fasta").write_text(
        "".join(f">{row['candidate_id']}\n{row['sequence']}\n" for row in rows)
    )
    np.savez_compressed(
        job / "diversity.npz",
        candidate_ids=np.array([r["candidate_id"] for r in rows]),
        sequence_identity=identity,
        design_ca_rmsd_angstrom=backbone_rmsd,
    )
    followup = job / "complex_followup"
    followup.mkdir(exist_ok=True)
    selected = []
    for index, row in enumerate(representatives):
        request = requests[row["candidate_id"]]
        contexts = request["context_sequences"]
        if len(contexts) != 1:
            continue
        native = make_input(
            f"pilot_representative{index}",
            request["sequence"],
            next(iter(contexts.values())),
            seed=1,
            msa_mode="search",
        )
        path = write_json(followup / f"representative{index}.json", native)
        selected.append(
            {
                "candidate_id": row["candidate_id"],
                "selection_rank_index": positions[index],
                "input": file_record(path, followup),
            }
        )
    write_json(
        followup / "selection.json",
        {
            "status": "prepared_only",
            "predictor_executed": False,
            "msa_policy": "target search; binder query-only; no generated geometry template",
            "selection_rule": "descending mean CA pLDDT, ties by candidate ID",
            "candidates": selected,
        },
    )
    lines = [
        "# ESMFold monomer batch",
        "",
        report["scope"],
        "",
        "| Seed | Mean CA pLDDT | pTM | CA RMSD (Å) | Inference (s) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for row in sorted(rows, key=lambda r: r["seed"]):
        lines.append(
            f"| {row['seed']} | {row['mean_plddt_ca']:.2f} | {row['ptm']:.3f} | "
            f"{row['ca_rmsd_angstrom']:.3f} | {row['inference_seconds']:.3f} |"
        )
    lines.extend(
        [
            "",
            "All candidates are included. No binding/pass threshold was applied.",
            "",
            "[Full metrics](report.json) · [CSV](candidates.csv) · [FASTA](candidates.fasta)",
        ]
    )
    (job / "report.md").write_text("\n".join(lines) + "\n")
    return report_path

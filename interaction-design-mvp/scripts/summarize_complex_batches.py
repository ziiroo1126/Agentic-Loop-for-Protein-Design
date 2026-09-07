"""Merge disjoint, validated complex batches covering one complete generation."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np

from interaction_design.assets import sha256_file
from interaction_design.evaluation.complex import (
    completed_complex_output,
    load_complex_job,
    validate_complex_output,
)
from interaction_design.evaluation.jobs import read_json
from interaction_design.persistence import write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("jobs", type=Path, nargs="+")
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows, jobs, policy, generation_hash = [], [], None, None
    expected = None
    for job in [x.resolve() for x in args.jobs]:
        manifest = load_complex_job(job)
        current_hash = manifest["generation_manifest_sha256"]
        if policy is not None and (policy != manifest["policy"] or current_hash != generation_hash):
            raise ValueError("cannot merge different protocols or generations")
        policy, generation_hash = manifest["policy"], current_hash
        generation = read_json(Path(manifest["generation_run"]) / "manifest.json")
        expected = set(generation["candidate_ids"])
        output = completed_complex_output(job)
        candidates = validate_complex_output(job, output)
        requests = {x["candidate_id"]: read_json(job / x["path"]) for x in manifest["requests"]}
        for candidate in candidates:
            candidate["structure"] = str(job / candidate["structure"])
            candidate["chains"] = requests[candidate["candidate_id"]]["chains"]
            candidate["job"] = str(job)
        rows.extend(candidates)
        elapsed = sum(
            read_json(p)["elapsed_seconds"] for p in (job / "executions").glob("*/execution.json")
        )
        jobs.append(
            {
                "path": str(job),
                "request_manifest_sha256": sha256_file(job / "manifest.json"),
                "completed_receipt_sha256": sha256_file(job / "completed.json"),
                "process_seconds_including_failures": elapsed,
                "provenance": read_json(output / "provenance.json"),
            }
        )
    if len({x["candidate_id"] for x in rows}) != len(rows):
        raise ValueError("batches overlap; duplicate predictions cannot be silently selected")
    if {x["candidate_id"] for x in rows} != expected:
        raise ValueError("batches do not cover the complete saved generation")
    signature_keys = [
        "packages",
        "folding_model",
        "language_model",
        "ccd",
        "folding_dtype",
        "esmc_dtype",
        "torch_cuda",
        "worker_sha256",
    ]
    signatures = [{k: x["provenance"][k] for k in signature_keys} for x in jobs]
    if any(x != signatures[0] for x in signatures[1:]):
        raise ValueError("batches used different model assets, software or precision")
    rows.sort(key=lambda x: (-x["iptm"], x["candidate_id"]))
    summary = {
        "backend": "esmfold2",
        "candidate_count": len(rows),
        "policy": policy,
        "generation_manifest_sha256": generation_hash,
        "jobs": jobs,
        "candidates": rows,
        "process_seconds_including_failures": sum(
            x["process_seconds_including_failures"] for x in jobs
        ),
        "inference_seconds_sum": sum(x["inference_seconds"] for x in rows),
        "peak_torch_allocated_gib": max(x["peak_torch_allocated_bytes"] for x in rows) / 1024**3,
        "iptm_median": float(np.median([x["iptm"] for x in rows])),
        "thresholds": None,
        "binding_validated": False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_json(args.output_dir / "summary.json", summary)
    keys = [
        "candidate_id",
        "generation_seed",
        "iptm",
        "ptm",
        "binder_plddt_residue",
        "target_plddt_residue",
        "pae_a_to_b_angstrom",
        "pae_b_to_a_angstrom",
        "binder_ca_rmsd_angstrom",
        "binder_rmsd_after_target_alignment_angstrom",
        "inference_seconds",
        "threshold_pass",
        "structure",
        "job",
    ]
    with (args.output_dir / "candidates.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(args.output_dir.resolve() / "summary.json")


if __name__ == "__main__":
    main()

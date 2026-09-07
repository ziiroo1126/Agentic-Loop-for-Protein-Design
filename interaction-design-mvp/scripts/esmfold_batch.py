"""Predict each saved monomer request once, sharing one offline ESMFold model."""

from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

from esmfold_smoke import checksum, load_model, predict, write_json


def read_requests(manifest):
    data = json.loads(manifest.read_text())
    requests = data["requests"]
    ids = [r["candidate_id"] for r in requests]
    if not requests or len(ids) != len(set(ids)):
        raise ValueError("Request manifest must contain unique candidate IDs")
    checked = []
    for row in requests:
        path = (manifest.parent / row["path"]).resolve(strict=True)
        if not path.is_relative_to(manifest.parent.resolve()):
            raise ValueError("Request file escapes the manifest directory")
        if checksum(path) != row["sha256"]:
            raise ValueError(f"Request checksum changed: {row['candidate_id']}")
        request = json.loads(path.read_text())
        sequence = request["sequence"]
        if request["candidate_id"] != row["candidate_id"]:
            raise ValueError("Request candidate ID differs from manifest")
        if not sequence or set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"):
            raise ValueError("Expected canonical monomer sequence")
        reference = request.get("reference")
        if reference and checksum(Path(reference["path"])) != reference["sha256"]:
            raise ValueError("Reference structure changed since request preparation")
        checked.append((row, path))
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=128)
    args = parser.parse_args()
    checked = read_requests(args.requests)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    rows = []
    started = time.perf_counter()
    status = {
        "status": "running",
        "requested": len(checked),
        "requests_sha256": checksum(args.requests),
        "batch_worker_sha256": checksum(Path(__file__)),
        "candidates": rows,
    }
    write_json(args.output_dir / "status.json", status)
    try:
        model, metadata, load_seconds = load_model(args)
        status["shared_model_load_and_gpu_transfer_seconds"] = load_seconds
        for index, (row, path) in enumerate(checked):
            destination = args.output_dir / f"candidate{index:04d}"
            destination.mkdir()
            candidate_args = SimpleNamespace(input=path, output_dir=destination)
            print(f"Candidate {index + 1}/{len(checked)}: {row['candidate_id']}", flush=True)
            metrics = predict(candidate_args, model, metadata, model_reused=index > 0)
            rows.append(
                {
                    "candidate_id": row["candidate_id"],
                    "request_sha256": row["sha256"],
                    "output_dir": destination.name,
                    "metrics_sha256": checksum(destination / "metrics.json"),
                    "status": "completed",
                    "inference_seconds": metrics["timing"]["inference_seconds"],
                }
            )
            write_json(args.output_dir / "status.json", status)
        status["status"] = "completed"
    except BaseException as error:
        status["status"] = "failed"
        status["failure"] = {
            "type": type(error).__name__,
            "message": str(error),
            "traceback": traceback.format_exc(),
            "next_request_index": len(rows),
        }
        raise
    finally:
        status["elapsed_seconds"] = time.perf_counter() - started
        write_json(args.output_dir / "status.json", status)
        write_json(
            args.output_dir / "manifest.json",
            {
                "status": status["status"],
                "stage": "esmfold_monomer_batch",
                "requested": len(checked),
                "completed": len(rows),
                "requests_sha256": checksum(args.requests),
                "artifacts": [
                    {
                        "path": str(p.relative_to(args.output_dir)),
                        "sha256": checksum(p),
                        "size": p.stat().st_size,
                    }
                    for p in sorted(args.output_dir.rglob("*"))
                    if p.is_file() and p != args.output_dir / "manifest.json"
                ],
            },
        )


if __name__ == "__main__":
    main()

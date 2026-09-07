"""Prepare and run offline monomer batches without changing PPI assessment records."""

from __future__ import annotations

import math
import os
import shutil
from importlib.resources import files
from pathlib import Path

import numpy as np
from biotite.structure.io.pdbx import CIFFile, get_structure

from interaction_design.assets import sha256_file
from interaction_design.conversion import spec_to_system
from interaction_design.evaluation.jobs import (
    checked_path,
    file_record,
    job_lock,
    read_json,
    timestamp_id,
)
from interaction_design.manifest import canonical_sha256
from interaction_design.outputs import PROTEIN_3_TO_1, _chain_id, parse_odesign_outputs
from interaction_design.persistence import write_json
from interaction_design.runtime.process import run_logged
from interaction_design.specs import InteractionDesignSpec


def prepare_monomer_batch(run_dir: Path, protocol: Path | None = None) -> Path:
    root = run_dir.resolve()
    generation = read_json(root / "manifest.json")
    if generation.get("stage") != "generation" or generation.get("status") != "generated":
        raise ValueError("monomer preparation requires a successful saved generation")
    spec = InteractionDesignSpec.model_validate(generation["task"])
    if canonical_sha256(spec.model_dump(mode="json")) != generation["task_sha256"]:
        raise ValueError("generation task checksum mismatch")
    binder_index = spec.binder_index
    binder = spec.molecules[binder_index]
    if binder.type != "protein" or binder.cyclic or binder.role != "design":
        raise ValueError("monomer check requires a non-cyclic protein binder")
    instances = parse_odesign_outputs(root / "output", spec_to_system(spec), spec)
    ids = [item.id for item in instances]
    if len(set(ids)) != len(ids) or set(ids) != set(generation["candidate_ids"]):
        raise ValueError("candidate set differs from generation manifest")
    records = {r["path"]: r for r in generation["artifacts"]}
    prepared = []
    for instance in instances:
        source = Path(instance.metadata["artifacts"]["structure"])
        record = records.get(str(source.relative_to(root)))
        if record is None:
            raise ValueError("candidate missing from generation artifact records")
        checked_path(root, record)
        atoms = get_structure(CIFFile.read(source), model=1, use_author_fields=False)
        ca = atoms[(atoms.chain_id == _chain_id(binder_index)) & (atoms.atom_name == "CA")]
        sequence = "".join(instance[binder_index].rep)
        keys = list(zip(ca.res_id.tolist(), ca.ins_code.tolist(), strict=True))
        if (
            "".join(PROTEIN_3_TO_1[x] for x in ca.res_name) != sequence
            or len(keys) != len(set(keys))
            or not np.isfinite(ca.coord).all()
        ):
            raise ValueError("reference C-alpha atoms do not match the binder sequence")
        prepared.append(
            {
                "candidate_id": instance.id,
                "sequence": sequence,
                "odesign": instance.metadata["odesign"],
                "generation_run": str(root),
                "generation_manifest_sha256": sha256_file(root / "manifest.json"),
                "reference": {
                    "path": str(source),
                    "sha256": record["sha256"],
                    "chain": _chain_id(binder_index),
                    "atom_selection": "all sequence-matched CA",
                },
                "reference_ca": ca.coord.astype(float).tolist(),
                "context_sequences": {
                    molecule.id: "".join(instance[index].rep)
                    for index, molecule in enumerate(spec.molecules)
                    if molecule.role == "context" and molecule.type == "protein"
                },
            }
        )
    policy = read_json(protocol) if protocol else None
    if policy:
        if len(prepared) != policy["candidate_count"]:
            raise ValueError("candidate count differs from frozen protocol")
        if sorted(p["odesign"]["seed"] for p in prepared) != sorted(policy["seeds"]):
            raise ValueError("candidate seeds differ from frozen protocol")
        if any(len(p["sequence"]) != policy["fixed"]["binder_length"] for p in prepared):
            raise ValueError("binder length differs from frozen protocol")
    job = root / "monomer_batches" / timestamp_id()
    (job / "requests").mkdir(parents=True)
    requests = []
    for index, request in enumerate(prepared):
        path = write_json(job / "requests" / f"candidate{index:04d}.json", request)
        requests.append({"candidate_id": request["candidate_id"], **file_record(path, job)})
    manifest = {
        "stage": "monomer_batch_preparation",
        "generation_run": str(root),
        "generation_manifest_sha256": sha256_file(root / "manifest.json"),
        "requests": requests,
        "protocol": None,
    }
    if protocol:
        shutil.copyfile(protocol, job / "protocol.json")
        manifest["protocol"] = file_record(job / "protocol.json", job)
    write_json(job / "manifest.json", manifest)
    write_json(job / "status.json", {"status": "prepared"})
    return job


def load_monomer_job(job: Path) -> dict:
    manifest = read_json(job / "manifest.json")
    if manifest.get("stage") != "monomer_batch_preparation":
        raise ValueError("not a prepared monomer batch")
    generation = Path(manifest["generation_run"]) / "manifest.json"
    if sha256_file(generation) != manifest["generation_manifest_sha256"]:
        raise ValueError("generation manifest changed")
    if manifest["protocol"]:
        checked_path(job, manifest["protocol"])
    for row in manifest["requests"]:
        request = read_json(checked_path(job, row))
        if request["candidate_id"] != row["candidate_id"]:
            raise ValueError("request ID mismatch")
        reference = request["reference"]
        if sha256_file(Path(reference["path"])) != reference["sha256"]:
            raise ValueError("original reference structure changed")
    return manifest


def _worker_source(name: str) -> Path:
    packaged = Path(str(files("interaction_design").joinpath("data", name)))
    return packaged if packaged.is_file() else Path(__file__).parents[3] / "scripts" / name


def completed_monomer_output(job: Path) -> Path:
    receipt = read_json(job / "completed.json")
    if receipt["request_manifest_sha256"] != sha256_file(job / "manifest.json"):
        raise ValueError("request manifest changed since execution")
    checked_path(job, receipt["execution"])
    manifest_path = checked_path(job, receipt["output_manifest"])
    for record in read_json(manifest_path)["artifacts"]:
        checked_path(manifest_path.parent, record)
    return manifest_path.parent


def run_monomer_batch(
    job: Path,
    *,
    python: Path,
    model_dir: Path,
    gpu: int = 0,
    timeout: float = 900,
) -> Path:
    job = job.resolve()
    with job_lock(job):
        manifest = load_monomer_job(job)
        if (job / "completed.json").exists():
            return completed_monomer_output(job)
        if gpu < 0 or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("GPU must be nonnegative and timeout positive and finite")
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError("ESMFold Python executable is unavailable")
        if not all((model_dir / name).is_file() for name in ["config.json", "pytorch_model.bin"]):
            raise ValueError("local ESMFold snapshot is incomplete")
        cpu_threads, chunk_size = 4, 128
        if manifest["protocol"]:
            protocol = read_json(checked_path(job, manifest["protocol"]))
            if model_dir.resolve().name != protocol["fixed"]["esmfold_snapshot"]:
                raise ValueError("ESMFold snapshot differs from frozen protocol")
            timeout = min(timeout, protocol["resources"]["esmfold_wall_timeout_seconds"])
            cpu_threads = protocol["resources"]["cpu_threads"]
            chunk_size = protocol["fixed"]["esmfold_chunk_size"]
        spent = 0.0
        for record_path in (job / "executions").glob("*/execution.json"):
            record = read_json(record_path)
            if record["status"] == "running":
                raise ValueError("inspect unfinished previous monomer process before retrying")
            spent += record["elapsed_seconds"]
        remaining = timeout - spent
        if remaining <= 0:
            raise ValueError("monomer process time budget exhausted")
        attempt = job / "executions" / timestamp_id()
        attempt.mkdir(parents=True)
        for name in ["esmfold_smoke.py", "esmfold_batch.py"]:
            shutil.copyfile(_worker_source(name), attempt / name)
        env = os.environ.copy()
        env.update(
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            HF_HUB_DISABLE_TELEMETRY="1",
            PYTHONDONTWRITEBYTECODE="1",
            CUDA_VISIBLE_DEVICES=str(gpu),
            OMP_NUM_THREADS=str(cpu_threads),
            MKL_NUM_THREADS=str(cpu_threads),
        )
        command = [
            str(python.resolve()),
            str(attempt / "esmfold_batch.py"),
            "--model-dir",
            str(model_dir.resolve()),
            "--requests",
            str(job / "manifest.json"),
            "--output-dir",
            str(attempt / "output"),
            "--cpu-threads",
            str(cpu_threads),
            "--chunk-size",
            str(chunk_size),
        ]
        write_json(job / "status.json", {"status": "running", "attempt": str(attempt)})
        try:
            run_logged(
                command,
                attempt,
                metadata={"stage": "esmfold_monomer_batch", "offline": True},
                timeout_seconds=remaining,
                env=env,
                label="ESMFold",
                log_prefix="esmfold",
            )
            output = attempt / "output"
            status = read_json(output / "status.json")
            if status["status"] != "completed" or len(status["candidates"]) != len(
                manifest["requests"]
            ):
                raise ValueError("ESMFold output does not cover the complete batch")
            write_json(
                job / "completed.json",
                {
                    "request_manifest_sha256": sha256_file(job / "manifest.json"),
                    "output_manifest": file_record(output / "manifest.json", job),
                    "execution": file_record(attempt / "execution.json", job),
                },
            )
            completed_monomer_output(job)
        except BaseException as error:
            write_json(job / "status.json", {"status": "failed", "error": str(error)})
            raise
        write_json(job / "status.json", {"status": "completed", "output": str(output)})
        return output

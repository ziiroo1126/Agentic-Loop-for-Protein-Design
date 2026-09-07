"""Immutable evaluation requests and validated receipts for saved generations."""

from __future__ import annotations

import csv
import fcntl
import json
import math
import shutil
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

from interaction_design.assets import sha256_file
from interaction_design.conversion import spec_to_system
from interaction_design.evaluation.af3 import make_input, validate_refold
from interaction_design.manifest import canonical_sha256
from interaction_design.outputs import parse_odesign_outputs
from interaction_design.persistence import write_json
from interaction_design.scoring.rosetta import extract_rosetta_metrics
from interaction_design.specs import InteractionDesignSpec
from interaction_design.workflow import evaluate_run


def timestamp_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def protocol_path() -> Path:
    packaged = Path(str(files("interaction_design").joinpath("data", "ppi.xml")))
    if packaged.is_file():
        return packaged
    return Path(__file__).parents[3] / "config" / "ppi.xml"


def read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return payload


def file_record(path: Path, root: Path) -> dict:
    return {
        "path": str(path.relative_to(root)),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
    }


def checked_path(root: Path, record: dict) -> Path:
    path = (root / record["path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("artifact path escapes its record directory")
    if not path.is_file() or sha256_file(path) != record["sha256"]:
        raise ValueError(f"artifact checksum mismatch: {record['path']}")
    return path


@contextmanager
def job_lock(job: Path):
    with (job / ".lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("this assessment is already running or being imported") from error
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def prepare_assessment(
    run_dir: str | Path,
    *,
    msa_mode: str = "search",
    target_data: Path | None = None,
    seed: int = 1,
    budget_seconds: float = 1800,
) -> Path:
    root = Path(run_dir).resolve()
    generation = read_json(root / "manifest.json")
    if generation.get("stage") != "generation" or generation.get("status") != "generated":
        raise ValueError("assessment preparation requires a successful saved generation")
    spec = InteractionDesignSpec.model_validate(generation["task"])
    if canonical_sha256(spec.model_dump(mode="json")) != generation["task_sha256"]:
        raise ValueError("generation task checksum mismatch")
    if len(spec.molecules) != 2 or any(m.type != "protein" or m.cyclic for m in spec.molecules):
        raise ValueError("the PPI assessment protocol requires two non-cyclic protein chains")
    binder_index = spec.binder_index
    if (
        spec.molecules[binder_index].role != "design"
        or spec.molecules[1 - binder_index].role != "context"
    ):
        raise ValueError("the PPI protocol requires one designed binder and one context target")
    if seed < 0 or seed >= 2**31 or not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("seed must be in [0, 2**31); budget must be finite and positive")
    if (msa_mode == "provided") != (target_data is not None):
        raise ValueError("provided MSA mode requires target_data; other modes do not accept it")
    target_payload = read_json(target_data) if target_data is not None else None
    instances = parse_odesign_outputs(root / "output", spec_to_system(spec), spec)
    by_id = {item.id: item for item in instances}
    if len(by_id) != len(instances) or set(by_id) != set(generation["candidate_ids"]):
        raise ValueError("candidate set differs from the saved generation")
    artifacts = {record["path"]: record for record in generation["artifacts"]}
    prepared = []
    for index, candidate_id in enumerate(generation["candidate_ids"]):
        instance = by_id[candidate_id]
        source = Path(instance.metadata["artifacts"]["structure"])
        relative = str(source.relative_to(root))
        if relative not in artifacts:
            raise ValueError("candidate structure absent from generation manifest")
        checked_path(root, artifacts[relative])
        binder = "".join(instance[binder_index].rep)
        target = "".join(instance[1 - binder_index].rep)
        key = f"candidate{index:04d}"
        native = make_input(
            key, binder, target, seed=seed, msa_mode=msa_mode, target_data=target_payload
        )
        prepared.append((candidate_id, key, artifacts[relative], native))
    job = root / "assessments" / timestamp_id()
    (job / "inputs").mkdir(parents=True)
    shutil.copyfile(protocol_path(), job / "ppi.xml")
    candidates = []
    for candidate_id, key, source, native in prepared:
        path = write_json(job / "inputs" / f"{key}.json", native)
        candidates.append(
            {
                "id": candidate_id,
                "key": key,
                "source_structure": source,
                "input": file_record(path, job),
            }
        )
    inputs = [file_record(job / "ppi.xml", job)]
    if target_data is not None:
        shutil.copyfile(target_data, job / "target_data.json")
        inputs.append(file_record(job / "target_data.json", job))
    write_json(
        job / "manifest.json",
        {
            "schema_version": "1",
            "stage": "assessment",
            "job_id": job.name,
            "generation_manifest_sha256": sha256_file(root / "manifest.json"),
            "generation_executor": generation["executor"]["name"],
            "protocol": {
                "name": "af3-refold-pyrosetta-ppi",
                "msa_mode": msa_mode,
                "seed": seed,
                "diffusion_samples": 1,
                "budget_seconds": budget_seconds,
                "budget_scope": "local subprocess wall seconds, including failed attempts",
                "binder_chain": "A",
                "target_chain": "B",
                "original_binder_index": binder_index,
                "rosetta_xml_sha256": sha256_file(job / "ppi.xml"),
            },
            "candidates": candidates,
            "inputs": inputs,
        },
    )
    write_json(job / "status.json", {"status": "prepared"})
    return job


def load_job(job: Path) -> dict:
    payload = read_json(job / "manifest.json")
    if payload.get("stage") != "assessment":
        raise ValueError("not an assessment directory")
    root = job.parents[1]
    if sha256_file(root / "manifest.json") != payload["generation_manifest_sha256"]:
        raise ValueError("generation manifest changed after assessment preparation")
    for record in payload["inputs"]:
        checked_path(job, record)
    for candidate in payload["candidates"]:
        checked_path(job, candidate["input"])
        checked_path(root, candidate["source_structure"])
    return payload


def receipt(job: Path, candidate: dict, stage: str) -> dict | None:
    path = job / "results" / candidate["key"] / f"{stage}.json"
    if not path.exists():
        return None
    record = read_json(path)
    if record["job_manifest_sha256"] != sha256_file(job / "manifest.json"):
        raise ValueError("assessment request changed after evaluation")
    for artifact in record["artifacts"].values():
        checked_path(job, artifact)
    if record.get("execution"):
        checked_path(job, record["execution"])
    return record


def _candidate(payload: dict, candidate_id: str) -> dict:
    matches = [c for c in payload["candidates"] if c["id"] == candidate_id]
    if len(matches) != 1:
        raise ValueError(f"unknown candidate: {candidate_id}")
    return matches[0]


def save_receipt(
    job: Path, candidate: dict, stage: str, artifacts: dict, mode: str, execution: Path | None
) -> dict:
    record = {
        "candidate_id": candidate["id"],
        "mode": mode,
        "execution": file_record(execution, job) if execution else None,
        "job_manifest_sha256": sha256_file(job / "manifest.json"),
        "artifacts": {key: file_record(path, job) for key, path in artifacts.items()},
    }
    write_json(job / "results" / candidate["key"] / f"{stage}.json", record)
    return record


def accept_af3(
    job: Path, candidate: dict, output_dir: Path, *, mode: str, execution: Path | None = None
) -> dict:
    key = candidate["key"]
    matches = list(output_dir.rglob(f"{key}_summary_confidences.json"))
    if len(matches) != 1:
        raise ValueError(f"expected one top-ranked AF3 result for {key}, found {len(matches)}")
    summary = matches[0]
    model = summary.with_name(f"{key}_model.cif")
    data = summary.with_name(f"{key}_data.json")
    validate_refold(
        model, read_json(data), read_json(summary), read_json(checked_path(job, candidate["input"]))
    )
    rankings = [
        path
        for path in (
            summary.parent / f"{key}_ranking_scores.csv",
            summary.parent / "ranking_scores.csv",
        )
        if path.is_file()
    ]
    if len(rankings) != 1:
        raise ValueError("AF3 import requires one native ranking_scores.csv")
    with rankings[0].open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    seed = load_job(job)["protocol"]["seed"]
    if len(rows) != 1 or int(rows[0]["seed"]) != seed or int(rows[0]["sample"]) != 0:
        raise ValueError("AF3 sampling count/seed differs from the single-sample protocol")
    destination = job / "accepted" / candidate["key"] / "af3" / timestamp_id()
    destination.mkdir(parents=True)
    artifacts = {}
    sources = {
        "af3_summary": summary,
        "af3_structure": model,
        "af3_data": data,
        "af3_ranking": rankings[0],
    }
    terms = summary.parent / "TERMS_OF_USE.md"
    if terms.is_file():
        sources["af3_terms"] = terms
    for name, source in sources.items():
        target = destination / source.name
        shutil.copyfile(source, target)
        artifacts[name] = target
    return save_receipt(job, candidate, "af3", artifacts, mode, execution)


def accept_rosetta(
    job: Path, candidate: dict, metrics_file: Path, *, mode: str, execution: Path | None = None
) -> dict:
    af3 = receipt(job, candidate, "af3")
    if af3 is None:
        raise ValueError("Rosetta scoring requires an accepted AF3 refold")
    metrics = read_json(metrics_file)
    provenance = metrics.get("provenance", {})
    expected_hash = af3["artifacts"]["af3_structure"]["sha256"]
    protocol = load_job(job)["protocol"]
    if provenance.get("structure_sha256") != expected_hash:
        raise ValueError("Rosetta metrics do not match the accepted AF3 structure checksum")
    if (provenance.get("binder_chain"), provenance.get("target_chain")) != ("A", "B"):
        raise ValueError("Rosetta chain mapping must be binder A / target B")
    if provenance.get("protocol_sha256") != protocol["rosetta_xml_sha256"]:
        raise ValueError("Rosetta protocol checksum differs from the prepared protocol")
    if provenance.get("seed") != protocol["seed"]:
        raise ValueError("Rosetta random seed differs from the prepared protocol")
    values = extract_rosetta_metrics(metrics)
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("Rosetta metrics must be finite")
    destination = job / "accepted" / candidate["key"] / "rosetta" / timestamp_id()
    destination.mkdir(parents=True)
    target = destination / "metrics.json"
    shutil.copyfile(metrics_file, target)
    return save_receipt(job, candidate, "rosetta", {"rosetta_metrics": target}, mode, execution)


def import_result(job_dir: str | Path, candidate_id: str, stage: str, source: Path) -> dict:
    job = Path(job_dir).resolve()
    with job_lock(job):
        candidate = _candidate(load_job(job), candidate_id)
        if receipt(job, candidate, stage) is not None:
            raise ValueError(
                f"{stage} result already accepted; prepare a new assessment to replace it"
            )
        if stage == "af3":
            record = accept_af3(job, candidate, source, mode="external-import")
        elif stage == "rosetta":
            record = accept_rosetta(job, candidate, source, mode="external-import")
        else:
            raise ValueError("stage must be af3 or rosetta")
        write_json(job / "status.json", {"status": "results_imported", "last_stage": stage})
        return record


def report_assessment(job_dir: str | Path):
    job = Path(job_dir).resolve()
    with job_lock(job):
        payload = load_job(job)
        outputs = {}
        origins = {}
        for candidate in payload["candidates"]:
            merged = {}
            origins[candidate["id"]] = {}
            for stage in ("af3", "rosetta"):
                result = receipt(job, candidate, stage)
                if result is None:
                    raise ValueError(f"missing {stage} result for {candidate['id']}")
                origins[candidate["id"]][stage] = result["mode"]
                merged.update(
                    {
                        name: str(checked_path(job, artifact))
                        for name, artifact in result["artifacts"].items()
                    }
                )
            outputs[candidate["id"]] = merged
        result = evaluate_run(
            job.parents[1],
            evaluator_outputs=outputs,
            af3_binder_index=0,
            evaluation_provenance={
                "assessment_manifest": str(job / "manifest.json"),
                "assessment_manifest_sha256": sha256_file(job / "manifest.json"),
                "result_origins": origins,
                "protocol": payload["protocol"],
            },
        )
        write_json(job / "status.json", {"status": "completed", "report": str(result.report_json)})
        return result

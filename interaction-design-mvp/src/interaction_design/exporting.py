"""Portable, diagnostic-only exports of completed candidate screening sessions.

Original records remain immutable. JSON is projected into portable records; original
file hashes and hashes of the exported files are deliberately recorded separately.
"""

from __future__ import annotations

import csv
import re
import shutil
import tempfile
from pathlib import Path

from interaction_design import screening
from interaction_design.assets import sha256_file
from interaction_design.campaign_tools import load_feedback
from interaction_design.evaluation.complex import (
    completed_complex_output,
    load_complex_job,
    validate_complex_output,
)
from interaction_design.evaluation.jobs import checked_path, file_record, read_json
from interaction_design.evaluation.monomer import completed_monomer_output, load_monomer_job
from interaction_design.evaluation.monomer_report import read_monomer_batch
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.selection import POST_FIELDS, PRE_FIELDS, post_features
from interaction_design.selection_catalogue import build_catalogue
from interaction_design.structure_viewer import render_structure_viewer


def _completed(session: Path) -> tuple[dict, dict, list[dict], list[dict]]:
    if not (session / "completed.json").is_file() or read_json(session / "status.json") != {
        "status": "completed"
    }:
        raise ValueError("export requires a completed screening session")
    manifest, config, catalogue = screening._load(session)
    if manifest["mode"] not in {"saved_complex_replay", "local_complex"}:
        raise ValueError("unsupported screening execution mode")
    observed, intents, spent, pending = screening._history(session, manifest, config, catalogue)
    if pending or not intents or intents[-1]["decision"]["action"] != "stop":
        raise ValueError("export requires a completed screening stop decision")
    expected = {
        "status": "completed",
        "mode": manifest["mode"],
        "strategy": config.strategy,
        "candidate_count": len(catalogue["pool"]),
        "evaluated_count": len(observed),
        "selection_order": [r["candidate_id"] for r in observed],
        "observations": [
            {"candidate_id": r["candidate_id"], "post": post_features(r)} for r in observed
        ],
        "decisions": intents,
        "stop_reason": intents[-1]["decision"]["reason"],
        "new_model_inference": manifest["mode"] == "local_complex" and bool(observed),
        "tool_wall_seconds": spent,
        "host_token_usage": None,
        "actor_metadata_verified": False,
        "acceptance": None,
        "binding_validated": False,
        "scope": "development selection trace; no held-out policy comparison or binding labels",
    }
    records = read_json(session / "completed.json")["artifacts"]
    paths = [r["path"] for r in records]
    required = {"report.json"} | {
        f"steps/{index:04d}/receipt.json" for index in range(len(intents))
    }
    if len(paths) != len(set(paths)) or set(paths) != required:
        raise ValueError("screening completion lacks exact report and receipt coverage")
    for record in records:
        checked_path(session, record)
    if read_json(session / "report.json") != expected:
        raise ValueError("completion report differs from recorded history")
    return manifest, catalogue, observed, intents


class _Bundle:
    def __init__(self, root: Path):
        self.root = root
        self.sources: dict[Path, dict] = {}
        self.paths: dict[str, str] = {}

    def source(self, path: Path, expected: str | None = None) -> Path:
        path = path.resolve()
        digest = sha256_file(path)
        if expected is not None and digest != expected:
            raise ValueError("export source checksum mismatch")
        if path in self.sources and self.sources[path]["sha256"] != digest:
            raise ValueError("export source changed during export")
        self.sources.setdefault(
            path,
            {
                "source_id": f"source{len(self.sources):05d}",
                "sha256": digest,
                "size": path.stat().st_size,
                "name": path.name,
            },
        )
        return path

    def records(self, root: Path, records: list[dict]) -> None:
        names = [row["path"] for row in records]
        if len(names) != len(set(names)):
            raise ValueError("duplicate export source artifact records")
        for row in records:
            self.source(checked_path(root, row), row["sha256"])

    def pinned(self, path: Path) -> Path:
        path = path.resolve()
        if path not in self.sources:
            raise ValueError("export artifact is absent from a validated source manifest")
        return self.source(path, self.sources[path]["sha256"])

    def copy(self, source: Path, relative: str) -> str:
        source = self.pinned(source)
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        if sha256_file(destination) != self.sources[source]["sha256"]:
            raise ValueError("export source changed while copying")
        self.paths[str(source)] = relative
        self.sources[source]["export_path"] = relative
        return relative

    def portable(self, value):
        if isinstance(value, dict):
            return {str(self.portable(key)): self.portable(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.portable(item) for item in value]
        if isinstance(value, str):
            if value in self.paths:
                return self.paths[value]
            # Runtime commands can embed paths in key=value arguments or prose.
            # URL slashes are excluded; original bytes remain identified by source hashes.
            return re.sub(r"(?<![\w:/])/(?:[^\s\"'<>|]+)", "[external path omitted]", value)
        return value

    def json(self, relative: str, payload) -> str:
        write_json(self.root / relative, self.portable(payload))
        return relative


def _monomers(bundle: _Bundle, catalogue: dict) -> tuple[Path, dict, dict, dict]:
    for source in catalogue["sources"]:
        bundle.source(Path(source["path"]), source["sha256"])
    generation_root = Path(catalogue["generation_run"]).resolve()
    generation = read_json(bundle.pinned(generation_root / "manifest.json"))
    if canonical_sha256(generation["task"]) != generation["task_sha256"]:
        raise ValueError("generation task checksum mismatch")
    jobs = [
        path.parent
        for path in bundle.sources
        if path.name == "manifest.json"
        and read_json(path).get("stage") == "monomer_batch_preparation"
    ]
    if len(jobs) != 1:
        raise ValueError("catalogue must identify exactly one monomer batch")
    job = jobs[0]
    if build_catalogue(job) != catalogue:
        raise ValueError("rebuilt candidate catalogue differs from the screening input")
    prepared = load_monomer_job(job)
    output = completed_monomer_output(job)
    bundle.records(output, read_json(bundle.pinned(output / "manifest.json"))["artifacts"])
    rows = {row["candidate_id"]: row for row in read_monomer_batch(job)}
    requests = {
        record["candidate_id"]: read_json(bundle.pinned(checked_path(job, record)))
        for record in prepared["requests"]
    }
    return job, generation, rows, requests


def _complexes(bundle: _Bundle, session: Path, manifest: dict, observed: list[dict]) -> dict:
    if not observed:
        return {}
    payloads = []

    def feedback_payload(folder: Path) -> dict:
        payload = load_feedback(folder)
        bundle.source(folder / "manifest.json")
        bundle.records(folder, read_json(folder / "manifest.json")["artifacts"])
        return payload

    if manifest["mode"] == "saved_complex_replay":
        replay = read_json(session / "replay.json")
        matches = [
            row for row in manifest["sources"] if Path(row["path"]).name == "observations.json"
        ]
        if len(matches) != 1:
            raise ValueError("screening replay must identify one saved feedback source")
        source = bundle.source(Path(matches[0]["path"]), matches[0]["sha256"])
        payload = feedback_payload(source.parent)
        if payload != replay:
            raise ValueError("saved replay differs from its validated feedback source")
        payloads.append(payload)
    else:
        for step in sorted((session / "steps").iterdir()):
            if not (step / "result.json").exists():
                continue
            feedback = read_json(step / "result.json")["feedback"]
            folder = Path(feedback["path"])
            bundle.source(folder / "observations.json", feedback["observations_sha256"])
            payloads.append(feedback_payload(folder))
    inputs, feedback_rows = {}, {}
    for payload in payloads:
        for row in payload["inputs"]:
            job = Path(row["job"]).resolve()
            if job in inputs and inputs[job] != row:
                raise ValueError("conflicting complex feedback source identities")
            inputs[job] = row
        for row in payload["observations"]:
            if row["candidate_id"] in feedback_rows:
                raise ValueError("duplicate complex feedback candidates")
            feedback_rows[row["candidate_id"]] = row
    jobs, results = {}, {}
    for observation in observed:
        key = observation["candidate_id"]
        if feedback_rows.get(key) != observation:
            raise ValueError("selected observation differs from validated feedback")
        sources = observation["sources"]
        job = Path(sources["job"]).resolve()
        if job not in inputs:
            raise ValueError("selected complex job is missing from feedback provenance")
        if job not in jobs:
            record = inputs[job]
            bundle.source(job / "manifest.json", record["manifest_sha256"])
            bundle.source(job / "completed.json", record["receipt_sha256"])
            prepared = load_complex_job(job)
            output = completed_complex_output(job)
            bundle.source(output / "manifest.json")
            bundle.records(output, read_json(output / "manifest.json")["artifacts"])
            metrics = {row["candidate_id"]: row for row in validate_complex_output(job, output)}
            requests = {}
            for request in prepared["requests"]:
                path = checked_path(job, request)
                bundle.source(path, request["sha256"])
                requests[request["candidate_id"]] = (request, read_json(path))
            jobs[job] = (metrics, requests, output)
        metrics, requests, output = jobs[job]
        entry, request = requests[key]
        prediction = bundle.pinned(job / metrics[key]["structure"])
        if (
            entry["sha256"] != sources["request_sha256"]
            or prediction != Path(sources["prediction"]["path"]).resolve()
            or sha256_file(prediction) != sources["prediction"]["sha256"]
            or request["reference"] != sources["generated_structure"]
            or any(metrics[key][name] != value for name, value in observation["confidence"].items())
        ):
            raise ValueError("selected complex prediction identity or confidence mismatch")
        results[key] = (prediction, metrics[key], request, read_json(output / "provenance.json"))
    return results


def _write_bundle(
    bundle: _Bundle,
    session: Path,
    manifest: dict,
    catalogue: dict,
    observed: list[dict],
    intents: list[dict],
) -> None:
    job, generation, monomers, requests = _monomers(bundle, catalogue)
    complexes = _complexes(bundle, session, manifest, observed)
    by_id = {row["candidate_id"]: row for row in observed}
    reference = generation["inputs"]["reference_structure"]
    bundle.copy(
        bundle.pinned(Path(reference["path"])), "reference" + Path(reference["path"]).suffix
    )
    rows = []
    for index, candidate in enumerate(catalogue["pool"]):
        key = candidate["candidate_id"]
        if not key or any(ord(char) < 32 for char in key):
            raise ValueError("invalid candidate identifier for FASTA export")
        base = f"candidates/candidate{index:04d}"
        request, monomer = requests[key], monomers[key]
        generated = Path(request["reference"]["path"])
        pdb = bundle.pinned(job / monomer["prediction_pdb"])
        paths = {
            "generated_structure": bundle.copy(generated, f"{base}/generated.cif"),
            "monomer_structure": bundle.copy(pdb, f"{base}/monomer.pdb"),
            "predicted_complex": None,
        }
        metadata = {"odesign": request["odesign"], "artifacts": dict(paths)}
        for source in sorted(bundle.sources):
            if source.parent == generated.parent and source.name.startswith(generated.stem + "."):
                if source == generated:
                    continue
                suffix = source.name[len(generated.stem) :]
                relative = f"{base}/generation{suffix}"
                if source.suffix == ".json":
                    bundle.json(relative, read_json(source))
                    bundle.sources[source]["export_path"] = relative
                else:
                    bundle.copy(source, relative)
                metadata["artifacts"][suffix.lstrip(".")] = relative
        bundle.json(
            f"{base}/monomer_metrics.json", read_json(bundle.pinned(pdb.parent / "metrics.json"))
        )
        bundle.json(f"{base}/monomer_request.json", request)
        bundle.json(
            f"{base}/monomer_provenance.json",
            read_json(bundle.pinned(pdb.parent / "provenance.json")),
        )
        bundle.copy(pdb.parent / "prediction.npz", f"{base}/monomer_prediction.npz")
        if key in complexes:
            prediction, metrics, complex_request, provenance = complexes[key]
            paths["predicted_complex"] = bundle.copy(prediction, f"{base}/complex.cif")
            bundle.copy(prediction.parent / "confidence.npz", f"{base}/complex_confidence.npz")
            metrics = {**metrics, "structure": paths["predicted_complex"]}
            bundle.json(f"{base}/complex_metrics.json", metrics)
            bundle.json(f"{base}/complex_request.json", complex_request)
            bundle.json(f"{base}/complex_provenance.json", provenance)
            bundle.json(f"{base}/observation.json", by_id[key])
        row = {
            **candidate,
            "generation_status": "generated",
            "monomer_status": "completed",
            "selected_for_complex": key in by_id,
            "complex_evaluation_status": "evaluated" if key in by_id else "unevaluated",
            "post": post_features(by_id[key]) if key in by_id else None,
            "acceptance": None,
            "binding_validated": False,
            "artifacts": paths,
            "generation_metadata": metadata,
        }
        bundle.json(f"{base}/candidate.json", row)
        rows.append(row)
    bundle.json(
        "candidates.json", {"candidates": rows, "acceptance": None, "binding_validated": False}
    )
    bundle.json(
        "metrics.json", {"candidates": rows, "acceptance": None, "binding_validated": False}
    )
    for name, selected in [
        ("all", rows),
        ("evaluated", [row for row in rows if row["selected_for_complex"]]),
        ("selected", [row for row in rows if row["selected_for_complex"]]),
    ]:
        (bundle.root / f"{name}.fasta").write_text(
            "".join(f">{row['candidate_id']}\n{row['sequence']}\n" for row in selected),
            encoding="utf-8",
        )
    with (bundle.root / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = [
            "candidate_id",
            "seed",
            "length",
            "complex_evaluation_status",
            *PRE_FIELDS,
            *POST_FIELDS,
            "acceptance",
            "binding_validated",
        ]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    **{key: row[key] for key in fields[:4]},
                    **row["pre"],
                    **(row["post"] or {}),
                    "acceptance": "null",
                    "binding_validated": "false",
                }
            )
    report = read_json(session / "report.json")
    bundle.json(
        "report.json",
        {
            **report,
            "candidates": rows,
            "selection_meaning": "selected for complex evaluation; no binding acceptance",
        },
    )
    trace = []
    for index, intent in enumerate(intents):
        step = session / "steps" / f"{index:04d}"
        trace.append(
            {
                "step": index,
                "request": read_json(step / "request.json"),
                "submission": intent,
                "tool_wall_seconds": read_json(step / "receipt.json")["tool_wall_seconds"],
                "receipt_sha256": sha256_file(step / "receipt.json"),
            }
        )
    bundle.json("decision_trace.json", {"steps": trace, "actor_metadata_verified": False})
    bundle.json("task.json", generation["task"])
    monomer_protocol = read_json(job / "manifest.json")["protocol"]
    bundle.json(
        "protocol.json",
        {
            "selection": read_json(session / "configuration.json"),
            "monomer": read_json(bundle.pinned(checked_path(job, monomer_protocol)))
            if monomer_protocol is not None
            else None,
            "complex_model": catalogue["model_policy"],
            "interface_geometry": catalogue["geometry_policy"],
            "acceptance": None,
            "binding_validated": False,
        },
    )
    bundle.json(
        "generation.json", {key: value for key, value in generation.items() if key != "artifacts"}
    )
    for name in [
        "manifest.json",
        "completed.json",
        "report.json",
        "configuration.json",
        "catalogue.json",
        "progress.json",
        "status.json",
        *screening.SOURCE_FILES,
    ]:
        bundle.source(session / name)
    for index in range(len(intents)):
        step = session / "steps" / f"{index:04d}"
        for name in ["request.json", "submission.json", "receipt.json", "result.json"]:
            if (step / name).is_file():
                bundle.source(step / name)
    bundle.source(Path(__file__))
    bundle.json(
        "provenance.json",
        {
            "session_manifest_sha256": sha256_file(session / "manifest.json"),
            "session_completion_sha256": sha256_file(session / "completed.json"),
            "generation_manifest_sha256": sha256_file(
                Path(catalogue["generation_run"]) / "manifest.json"
            ),
            "original_task_sha256": generation["task_sha256"],
            "portable_task_sha256": canonical_sha256(read_json(bundle.root / "task.json")),
            "protocol_sha256": sha256_file(bundle.root / "protocol.json"),
            "sources": list(bundle.sources.values()),
            "path_policy": "All exported paths are relative to the export root. "
            "External runtime paths are omitted. Original source hashes identify original bytes; "
            "export hashes identify portable projections.",
        },
    )
    lines = [
        "# Candidate screening export",
        "",
        f"Mode: {manifest['mode']}. Strategy: {report['strategy']}.",
        "",
        f"Generated candidates: {len(rows)}. Evaluated complexes: {len(observed)}.",
        "Selection means selection for complex evaluation. Acceptance is null and "
        "binding_validated is false for every candidate.",
        "Unselected candidates remain unevaluated; their complex metrics and structures "
        "are absent.",
        "Saved complex replay performs no new model inference; local_complex records "
        "completed model evaluations.",
        "",
        "| Candidate | Seed | Complex status | ipTM |",
        "| --- | ---: | --- | ---: |",
    ]
    for row in rows:
        iptm = str(row["post"]["iptm"]) if row["post"] is not None else "unevaluated"
        lines.append(
            f"| {row['candidate_id']} | {row['seed']} | "
            f"{row['complex_evaluation_status']} | {iptm} |"
        )
    lines.extend(
        [
            "",
            "[Offline 3D structure viewer](index.html)",
            "",
            "[Metrics CSV](metrics.csv) · [Full metrics](metrics.json) · [All FASTA](all.fasta) · "
            "[Evaluated FASTA](evaluated.fasta) · [Selected FASTA](selected.fasta)",
            "",
            "[Task](task.json) · [Protocol](protocol.json) · "
            "[Decision trace](decision_trace.json) · [Provenance](provenance.json)",
            "",
            "Selected and evaluated FASTA contain the same completed evaluations. "
            "No composite score or binding threshold is applied.",
            "Paths in JSON are relative to this export root. The manifest checksums cover "
            "every exported file except the manifest itself.",
        ]
    )
    (bundle.root / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_screening(session: Path, destination: Path) -> Path:
    """Validate and export a completed session into a fresh portable directory.

    No model is run, no saved session/report is rewritten, and an existing
    destination is never overwritten. Failure removes only the new staging tree.
    """
    session, destination = session.resolve(), destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("export destination already exists")
    if destination.resolve().is_relative_to(session):
        raise ValueError("export destination must be outside the screening session")
    manifest, catalogue, observed, intents = _completed(session)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        bundle = _Bundle(staging)
        _write_bundle(bundle, session, manifest, catalogue, observed, intents)
        (staging / "index.html").write_text(render_structure_viewer(staging), encoding="utf-8")
        # Recheck mutable inputs before publishing the completed bundle.
        _completed(session)
        for source, record in bundle.sources.items():
            bundle.source(source, record["sha256"])
        artifacts = [
            file_record(path, staging) for path in sorted(staging.rglob("*")) if path.is_file()
        ]
        write_json(
            staging / "manifest.json",
            {
                "schema_version": 1,
                "stage": "screening_export",
                "status": "completed",
                "mode": manifest["mode"],
                "candidate_count": len(catalogue["pool"]),
                "evaluated_count": len(observed),
                "acceptance": None,
                "binding_validated": False,
                "artifacts": artifacts,
            },
        )
        # mkdir is exclusive: even an empty destination created concurrently is preserved.
        destination.mkdir()
        try:
            # Publish completion last: a hard interruption must not leave a
            # completed manifest visible before its data files have arrived.
            for path in sorted(staging.iterdir(), key=lambda item: item.name == "manifest.json"):
                shutil.move(str(path), destination / path.name)
        except BaseException:
            shutil.rmtree(destination)
            raise
        return destination.resolve()
    finally:
        shutil.rmtree(staging)

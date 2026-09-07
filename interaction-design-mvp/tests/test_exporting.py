"""Synthetic export contracts: no model inference or molecular evidence is produced."""

from __future__ import annotations

import csv
import json
import shutil
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from interaction_design import exporting, screening
from interaction_design.assets import sha256_file
from interaction_design.evaluation.complex import default_policy
from interaction_design.evaluation.feedback import feedback_policy_path
from interaction_design.evaluation.jobs import checked_path, file_record, read_json
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.selection import SelectionConfig


def seal_output(output):
    write_json(
        output / "manifest.json",
        {
            "artifacts": [
                file_record(path, output)
                for path in sorted(output.rglob("*"))
                if path.is_file() and path != output / "manifest.json"
            ]
        },
    )


def complete_job(job, output):
    write_json(job / "execution.json", {"status": "completed", "synthetic": True})
    write_json(
        job / "completed.json",
        {
            "request_manifest_sha256": sha256_file(job / "manifest.json"),
            "execution": file_record(job / "execution.json", job),
            "output_manifest": file_record(output / "manifest.json", job),
        },
    )


@pytest.fixture
def synthetic_session(tmp_path, monkeypatch):
    """Only numerical model validation is stubbed; manifests/history use real validators.

    Structure bytes are deliberately labelled synthetic. Numerical reconstruction
    and molecular sequence/coordinate checks are covered by monomer/complex tests.
    """
    generation = tmp_path / "generation"
    monomer = generation / "monomer_batches/synthetic"
    complex_job = tmp_path / "complex"
    reference = tmp_path / "reference.pdb"
    reference.write_text("REMARK synthetic reference fixture\n")
    task = {"name": "synthetic-export", "reference_structure": str(reference)}
    pool, generated, mono_requests, mono_rows = [], [], [], []
    for index, sequence in enumerate(["AAAA", "CCCC"]):
        key = f"synthetic-{index}"
        source = generation / "output" / f"{key}.cif"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text(f"# Synthetic generated CIF {key}\n")
        write_json(source.with_suffix(".af3.json"), {"synthetic": True, "candidate_id": key})
        generated.extend([source, source.with_suffix(".af3.json")])
        pool.append(
            {
                "candidate_id": key,
                "seed": index,
                "sequence": sequence,
                "length": 4,
                "pre": {
                    "monomer_plddt": 80.0,
                    "monomer_design_rmsd": 1.0,
                    "generated_hotspot_coverage": 0.5,
                    "generated_clash_residue_pairs": 0.0,
                },
            }
        )
    write_json(
        generation / "manifest.json",
        {
            "stage": "generation",
            "status": "generated",
            "task": task,
            "task_sha256": canonical_sha256(task),
            "candidate_ids": [x["candidate_id"] for x in pool],
            "artifacts": [file_record(path, generation) for path in generated],
            "inputs": {
                "reference_structure": {"path": str(reference), "sha256": sha256_file(reference)}
            },
            "executor": {
                "name": "synthetic",
                "command": ["/runtime/python", "root=/runtime/model"],
                "metadata": {"model_revision": "synthetic-fixed-revision"},
            },
        },
    )
    for candidate in pool:
        key = candidate["candidate_id"]
        generated_path = generation / "output" / f"{key}.cif"
        request = {
            "candidate_id": key,
            "sequence": candidate["sequence"],
            "generation_run": str(generation),
            "odesign": {"seed": candidate["seed"], "backbone_index": 0, "sequence_index": 0},
            "reference": {"path": str(generated_path), "sha256": sha256_file(generated_path)},
        }
        path = write_json(monomer / "requests" / f"{key}.json", request)
        mono_requests.append({"candidate_id": key, **file_record(path, monomer)})
        folder = monomer / "output" / key
        write_json(folder / "metrics.json", {"synthetic": True, "mean_plddt_ca": 80.0})
        write_json(folder / "provenance.json", {"synthetic": True, "request": request})
        (folder / "binder.pdb").write_text(f"REMARK synthetic monomer PDB {key}\n")
        (folder / "prediction.npz").write_bytes(b"synthetic NPZ fixture; not numpy data")
        mono_rows.append({**candidate, "prediction_pdb": f"output/{key}/binder.pdb"})
    write_json(
        monomer / "manifest.json",
        {
            "stage": "monomer_batch_preparation",
            "generation_run": str(generation),
            "generation_manifest_sha256": sha256_file(generation / "manifest.json"),
            "requests": mono_requests,
            "protocol": None,
        },
    )
    seal_output(monomer / "output")
    complete_job(monomer, monomer / "output")
    policy, geometry = default_policy(), read_json(feedback_policy_path())
    source_paths = [generation / "manifest.json", reference, feedback_policy_path(), *generated]
    source_paths += [path for path in monomer.rglob("*") if path.is_file()]
    catalogue = {
        "generation_run": str(generation),
        "pool": pool,
        "model_policy": policy,
        "geometry_policy": geometry,
        "sources": [{"path": str(path), "sha256": sha256_file(path)} for path in source_paths],
    }
    monkeypatch.setattr(screening, "build_catalogue", lambda _: deepcopy(catalogue))
    monkeypatch.setattr(exporting, "build_catalogue", lambda _: deepcopy(catalogue))
    monkeypatch.setattr(exporting, "read_monomer_batch", lambda _: deepcopy(mono_rows))
    complex_requests, complex_metrics, observations = [], [], []
    for candidate, mono_request in zip(pool, mono_requests, strict=True):
        key = candidate["candidate_id"]
        request = read_json(monomer / mono_request["path"])
        path = write_json(complex_job / "requests" / f"{key}.json", request)
        entry = {"candidate_id": key, **file_record(path, complex_job)}
        complex_requests.append(entry)
        folder = complex_job / "output" / key
        write_json(folder / "metrics.json", {"synthetic": True, "iptm": 0.5})
        (folder / "complex.cif").write_text(f"# Synthetic predicted CIF {key}\n")
        (folder / "confidence.npz").write_bytes(b"synthetic confidence fixture; not numpy data")
        complex_metrics.append(
            {"candidate_id": key, "iptm": 0.5, "structure": f"output/{key}/complex.cif"}
        )
        observations.append(
            {
                "candidate_id": key,
                "generation_seed": candidate["seed"],
                "model_policy": policy,
                "geometry_policy_sha256": sha256_file(feedback_policy_path()),
                "confidence": {"iptm": 0.5},
                "geometry": {
                    "predicted": {
                        "hotspot_coverage": 0.5,
                        "hotspots_contacted": 1,
                        "hotspot_count": 2,
                        "clash_residue_pair_count": 0,
                    }
                },
                "design_consistency": {"binder_rmsd_after_target_alignment_angstrom": 1.0},
                "sources": {
                    "job": str(complex_job),
                    "request_sha256": entry["sha256"],
                    "prediction": {
                        "path": str(folder / "complex.cif"),
                        "sha256": sha256_file(folder / "complex.cif"),
                    },
                    "generated_structure": request["reference"],
                },
                "acceptance": None,
                "binding_validated": False,
                "synthetic": True,
            }
        )
    write_json(
        complex_job / "manifest.json",
        {
            "stage": "complex_batch_preparation",
            "backend": "esmfold2",
            "policy": policy,
            "generation_run": str(generation),
            "generation_manifest_sha256": sha256_file(generation / "manifest.json"),
            "requests": complex_requests,
        },
    )
    write_json(
        complex_job / "output/provenance.json",
        {"synthetic": True, "backend": "esmfold2", "policy": policy},
    )
    seal_output(complex_job / "output")
    complete_job(complex_job, complex_job / "output")
    monkeypatch.setattr(exporting, "validate_complex_output", lambda *_: deepcopy(complex_metrics))
    payload = {
        "candidate_count": len(pool),
        "observations": observations,
        "generation_manifest_sha256": sha256_file(generation / "manifest.json"),
        "model_policy": policy,
        "policy": geometry,
        "inputs": [
            {
                "job": str(complex_job),
                "manifest_sha256": sha256_file(complex_job / "manifest.json"),
                "receipt_sha256": sha256_file(complex_job / "completed.json"),
            }
        ],
    }
    feedback = tmp_path / "feedback"
    write_json(feedback / "observations.json", payload)
    write_json(feedback / "status.json", {"status": "completed"})
    seal_output(feedback)

    def prepare(*, live=False, completed=True, empty=False):
        if live:
            monkeypatch.setattr(screening, "validate_complex_runtime", lambda runtime, _: runtime)

            def evaluate(_, ids, *args, **kwargs):
                return {
                    "observations": [row for row in observations if row["candidate_id"] in ids],
                    "model_policy": policy,
                    "geometry_policy": geometry,
                    "mode": "local_complex",
                    "new_model_inference": True,
                    "feedback": {
                        "path": str(feedback),
                        "observations_sha256": sha256_file(feedback / "observations.json"),
                    },
                }

            monkeypatch.setattr(screening, "evaluate_selected", evaluate)
        session = screening.prepare_screening(
            monomer,
            SelectionConfig(strategy="fixed", batch_size=1, max_evaluations=1),
            feedback=None if live else feedback,
            runtime={"synthetic": True} if live else None,
            artifacts=tmp_path / "sessions",
        )
        if completed:
            if empty:
                request = screening.observe_screening(session)
                screening.apply_screening(
                    session,
                    {
                        "request_sha256": request["request_sha256"],
                        "decision": {
                            "action": "stop",
                            "candidate_ids": [],
                            "reason": "Synthetic stop.",
                            "evidence": [],
                        },
                        "actor": {"harness": "synthetic", "model": None, "agent_id": None},
                    },
                )
                screening.observe_screening(session)
            else:
                screening.run_screening(session)
        return session

    return SimpleNamespace(
        prepare=prepare,
        generation=generation,
        monomer=monomer,
        complex_job=complex_job,
        feedback=feedback,
    )


@pytest.mark.parametrize("live", [False, True])
def test_export_is_complete_portable_selected_only_and_read_only(synthetic_session, tmp_path, live):
    session = synthetic_session.prepare(live=live)
    before = {path: sha256_file(path) for path in tmp_path.rglob("*") if path.is_file()}
    destination = exporting.export_screening(session, tmp_path / "export")
    assert all(sha256_file(path) == digest for path, digest in before.items())
    manifest = read_json(destination / "manifest.json")
    names = {record["path"] for record in manifest["artifacts"]}
    assert names == {
        str(path.relative_to(destination))
        for path in destination.rglob("*")
        if path.is_file() and path != destination / "manifest.json"
    }
    for record in manifest["artifacts"]:
        checked_path(destination, record)
    assert manifest["candidate_count"] == 2 and manifest["evaluated_count"] == 1
    rows = read_json(destination / "candidates.json")["candidates"]
    assert [row["complex_evaluation_status"] for row in rows] == ["evaluated", "unevaluated"]
    assert rows[0]["post"]["iptm"] == 0.5 and rows[1]["post"] is None
    for row in rows:
        assert row["acceptance"] is None and row["binding_validated"] is False
        for path in row["artifacts"].values():
            if path is not None:
                assert not Path(path).is_absolute() and (destination / path).is_file()
    assert len(list(destination.rglob("generated.cif"))) == 2
    assert len(list(destination.rglob("monomer.pdb"))) == 2
    assert len(list(destination.rglob("complex.cif"))) == 1
    assert (destination / "selected.fasta").read_text() == ">synthetic-0\nAAAA\n"
    assert (destination / "evaluated.fasta").read_bytes() == (
        destination / "selected.fasta"
    ).read_bytes()
    assert ">synthetic-1\nCCCC\n" in (destination / "all.fasta").read_text()
    assert not (destination / "candidates/candidate0001/observation.json").exists()
    assert read_json(destination / "report.json")["new_model_inference"] is live
    for path in destination.rglob("*"):
        if path.suffix in {".json", ".md", ".csv", ".fasta"}:
            assert str(tmp_path) not in path.read_text()
            assert "/runtime/" not in path.read_text()
    csv_rows = list(csv.DictReader((destination / "metrics.csv").open()))
    assert csv_rows[1]["iptm"] == "" and csv_rows[1]["acceptance"] == "null"
    provenance = read_json(destination / "provenance.json")
    assert provenance["session_manifest_sha256"] == sha256_file(session / "manifest.json")
    assert provenance["original_task_sha256"] != provenance["portable_task_sha256"]
    moved = tmp_path / "moved"
    shutil.move(destination, moved)
    for record in manifest["artifacts"]:
        checked_path(moved, record)


def test_stop_without_evaluation_exports_no_complex(synthetic_session, tmp_path):
    session = synthetic_session.prepare(empty=True)
    destination = exporting.export_screening(session, tmp_path / "export")
    assert (destination / "selected.fasta").read_text() == ""
    assert list(destination.rglob("complex.cif")) == []
    assert all(
        row["post"] is None for row in read_json(destination / "candidates.json")["candidates"]
    )


@pytest.mark.parametrize(
    "mutation", ["prepared", "status", "report", "coverage", "receipt", "extra-step-file"]
)
def test_export_rejects_incomplete_or_tampered_session(synthetic_session, tmp_path, mutation):
    session = synthetic_session.prepare(completed=mutation != "prepared")
    if mutation == "status":
        write_json(session / "status.json", {"status": "failed"})
    elif mutation == "report":
        report = read_json(session / "report.json")
        report["acceptance"] = True
        write_json(session / "report.json", report)
    elif mutation == "coverage":
        write_json(session / "completed.json", {"artifacts": []})
    elif mutation == "receipt":
        path = session / "steps/0000/receipt.json"
        path.write_text(path.read_text() + " ")
    elif mutation == "extra-step-file":
        (session / "steps/0000/extra.txt").write_text("unrecorded")
    with pytest.raises(ValueError):
        exporting.export_screening(session, tmp_path / "export")
    assert not (tmp_path / "export").exists()


@pytest.mark.parametrize(
    "artifact", ["generated", "monomer", "complex", "complex-manifest", "feedback"]
)
def test_export_rejects_changed_source_and_cleans_staging(synthetic_session, tmp_path, artifact):
    session = synthetic_session.prepare()
    paths = {
        "generated": synthetic_session.generation / "output/synthetic-0.cif",
        "monomer": synthetic_session.monomer / "output/synthetic-0/binder.pdb",
        "complex": synthetic_session.complex_job / "output/synthetic-0/complex.cif",
        "complex-manifest": synthetic_session.complex_job / "manifest.json",
        "feedback": synthetic_session.feedback / "observations.json",
    }
    path = paths[artifact]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        exporting.export_screening(session, tmp_path / "export")
    assert not (tmp_path / "export").exists()
    assert not list(tmp_path.glob(".export.*"))


def test_export_refuses_existing_destination_and_session_subtree(synthetic_session, tmp_path):
    session = synthetic_session.prepare()
    destination = tmp_path / "export"
    destination.mkdir()
    (destination / "keep.txt").write_text("preserve")
    with pytest.raises(FileExistsError):
        exporting.export_screening(session, destination)
    assert (destination / "keep.txt").read_text() == "preserve"
    with pytest.raises(ValueError, match="outside"):
        exporting.export_screening(session, session / "export")


def test_replay_export_allows_equivalent_original_json_serialization(synthetic_session, tmp_path):
    source = synthetic_session.feedback / "observations.json"
    source.write_text(json.dumps(read_json(source), separators=(",", ":")))
    seal_output(synthetic_session.feedback)
    session = synthetic_session.prepare()
    assert sha256_file(source) != sha256_file(session / "replay.json")
    exporting.export_screening(session, tmp_path / "export")


def test_completion_manifest_is_published_only_after_all_data(
    synthetic_session, tmp_path, monkeypatch
):
    session = synthetic_session.prepare()
    destination = tmp_path / "export"
    move = exporting.shutil.move
    published = []

    def checked_move(source, target):
        assert not (destination / "manifest.json").exists()
        if Path(source).name == "manifest.json":
            for record in read_json(Path(source))["artifacts"]:
                artifact = destination / record["path"]
                assert artifact.is_file()
                assert sha256_file(artifact) == record["sha256"]
        published.append(Path(source).name)
        return move(source, target)

    monkeypatch.setattr(exporting.shutil, "move", checked_move)
    exporting.export_screening(session, destination)
    assert published[-1] == "manifest.json"

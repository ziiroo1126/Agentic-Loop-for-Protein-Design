from __future__ import annotations

import shutil
import sys
from pathlib import Path

import numpy as np
import pytest
from biotite.structure.io.pdbx import CIFFile, get_structure, set_structure

from interaction_design.assets import sha256_file
from interaction_design.cli import main
from interaction_design.evaluation.complex import (
    default_policy,
    load_complex_job,
    prepare_complex_batch,
    run_complex_batch,
    validate_complex_output,
)
from interaction_design.evaluation.feedback import write_interface_feedback
from interaction_design.evaluation.jobs import file_record, read_json
from interaction_design.generator import ODesignGenerator
from interaction_design.persistence import write_json
from interaction_design.runtime import MockODesignExecutor
from interaction_design.specs import InteractionDesignSpec
from interaction_design.workflow import DesignWorkflow


@pytest.fixture
def complex_job(tmp_path):
    spec = InteractionDesignSpec.model_validate(
        {
            "name": "synthetic_complex",
            "model": "odesign_base_prot_flex",
            "design_modality": "protein",
            "reference_structure": str(tmp_path / "mock.cif"),
            "molecules": [
                {
                    "id": "target",
                    "type": "protein",
                    "role": "context",
                    "segments": [{"kind": "fixed", "chain": "B", "start": 1, "end": 8}],
                },
                {
                    "id": "binder",
                    "type": "protein",
                    "role": "design",
                    "segments": [{"kind": "generated", "min_length": 5, "max_length": 5}],
                },
            ],
            "evaluation": {"binder_molecule": "binder"},
        }
    )
    run = DesignWorkflow(ODesignGenerator(MockODesignExecutor(), tmp_path / "runs")).run(
        spec, evaluate=False
    )
    before = (run.run_dir / "manifest.json").read_bytes()
    job = prepare_complex_batch(run.run_dir, artifacts=tmp_path / "complexes")
    assert (run.run_dir / "manifest.json").read_bytes() == before
    return job


def synthetic_output(job: Path, output: Path):
    """Synthetic IO fixtures, never used for real candidates or real model scores."""
    request_manifest = load_complex_job(job)
    entry = request_manifest["requests"][0]
    request = read_json(job / entry["path"])
    output.mkdir(parents=True)
    folder = output / "candidate0000"
    folder.mkdir()
    source = get_structure(
        CIFFile.read(request["reference"]["path"]), model=1, use_author_fields=False
    )
    a, b = source[source.chain_id == "B"], source[source.chain_id == "A"]
    a.chain_id[:], b.chain_id[:] = "A", "B"
    atoms = a + b
    atoms.set_annotation("b_factor", np.full(len(atoms), 80.0))
    cif = CIFFile()
    set_structure(cif, atoms)
    cif.write(folder / "complex.cif")
    n_a, n_b = [len(x["sequence"]) for x in request["chains"]]
    np.savez(
        folder / "confidence.npz",
        plddt_0_to_1=np.full(n_a + n_b, 0.8),
        pae_angstrom=np.full((n_a + n_b, n_a + n_b), 5.0),
        chain_ids=np.array(["A"] * n_a + ["B"] * n_b),
        atom_positions=atoms.coord,
    )
    write_json(
        folder / "metrics.json",
        {
            "backend": "esmfold2",
            "candidate_id": request["candidate_id"],
            "request_sha256": entry["sha256"],
            "policy": request_manifest["policy"],
            "ptm": 0.7,
            "iptm": 0.6,
            "mean_plddt_residue": 80.0,
            "binder_plddt_residue": 80.0,
            "target_plddt_residue": 80.0,
            "pae_a_to_b_angstrom": 5.0,
            "pae_b_to_a_angstrom": 5.0,
            "inference_seconds": 1.0,
            "peak_torch_allocated_bytes": 100,
            "peak_torch_reserved_bytes": 120,
            "threshold_pass": None,
        },
    )
    write_json(
        output / "provenance.json",
        {
            "backend": "esmfold2",
            "policy": request_manifest["policy"],
            "synthetic": True,
        },
    )
    write_json(
        output / "status.json",
        {
            "status": "completed",
            "requests_sha256": sha256_file(job / "manifest.json"),
            "candidates": [
                {
                    "candidate_id": request["candidate_id"],
                    "output_dir": folder.name,
                    "request_sha256": entry["sha256"],
                }
            ],
        },
    )
    seal_output(output)
    return folder


def seal_output(output):
    write_json(
        output / "manifest.json",
        {
            "artifacts": [
                file_record(p, output)
                for p in sorted(output.rglob("*"))
                if p.is_file() and p.name != "manifest.json"
            ]
        },
    )


def test_complex_chain_mapping_and_selection(complex_job, tmp_path):
    manifest = load_complex_job(complex_job)
    request = read_json(complex_job / manifest["requests"][0]["path"])
    assert [x["generation_chain"] for x in request["chains"]] == ["B", "A"]
    assert [len(x["sequence"]) for x in request["chains"]] == [5, 8]
    assert manifest["policy"]["backend"] == "esmfold2"
    assert manifest["policy"]["msa_mode"] == "none"
    assert manifest["policy"]["thresholds"] is None
    for selected in [["unknown"], [], [request["candidate_id"]] * 2]:
        with pytest.raises(ValueError, match="selection"):
            prepare_complex_batch(
                Path(manifest["generation_run"]), candidates=selected, artifacts=tmp_path / "unused"
            )


def test_complex_refuses_modified_request(complex_job):
    row = load_complex_job(complex_job)["requests"][0]
    path = complex_job / row["path"]
    path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        load_complex_job(complex_job)


@pytest.mark.parametrize("mutation", ["identity", "scale", "chain", "protocol", "coordinates"])
def test_complex_independently_validates_native_output(complex_job, tmp_path, mutation):
    output = complex_job / "synthetic-output"
    folder = synthetic_output(complex_job, output)
    result = validate_complex_output(complex_job, output)[0]
    assert result["binder_ca_rmsd_angstrom"] < 1e-5
    assert result["binder_rmsd_after_target_alignment_angstrom"] < 1e-5
    if mutation in {"identity", "scale", "protocol"}:
        metrics = read_json(folder / "metrics.json")
        if mutation == "identity":
            metrics["candidate_id"] = "different-candidate"
        elif mutation == "scale":
            metrics["binder_plddt_residue"] = 0.8
        else:
            metrics["policy"]["num_diffusion_samples"] = 5
        write_json(folder / "metrics.json", metrics)
    else:
        atoms = get_structure(
            CIFFile.read(folder / "complex.cif"),
            model=1,
            use_author_fields=False,
            extra_fields=["b_factor"],
        )
        if mutation == "chain":
            atoms.chain_id[atoms.chain_id == "A"] = "C"
        else:
            atoms.coord[0, 0] += 1
        cif = CIFFile()
        set_structure(cif, atoms)
        cif.write(folder / "complex.cif")
    # Reseal to exercise semantic checks, beyond byte-level checksums.
    seal_output(output)
    with pytest.raises(ValueError):
        validate_complex_output(complex_job, output)


def fake_runtime(tmp_path):
    policy = default_policy()
    paths = [tmp_path / policy[k] for k in ["model_revision", "esmc_revision"]]
    for path in paths:
        path.mkdir()
        write_json(path / "config.json", {})
    ccd = tmp_path / "synthetic-ccd.pkl"
    ccd.write_text("not a real CCD; never unpickled")
    return write_json(
        tmp_path / "runtime.json",
        {
            "python": sys.executable,
            "model_dir": str(paths[0]),
            "esmc_dir": str(paths[1]),
            "ccd": str(ccd),
            "unset_ld_library_path": True,
        },
    )


def test_complex_cli_completion_reuses_verified_results(complex_job, tmp_path, monkeypatch):
    config = fake_runtime(tmp_path)
    calls = []

    def fake_run(command, attempt, **kwargs):
        calls.append(command)
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert "LD_LIBRARY_PATH" not in kwargs["env"]
        synthetic_output(complex_job, attempt / "output")
        write_json(attempt / "execution.json", {"status": "completed", "elapsed_seconds": 2.0})

    monkeypatch.setattr("interaction_design.evaluation.complex.run_logged", fake_run)
    assert main(["complex", "run", str(complex_job), "--config", str(config)]) == 0
    # Completed jobs do not need model/runtime files to generate verified reports.
    config.unlink()
    run_complex_batch(complex_job, config=config)
    assert len(calls) == 1
    report = read_json(complex_job / "report.json")
    assert report["binding_validated"] is False
    assert report["process_seconds_including_failures"] == 2.0
    assert main(["complex", "report", str(complex_job)]) == 0


def test_complex_failed_attempts_consume_budget(complex_job, tmp_path, monkeypatch):
    config = fake_runtime(tmp_path)
    old = complex_job / "executions" / "previous" / "execution.json"
    write_json(old, {"status": "failed", "elapsed_seconds": 1800})

    def unexpected(*args, **kwargs):
        pytest.fail("exhausted job must not launch the worker")

    monkeypatch.setattr("interaction_design.evaluation.complex.run_logged", unexpected)
    with pytest.raises(ValueError, match="budget exhausted"):
        run_complex_batch(complex_job, config=config)
    write_json(old, {"status": "running"})
    with pytest.raises(ValueError, match="unfinished"):
        run_complex_batch(complex_job, config=config)


def test_complex_requires_all_output_artifacts_to_be_recorded(complex_job):
    output = complex_job / "synthetic-output"
    synthetic_output(complex_job, output)
    manifest = read_json(output / "manifest.json")
    manifest["artifacts"] = [
        x for x in manifest["artifacts"] if not x["path"].endswith("confidence.npz")
    ]
    write_json(output / "manifest.json", manifest)
    with pytest.raises(ValueError, match="absent from the artifact manifest"):
        validate_complex_output(complex_job, output)


def test_generation_cli_can_continue_into_esmfold2(complex_job, tmp_path, monkeypatch):
    prior = read_json(Path(load_complex_job(complex_job)["generation_run"]) / "manifest.json")
    task = write_json(tmp_path / "task.json", prior["task"])
    config = fake_runtime(tmp_path)
    calls = []

    def fake_run(command, attempt, **kwargs):
        calls.append(command)
        job = Path(command[command.index("--requests") + 1]).parent
        synthetic_output(job, attempt / "output")
        write_json(attempt / "execution.json", {"status": "completed", "elapsed_seconds": 2.0})

    monkeypatch.setattr("interaction_design.evaluation.complex.run_logged", fake_run)
    artifacts = tmp_path / "continued-run"
    assert (
        main(["run", str(task), "--artifacts", str(artifacts), "--complex-config", str(config)])
        == 0
    )
    assert len(calls) == 1
    reports = list((artifacts / "complex-assessments").glob("*/report.json"))
    assert len(reports) == 1
    assert read_json(reports[0])["backend"] == "esmfold2"


def test_default_cli_requires_esmfold2_runtime_before_generation(
    complex_job, tmp_path, monkeypatch
):
    prior = read_json(Path(load_complex_job(complex_job)["generation_run"]) / "manifest.json")
    task = write_json(tmp_path / "task.json", prior["task"])
    monkeypatch.chdir(tmp_path)

    def unexpected(*args):
        pytest.fail("missing ESMFold2 runtime must fail before launching generation")

    monkeypatch.setattr("interaction_design.cli._executor", unexpected)
    with pytest.raises(SystemExit) as exc:
        main(["run", str(task)])
    assert exc.value.code == 2


@pytest.fixture
def feedback_job(complex_job, tmp_path):
    """Create a wholly synthetic completed job with an exact target reference."""
    prepared = load_complex_job(complex_job)
    generation_dir = Path(prepared["generation_run"])
    generation = read_json(generation_dir / "manifest.json")
    request = read_json(complex_job / prepared["requests"][0]["path"])
    source = get_structure(
        CIFFile.read(request["reference"]["path"]), model=1, use_author_fields=False
    )
    target = source[source.chain_id == "A"]
    target.chain_id[:] = "B"
    reference = Path(generation["task"]["reference_structure"])
    cif = CIFFile()
    set_structure(cif, target)
    cif.write(reference)
    # Mock generation does not read reference coordinates. Supply its synthetic
    # sequence as the reference before preparing a new, internally consistent job.
    generation["inputs"]["reference_structure"] = {
        "path": str(reference),
        "sha256": sha256_file(reference),
        "size": reference.stat().st_size,
    }
    write_json(generation_dir / "manifest.json", generation)
    job = prepare_complex_batch(generation_dir, artifacts=tmp_path / "feedback-inputs")
    output = job / "synthetic-output"
    synthetic_output(job, output)
    execution = write_json(job / "execution.json", {"status": "completed", "elapsed_seconds": 2})
    write_json(
        job / "completed.json",
        {
            "request_manifest_sha256": sha256_file(job / "manifest.json"),
            "execution": file_record(execution, job),
            "output_manifest": file_record(output / "manifest.json", job),
        },
    )
    return job, reference


def test_feedback_cli_preserves_completed_inputs(feedback_job, tmp_path):
    job, reference = feedback_job
    generation = Path(load_complex_job(job)["generation_run"])
    paths = [reference] + [p for root in [job, generation] for p in root.rglob("*") if p.is_file()]
    before = {p: sha256_file(p) for p in paths}
    destination = tmp_path / "feedback"
    assert main(["complex", "feedback", str(job), "--artifacts", str(destination)]) == 0
    analysis = next(destination.iterdir())
    report = read_json(analysis / "observations.json")
    assert report["candidate_count"] == 1
    row = report["observations"][0]
    assert row["target_mapping"]["residues"][0]["source_chain"] == "B"
    assert row["geometry"]["predicted"]["hotspot_coverage"] is None
    assert row["geometry"]["predicted"]["contact_residue_pair_count"] > 0
    assert row["design_consistency"]["generated_contact_retention"] == 1
    assert row["acceptance"] is None and row["binding_validated"] is False
    assert report["controls"] is None
    assert read_json(analysis / "status.json")["status"] == "completed"
    assert before == {p: sha256_file(p) for p in paths}
    for record in read_json(analysis / "manifest.json")["artifacts"]:
        assert sha256_file(analysis / record["path"]) == record["sha256"]


@pytest.mark.parametrize("mutation", ["reference", "prediction", "overlap"])
def test_feedback_refuses_changed_or_duplicated_inputs(feedback_job, tmp_path, mutation):
    job, reference = feedback_job
    jobs = [job]
    if mutation == "reference":
        reference.write_text(reference.read_text() + "\n")
    elif mutation == "prediction":
        structure = next((job / "synthetic-output").rglob("complex.cif"))
        structure.write_text(structure.read_text() + "\n")
    else:
        duplicate = tmp_path / "duplicate-job"
        shutil.copytree(job, duplicate)
        jobs.append(duplicate)
    destination = tmp_path / "failed-feedback"
    with pytest.raises(ValueError):
        write_interface_feedback(jobs, artifacts=destination)
    analysis = next(destination.iterdir())
    assert read_json(analysis / "status.json")["status"] == "failed"
    assert len(read_json(analysis / "request.json")["jobs"]) == len(jobs)
    assert not (analysis / "observations.json").exists()
    for record in read_json(analysis / "manifest.json")["artifacts"]:
        assert sha256_file(analysis / record["path"]) == record["sha256"]

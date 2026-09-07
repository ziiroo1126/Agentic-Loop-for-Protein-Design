from __future__ import annotations

from pathlib import Path

import pytest
from biotite.structure.io.pdbx import CIFFile, get_structure, set_structure

from interaction_design.assets import sha256_file
from interaction_design.evaluation.af3 import make_input, target_features
from interaction_design.evaluation.jobs import (
    import_result,
    job_lock,
    load_job,
    prepare_assessment,
    read_json,
    receipt,
    report_assessment,
)
from interaction_design.evaluation.runtime import (
    AF3Runtime,
    EvaluationRuntime,
    RosettaRuntime,
    check_assessment,
    remaining_budget,
    run_assessment,
    stage_command,
)
from interaction_design.generator import ODesignGenerator
from interaction_design.persistence import write_json
from interaction_design.runtime import MockODesignExecutor
from interaction_design.specs import InteractionDesignSpec
from interaction_design.workflow import DesignWorkflow


@pytest.fixture
def generation(tmp_path):
    # Binder originally B: evaluation must deliberately remap it to AF3 A.
    spec = InteractionDesignSpec.model_validate(
        {
            "name": "synthetic_ppi",
            "model": "odesign_base_prot_flex",
            "design_modality": "protein",
            "reference_structure": str(tmp_path / "unused_by_mock.cif"),
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
    return DesignWorkflow(ODesignGenerator(MockODesignExecutor(), tmp_path / "runs")).run(
        spec,
        num_designs=1,
        evaluate=False,
    )


def fake_af3_output(job, out):
    """Synthetic output for IO contract tests; never used for real candidates."""
    payload = load_job(job)
    candidate = payload["candidates"][0]
    request = read_json(job / candidate["input"]["path"])
    source = job.parents[1] / candidate["source_structure"]["path"]
    atoms = get_structure(CIFFile.read(source), model=1, use_author_fields=False)
    binder, target = atoms[atoms.chain_id == "B"], atoms[atoms.chain_id == "A"]
    binder.chain_id[:] = "A"
    target.chain_id[:] = "B"
    model = CIFFile()
    set_structure(model, binder + target)
    folder = out / candidate["key"]
    folder.mkdir(parents=True)
    model.write(folder / f"{candidate['key']}_model.cif")
    write_json(folder / f"{candidate['key']}_data.json", request)
    write_json(
        folder / f"{candidate['key']}_summary_confidences.json",
        {
            "ptm": 0.85,
            "iptm": 0.9,
            "chain_ids": ["A", "B"],
            "chain_ptm": [0.95, 0.55],
            "chain_pair_pae_min": [[0, 1], [1, 0]],
        },
    )
    (folder / "ranking_scores.csv").write_text("seed,sample,ranking_score\n1,0,0.9\n")
    return candidate, folder


def fake_rosetta_output(job, path, *, wrong_structure=False):
    candidate = load_job(job)["candidates"][0]
    af3 = receipt(job, candidate, "af3")
    return write_json(
        path,
        {
            "ddg": -50,
            "sap_score": 20,
            "contact_molecular_surface": 500,
            "provenance": {
                "structure_sha256": "0" * 64
                if wrong_structure
                else af3["artifacts"]["af3_structure"]["sha256"],
                "protocol_sha256": sha256_file(job / "ppi.xml"),
                "binder_chain": "A",
                "target_chain": "B",
                "seed": 1,
            },
        },
    )


def test_preparation_maps_binder_and_requires_explicit_msa_policy(generation):
    before = generation.manifest.read_bytes()
    job = prepare_assessment(generation.run_dir)
    payload = load_job(job)
    request = read_json(job / payload["candidates"][0]["input"]["path"])
    binder, target = [x["protein"] for x in request["sequences"]]
    assert binder["id"] == "A" and len(binder["sequence"]) == 5
    assert binder["templates"] == [] and binder["unpairedMsa"] == ""
    assert target["id"] == "B" and len(target["sequence"]) == 8
    assert "unpairedMsa" not in target and "templates" not in target
    assert payload["protocol"]["msa_mode"] == "search"
    assert generation.manifest.read_bytes() == before
    free = make_input("job", "AAA", "VVV", seed=1, msa_mode="none")
    assert free["sequences"][1]["protein"]["unpairedMsa"] == ""


def test_provided_target_data_validates_sequence_and_query():
    protein = {
        "id": "X",
        "sequence": "AAA",
        "unpairedMsa": ">query\nAAA\n",
        "pairedMsa": "",
        "templates": [],
    }
    assert target_features({"sequences": [{"protein": protein}]}, "AAA")["templates"] == []
    protein["unpairedMsa"] = ">wrong_query\nVVV\n"
    with pytest.raises(ValueError, match="MSA query"):
        target_features({"sequences": [{"protein": protein}]}, "AAA")
    with pytest.raises(ValueError, match="matching protein"):
        target_features({"sequences": [{"protein": protein}]}, "VVV")


def test_missing_runtime_blocks_with_actionable_state_without_generation(generation):
    job = prepare_assessment(generation.run_dir)
    assert not check_assessment(job, EvaluationRuntime())["ready"]
    result = run_assessment(job, EvaluationRuntime())
    assert result["status"] == "blocked"
    assert "not configured" in result["issues"][0]
    assert not (job / "executions").exists()
    assert generation.report_json.is_file()
    with pytest.raises(ValueError, match="missing af3"):
        report_assessment(job)


def test_import_and_report_validate_identity_and_use_remapped_binder(generation, tmp_path):
    job = prepare_assessment(generation.run_dir, msa_mode="none")
    candidate, folder = fake_af3_output(job, tmp_path / "synthetic_outputs")
    input_path = folder / f"{candidate['key']}_data.json"
    original = input_path.read_bytes()
    bad = read_json(input_path)
    bad["sequences"][0]["protein"]["sequence"] = "WWWWW"
    write_json(input_path, bad)
    with pytest.raises(ValueError, match="chain IDs/sequences"):
        import_result(job, candidate["id"], "af3", folder)
    input_path.write_bytes(original)
    # Native AF3 materializes an empty MSA as a query-only alignment.
    processed = read_json(input_path)
    for item in processed["sequences"]:
        seq = item["protein"]["sequence"]
        item["protein"]["unpairedMsa"] = f">query\n{seq}\n"
        item["protein"]["pairedMsa"] = f">query\n{seq}\n"
    write_json(input_path, processed)
    import_result(job, candidate["id"], "af3", folder)
    with pytest.raises(ValueError, match="already accepted"):
        import_result(job, candidate["id"], "af3", folder)
    scores = fake_rosetta_output(job, tmp_path / "scores.json", wrong_structure=True)
    with pytest.raises(ValueError, match="structure checksum"):
        import_result(job, candidate["id"], "rosetta", scores)
    fake_rosetta_output(job, scores)
    import_result(job, candidate["id"], "rosetta", scores)
    before = generation.manifest.read_bytes()
    result = report_assessment(job)
    report = read_json(result.report_json)
    assert report["candidates"][0]["evaluations"]["af3"]["binder_ptm"] == 0.95
    assert "af3_structure" in report["candidates"][0]["artifacts"]
    assert generation.manifest.read_bytes() == before
    receipt_file = receipt(job, candidate, "af3")["artifacts"]["af3_summary"]["path"]
    with (job / receipt_file).open("a") as handle:
        handle.write("\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        report_assessment(job)


def test_wrong_refold_geometry_cannot_be_imported(generation, tmp_path):
    job = prepare_assessment(generation.run_dir, msa_mode="none")
    candidate, folder = fake_af3_output(job, tmp_path / "synthetic_outputs")
    model_path = folder / f"{candidate['key']}_model.cif"
    atoms = get_structure(CIFFile.read(model_path), model=1, use_author_fields=False)
    atoms.res_name[atoms.chain_id == "A"] = "TRP"
    model = CIFFile()
    set_structure(model, atoms)
    model.write(model_path)
    with pytest.raises(ValueError, match="model sequence mismatch"):
        import_result(job, candidate["id"], "af3", folder)


def test_completed_af3_is_reused_after_rosetta_failure(generation, tmp_path, monkeypatch):
    from interaction_design.evaluation import runtime

    job = prepare_assessment(generation.run_dir, msa_mode="none", budget_seconds=10)
    config = EvaluationRuntime(
        af3=AF3Runtime(
            python=Path("/fake/python"),
            repo=tmp_path,
            model_dir=tmp_path,
            expected_revision="0" * 40,
        ),
        rosetta=RosettaRuntime(python=Path("/fake/python")),
    )
    monkeypatch.setattr(runtime, "inspect_stage", lambda *args: {"issues": []})
    calls = []

    def synthetic_process(command, attempt, *, metadata, **kwargs):
        stage = metadata["stage"]
        calls.append(stage)
        write_json(attempt / "execution.json", {"status": "completed", "elapsed_seconds": 1})
        if stage == "af3":
            fake_af3_output(job, attempt / "output")
        elif calls.count("rosetta") == 1:
            raise RuntimeError("synthetic Rosetta failure")
        else:
            fake_rosetta_output(job, attempt / "metrics.json")

    monkeypatch.setattr(runtime, "run_logged", synthetic_process)
    with pytest.raises(ValueError, match="synthetic Rosetta failure"):
        run_assessment(job, config)
    assert run_assessment(job, config)["status"] == "ready_to_report"
    assert calls == ["af3", "rosetta", "rosetta"]
    assert remaining_budget(job, load_job(job)) == 7
    assert len(list((job / "executions").glob("*/*/*/failure.json"))) == 1


def test_budget_and_concurrent_access_are_enforced(generation, monkeypatch):
    from interaction_design.evaluation import runtime

    job = prepare_assessment(generation.run_dir, msa_mode="none", budget_seconds=2)
    write_json(
        job / "executions" / "c" / "af3" / "failed" / "execution.json",
        {"status": "failed", "elapsed_seconds": 2},
    )
    monkeypatch.setattr(runtime, "inspect_stage", lambda *args: pytest.fail("no work after budget"))
    assert run_assessment(job, EvaluationRuntime())["status"] == "budget_exhausted"
    with job_lock(job), pytest.raises(ValueError, match="already running"):
        run_assessment(job, EvaluationRuntime())


def test_native_command_uses_explicit_single_sample_and_pipeline_switch(generation, tmp_path):
    job = prepare_assessment(generation.run_dir, msa_mode="none")
    payload = load_job(job)
    config = EvaluationRuntime(
        af3=AF3Runtime(
            python=Path("/env/python"),
            repo=tmp_path,
            model_dir=tmp_path / "models",
            expected_revision="a" * 40,
        )
    )
    command = stage_command("af3", job, payload["candidates"][0], tmp_path, payload, config)
    assert "--run_data_pipeline=false" in command
    assert "--num_diffusion_samples=1" in command
    assert command[0] == "/env/python"


def test_import_rejects_best_of_more_samples_than_requested(generation, tmp_path):
    job = prepare_assessment(generation.run_dir, msa_mode="none")
    candidate, folder = fake_af3_output(job, tmp_path / "synthetic_outputs")
    with (folder / "ranking_scores.csv").open("a") as handle:
        handle.write("1,1,0.95\n")
    with pytest.raises(ValueError, match="sampling count"):
        import_result(job, candidate["id"], "af3", folder)


def test_failed_stage_timeout_is_capped_by_remaining_budget(generation, tmp_path, monkeypatch):
    from interaction_design.evaluation import runtime

    job = prepare_assessment(generation.run_dir, msa_mode="none", budget_seconds=3)
    config = EvaluationRuntime(
        af3=AF3Runtime(
            python=Path("/fake/python"),
            repo=tmp_path,
            model_dir=tmp_path,
            expected_revision="0" * 40,
        ),
        stage_timeout_seconds=900,
    )
    write_json(
        job / "executions" / "c" / "af3" / "old" / "execution.json",
        {"status": "failed", "elapsed_seconds": 2},
    )
    monkeypatch.setattr(runtime, "inspect_stage", lambda *args: {"issues": []})

    def timeout_process(command, attempt, *, timeout_seconds, **kwargs):
        assert timeout_seconds == 1
        write_json(attempt / "execution.json", {"status": "timed_out", "elapsed_seconds": 1})
        raise RuntimeError("synthetic timeout")

    monkeypatch.setattr(runtime, "run_logged", timeout_process)
    with pytest.raises(ValueError, match="synthetic timeout"):
        run_assessment(job, config)
    assert run_assessment(job, config)["status"] == "budget_exhausted"


def test_cancellation_retains_stage_state_and_charges_elapsed_time(
    generation, tmp_path, monkeypatch
):
    from interaction_design.evaluation import runtime

    job = prepare_assessment(generation.run_dir, msa_mode="none", budget_seconds=10)
    config = EvaluationRuntime(
        af3=AF3Runtime(
            python=Path("/fake/python"),
            repo=tmp_path,
            model_dir=tmp_path,
            expected_revision="0" * 40,
        )
    )
    monkeypatch.setattr(runtime, "inspect_stage", lambda *args: {"issues": []})

    def cancelled(command, attempt, **kwargs):
        write_json(attempt / "execution.json", {"status": "failed", "elapsed_seconds": 1})
        raise KeyboardInterrupt()

    monkeypatch.setattr(runtime, "run_logged", cancelled)
    with pytest.raises(KeyboardInterrupt):
        run_assessment(job, config)
    assert read_json(job / "status.json")["status"] == "cancelled"
    assert remaining_budget(job, load_job(job)) == 9

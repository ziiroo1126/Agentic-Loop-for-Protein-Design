"""Stage lifecycle tests with synthetic molecular tools; no scientific benchmark."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

import pytest
from test_screening import executor as executor  # noqa: F401
from test_screening import submission

from interaction_design import pipeline
from interaction_design.cli import main
from interaction_design.evaluation.jobs import read_json
from interaction_design.generator import ODesignGenerator
from interaction_design.persistence import write_json
from interaction_design.runtime import MockODesignExecutor
from interaction_design.selection import SelectionConfig
from interaction_design.specs import InteractionDesignSpec
from interaction_design.workflow import DesignWorkflow


@pytest.fixture
def flow(tmp_path, monkeypatch, executor):
    target = tmp_path / "synthetic-target.pdb"
    target.write_text("Synthetic task reference; mock generator supplies coordinates.\n")
    task = InteractionDesignSpec.model_validate(
        {
            "name": "synthetic_pipeline",
            "model": "odesign_base_prot_flex",
            "design_modality": "protein",
            "reference_structure": str(target),
            "molecules": [
                {
                    "id": "binder",
                    "type": "protein",
                    "role": "design",
                    "segments": [{"kind": "generated", "min_length": 4, "max_length": 4}],
                },
                {
                    "id": "target",
                    "type": "protein",
                    "role": "context",
                    "segments": [{"kind": "fixed", "chain": "B", "start": 1, "end": 4}],
                },
            ],
            "generation": {"seeds": [1, 2, 3]},
            "hotspots": [{"chain": "B", "residue": 2}],
            "evaluation": {"binder_molecule": "binder", "metric_rules": []},
        }
    )
    # Preflight has independent real structure/asset tests in test_pipeline_config.
    monkeypatch.setattr(pipeline, "validate_pipeline_task", lambda _: None)
    monkeypatch.setattr(pipeline, "validate_pipeline_runtime", lambda _, value: value)
    calls = []

    def generate(directory, spec, runtime):
        calls.append("generation")
        result = DesignWorkflow(
            ODesignGenerator(MockODesignExecutor(), directory / "generation")
        ).run(spec, evaluate=False)
        return {"generation_run": str(result.run_dir.relative_to(directory))}, [result.manifest]

    def monomer(directory, generation, runtime):
        calls.append("monomer")
        job = directory / "synthetic-monomer"
        manifest = write_json(job / "manifest.json", {"generation_run": str(generation)})
        completed = write_json(job / "completed.json", {"synthetic": True})
        return {"monomer_job": str(job.relative_to(directory))}, [manifest, completed]

    monkeypatch.setattr(pipeline, "_generate", generate)
    monkeypatch.setattr(pipeline, "_monomer", monomer)
    monkeypatch.setattr(pipeline, "read_monomer_batch", lambda _: [])
    runtime = {"generation": {}, "monomer": {}, "complex": {"synthetic": True}}

    def prepare(**kwargs):
        kwargs.setdefault("strategy", "harness")
        return pipeline.prepare_pipeline(
            task,
            runtime,
            SelectionConfig(batch_size=1, max_evaluations=1, **kwargs),
            artifacts=tmp_path / "pipelines",
        )

    return SimpleNamespace(
        prepare=prepare, calls=calls, target=target, task=task, runtime=runtime, executor=executor
    )


def test_full_host_loop_reuses_stages_and_preserves_submission(flow):
    directory = flow.prepare()
    assert pipeline.observe_pipeline(directory)["status"] == "prepared"
    assert flow.calls == []
    observed = pipeline.run_pipeline(directory)
    assert observed["status"] == "awaiting_selection"
    assert pipeline.run_pipeline(directory) == observed
    assert flow.calls == ["generation", "monomer"]
    proposal = submission(observed["request"])
    complete = pipeline.apply_pipeline(directory, proposal)
    assert complete["status"] == "completed"
    assert complete["report"]["evaluated_count"] == 1
    assert complete["report"]["acceptance"] is None
    assert (
        pipeline.apply_pipeline(directory, proposal)["application"]["status"] == "already_applied"
    )
    assert len(flow.executor.calls) == 1
    assert pipeline.run_pipeline(directory)["report"] == complete["report"]
    assert flow.calls == ["generation", "monomer"]


def test_fixed_strategy_finishes_without_host(flow):
    result = pipeline.run_pipeline(flow.prepare(strategy="fixed"))
    assert result["status"] == "completed"
    assert result["report"]["strategy"] == "fixed"
    assert result["report"]["decisions"][0]["actor"]["harness"] == "builtin"


def test_prepare_copies_target_and_freezes_runtime(flow):
    directory = flow.prepare()
    flow.target.unlink()
    assert pipeline.observe_pipeline(directory)["status"] == "prepared"
    write_json(directory / "runtime.json", {"changed": True})
    with pytest.raises(ValueError, match="checksum"):
        pipeline.run_pipeline(directory)
    assert flow.calls == []


def test_resume_between_stages_does_not_regenerate(flow):
    directory = flow.prepare()
    task, runtime, _ = pipeline._load(directory)
    pipeline._stage(directory, "generation", lambda: pipeline._generate(directory, task, runtime))
    assert pipeline.observe_pipeline(directory)["status"] == "ready"
    pipeline.run_pipeline(directory)
    assert flow.calls == ["generation", "monomer"]


@pytest.mark.parametrize(
    "exception", [RuntimeError("model failed"), KeyboardInterrupt(), SystemExit(143)]
)
def test_failure_keeps_receipt_and_refuses_automatic_retry(flow, monkeypatch, exception):
    directory = flow.prepare()

    def fail(*args):
        raise exception

    monkeypatch.setattr(pipeline, "_monomer", fail)
    with pytest.raises(type(exception)):
        pipeline.run_pipeline(directory)
    receipt = read_json(directory / "stages/monomer.json")
    assert receipt["status"] == "failed"
    assert receipt["elapsed_seconds"] >= 0
    assert receipt["artifacts_preserved"] is True
    with pytest.raises(ValueError, match="automatic resubmission"):
        pipeline.run_pipeline(directory)
    assert flow.calls == ["generation"]


def test_unfinished_and_deleted_receipts_block_duplicate_compute(flow):
    directory = flow.prepare()
    write_json(directory / "stages/generation.json", {"stage": "generation", "status": "running"})
    with pytest.raises(ValueError, match="running"):
        pipeline.run_pipeline(directory)
    assert flow.calls == []
    directory = flow.prepare()
    pipeline.run_pipeline(directory)
    (directory / "stages/monomer.json").unlink()
    with pytest.raises(ValueError, match="missing"):
        pipeline.run_pipeline(directory)
    assert flow.calls == ["generation", "monomer"]


def test_changed_generation_outputs_block_resume(flow):
    directory = flow.prepare()
    pipeline.run_pipeline(directory)
    receipt = read_json(directory / "stages/generation.json")
    manifest = directory / receipt["result"]["generation_run"] / "manifest.json"
    manifest.write_text(manifest.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        pipeline.run_pipeline(directory)


def test_apply_before_generation_and_stale_decision_never_run_tools(flow):
    directory = flow.prepare()
    with pytest.raises(ValueError, match="before submitting"):
        pipeline.apply_pipeline(directory, {})
    observed = pipeline.run_pipeline(directory)
    proposal = submission(observed["request"])
    proposal["request_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="stale"):
        pipeline.apply_pipeline(directory, proposal)
    assert flow.executor.calls == []


def test_cli_pipeline_apply_reads_stdin_and_prints_complete_json(flow, monkeypatch, capsys):
    directory = flow.prepare()
    observed = pipeline.run_pipeline(directory)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(submission(observed["request"]))))
    assert main(["pipeline", "apply", str(directory), "--decision", "-"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "completed"
    assert result["report"]["evaluated_count"] == 1

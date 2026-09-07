from __future__ import annotations

import json

from interaction_design.generator import ODesignGenerator
from interaction_design.runtime import MockODesignExecutor
from interaction_design.specs import load_spec
from interaction_design.workflow import DesignWorkflow


def test_declarative_task_runs_generation_evaluation_ranking_and_report(tmp_path):
    spec = load_spec("examples/ligand_binder.json")
    workflow = DesignWorkflow(ODesignGenerator(MockODesignExecutor(), tmp_path / "artifacts"))
    result = workflow.run(spec, num_designs=4)

    assert len(result.instances) == 4
    assert [instance.metadata["ranking"]["rank"] for instance in result.instances] == [1, 2, 3, 4]
    assert result.report_json.is_file()
    assert result.report_markdown.is_file()
    assert result.manifest.is_file()

    report = json.loads(result.report_json.read_text(encoding="utf-8"))
    manifest = json.loads(result.manifest.read_text(encoding="utf-8"))
    assert report["evaluated"] is True
    assert report["candidate_count"] == 4
    assert manifest["seeds"] == [7, 19]
    assert manifest["executor"]["name"] == "mock"
    assert all(len(asset["revision"]) == 40 for asset in manifest["model_assets"])
    assert any(item["path"] == "report.json" for item in manifest["artifacts"])


def test_evaluation_failure_is_recoverable_without_regeneration(tmp_path, monkeypatch):
    import pytest

    from interaction_design.workflow import evaluate_run

    generator = ODesignGenerator(MockODesignExecutor(), tmp_path)
    generated = DesignWorkflow(generator).run(
        load_spec("examples/ligand_binder.json"), num_designs=3, evaluate=False
    )
    original_manifest = generated.manifest.read_bytes()
    sidecar = next((generated.run_dir / "output").rglob("*.af3.json"))
    saved = sidecar.read_bytes()
    sidecar.unlink()

    def must_not_generate(*args, **kwargs):
        raise AssertionError("evaluation must not launch generation")

    monkeypatch.setattr(MockODesignExecutor, "execute", must_not_generate)
    with pytest.raises(ValueError, match="generation preserved"):
        evaluate_run(generated.run_dir)
    failure = next((generated.run_dir / "evaluations").rglob("failure.json"))
    assert json.loads(failure.read_text())["stage"] == "evaluation"
    assert (
        json.loads((generated.run_dir / "status.json").read_text())["status"] == "evaluation_failed"
    )
    sidecar.write_bytes(saved)
    result = evaluate_run(generated.run_dir)
    assert len(result.instances) == 3
    assert generated.manifest.read_bytes() == original_manifest
    assert len(list((generated.run_dir / "evaluations").iterdir())) == 2
    assert json.loads(result.manifest.read_text())["generation_manifest_sha256"]
    assert json.loads((generated.run_dir / "status.json").read_text())["status"] == "completed"


def test_evaluation_rejects_changed_structure(tmp_path):
    import pytest

    from interaction_design.workflow import evaluate_run

    result = DesignWorkflow(ODesignGenerator(MockODesignExecutor(), tmp_path)).run(
        load_spec("examples/ligand_binder.json"), num_designs=1, evaluate=False
    )
    cif = next((result.run_dir / "output").rglob("*.cif"))
    with cif.open("a") as handle:
        handle.write("\n# changed after generation\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        evaluate_run(result.run_dir)


def test_failed_generation_keeps_task_and_manifest(tmp_path):
    import pytest

    from interaction_design.runtime.base import ODesignExecutionError

    class FailingExecutor:
        name = "deliberate-failure"

        def execute(self, request):
            raise ODesignExecutionError("test process failure")

    generator = ODesignGenerator(FailingExecutor(), tmp_path)
    with pytest.raises(ODesignExecutionError, match="test process failure"):
        DesignWorkflow(generator).run(load_spec("examples/ligand_binder.json"))
    root = generator.last_run_dir
    assert json.loads((root / "task.json").read_text())["name"]
    assert json.loads((root / "request.json").read_text())["num_designs"] == 4
    assert json.loads((root / "manifest.json").read_text())["status"] == "generation_failed"
    assert json.loads((root / "failure.json").read_text())["stage"] == "generation"

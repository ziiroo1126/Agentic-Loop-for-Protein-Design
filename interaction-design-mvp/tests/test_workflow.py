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

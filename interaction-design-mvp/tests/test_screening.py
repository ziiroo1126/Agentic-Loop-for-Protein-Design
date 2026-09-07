"""Synthetic screening integration checks; these fixtures are not molecular evidence."""

from __future__ import annotations

import io
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from interaction_design import screening
from interaction_design.assets import sha256_file
from interaction_design.cli import main
from interaction_design.evaluation.jobs import read_json
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.selection import SelectionConfig


@pytest.fixture
def executor(tmp_path, monkeypatch):
    model_policy = {"fixture": "synthetic-model"}
    geometry_policy = {"fixture": "synthetic-geometry"}
    source = tmp_path / "synthetic-source.txt"
    source.write_text("Synthetic source identity.\n")
    pool = [
        {
            "candidate_id": f"synthetic-{index}",
            "seed": index,
            "sequence": sequence,
            "length": len(sequence),
            "structure_path": f"/private/synthetic-{index}.cif",
            "pre": {
                "monomer_plddt": 60.0 + 10 * index,
                "monomer_design_rmsd": 1.0,
                "generated_hotspot_coverage": 0.5,
                "generated_clash_residue_pairs": 0,
            },
        }
        for index, sequence in enumerate(["AAAA", "AAAC", "CCCC"], start=1)
    ]
    catalogue = {
        "pool": pool,
        "model_policy": model_policy,
        "geometry_policy": geometry_policy,
        "sources": [{"path": str(source), "sha256": sha256_file(source)}],
    }
    observations = [
        {
            "candidate_id": row["candidate_id"],
            "generation_seed": row["seed"],
            "confidence": {"iptm": 0.12345678 + row["seed"] / 10},
            "geometry": {"predicted": {"hotspot_coverage": 0.5, "clash_residue_pair_count": 0}},
            "design_consistency": {"binder_rmsd_after_target_alignment_angstrom": 1.0},
            "model_policy": model_policy,
            "acceptance": None,
            "binding_validated": False,
            "private_result_path": "/private/UNSELECTED_COMPLEX_RESULT.cif",
            "fixture_synthetic": True,
        }
        for row in pool
    ]
    payload = {
        "observations": observations,
        "model_policy": model_policy,
        "geometry_policy": geometry_policy,
    }
    feedback = tmp_path / "synthetic-feedback"
    write_json(feedback / "observations.json", payload)
    monkeypatch.setattr(screening, "build_catalogue", lambda _: deepcopy(catalogue))
    monkeypatch.setattr(screening, "load_feedback", lambda _: deepcopy(payload))
    monkeypatch.setattr(screening, "validate_complex_runtime", lambda runtime, _: deepcopy(runtime))

    validations = []

    def validate(saved_catalogue, saved_feedback):
        assert saved_catalogue == catalogue
        assert saved_feedback == payload
        validations.append(True)

    monkeypatch.setattr(screening, "validate_replay", validate)
    calls = []

    def evaluate(saved_catalogue, ids, directory, remaining, *, replay, runtime):
        assert saved_catalogue == catalogue
        assert (replay is None) != (runtime is None)
        calls.append({"ids": list(ids), "remaining": remaining, "replay": replay is not None})
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "synthetic-tool.log").write_text("Synthetic evaluator called.\n")
        return {
            "observations": [
                deepcopy(next(row for row in observations if row["candidate_id"] == key))
                for key in ids
            ],
            "model_policy": deepcopy(model_policy),
            "geometry_policy": deepcopy(geometry_policy),
            "mode": "saved_complex_replay" if replay is not None else "local_complex",
            "new_model_inference": replay is None,
        }

    monkeypatch.setattr(screening, "evaluate_selected", evaluate)

    def prepare(*, live=False, **configuration):
        values = {"strategy": "harness", "batch_size": 1, "max_evaluations": 2}
        values.update(configuration)
        return screening.prepare_screening(
            tmp_path / "synthetic-monomer",
            SelectionConfig(**values),
            feedback=None if live else feedback,
            runtime={"fixture": "synthetic-runtime"} if live else None,
            artifacts=tmp_path / "sessions",
        )

    return SimpleNamespace(
        prepare=prepare,
        calls=calls,
        source=source,
        feedback=feedback,
        validations=validations,
        evaluator=evaluate,
    )


def submission(request, candidate_id="synthetic-1", *, stop=False):
    candidates = request["payload"]["visible_state"]["candidates"]
    candidate = next(row for row in candidates if row["candidate_id"] == candidate_id)
    return {
        "request_sha256": request["request_sha256"],
        "decision": {
            "action": "stop" if stop else "evaluate",
            "candidate_ids": [] if stop else [candidate_id],
            "reason": "Synthetic integration choice." if not stop else "User requested stop.",
            "evidence": []
            if stop
            else [
                {
                    "candidate_id": candidate_id,
                    "field": "pre.monomer_plddt",
                    "value": candidate["pre"]["monomer_plddt"],
                }
            ],
        },
        "actor": {"harness": "synthetic-test", "model": None, "agent_id": None},
    }


def test_observe_persists_exact_request_and_excludes_hidden_results(executor):
    directory = executor.prepare()
    request = screening.observe_screening(directory)
    assert request["request_sha256"] == canonical_sha256(request["payload"])
    assert request["response_schema"]["required"] == ["request_sha256", "decision", "actor"]
    assert request["response_schema"]["properties"]["request_sha256"]["enum"] == [
        request["request_sha256"]
    ]
    assert read_json(directory / "steps/0000/request.json") == request
    assert screening.observe_screening(directory) == request
    assert screening.run_screening(directory) == request
    assert executor.validations == [True]
    assert executor.calls == []
    assert all("post" not in row for row in request["payload"]["visible_state"]["candidates"])
    assert "/private/" not in str(request["payload"])
    assert "UNSELECTED_COMPLEX_RESULT" not in str(request["payload"])


def test_identical_submission_is_idempotent_before_and_after_next_observe(executor):
    directory = executor.prepare()
    request = screening.observe_screening(directory)
    proposal = submission(request)
    result = screening.apply_screening(directory, proposal)
    assert result["status"] == "applied"
    assert result["candidate_ids"] == ["synthetic-1"]
    assert len(result["observations"]) == 1
    assert screening.apply_screening(directory, deepcopy(proposal))["status"] == "already_applied"
    next_request = screening.observe_screening(directory)
    assert next_request["request_sha256"] != request["request_sha256"]
    assert screening.apply_screening(directory, deepcopy(proposal))["status"] == "already_applied"
    assert len(executor.calls) == 1
    candidates = next_request["payload"]["visible_state"]["candidates"]
    assert [row["candidate_id"] for row in candidates if "post" in row] == ["synthetic-1"]
    assert "UNSELECTED_COMPLEX_RESULT" not in str(next_request)


def test_stale_and_conflicting_requests_are_rejected_without_execution(executor):
    directory = executor.prepare()
    request = screening.observe_screening(directory)
    proposal = submission(request)
    stale = deepcopy(proposal)
    stale["request_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="stale"):
        screening.apply_screening(directory, stale)
    screening.apply_screening(directory, proposal)
    conflict = deepcopy(proposal)
    conflict["decision"]["reason"] = "Changed decision for a completed request."
    with pytest.raises(ValueError, match="different submission"):
        screening.apply_screening(directory, conflict)
    assert len(executor.calls) == 1
    assert len(list((directory / "rejections").glob("*.json"))) == 2


@pytest.mark.parametrize(
    "invalid", ["unknown", "duplicates", "empty", "wrong-evidence", "hidden-post"]
)
def test_invalid_decisions_cannot_start_evaluation(executor, invalid):
    directory = executor.prepare(batch_size=2)
    proposal = submission(screening.observe_screening(directory))
    decision = proposal["decision"]
    if invalid == "unknown":
        decision["candidate_ids"] = ["unknown"]
    elif invalid == "duplicates":
        decision["candidate_ids"] *= 2
    elif invalid == "empty":
        decision["candidate_ids"] = []
    elif invalid == "wrong-evidence":
        decision["evidence"][0]["value"] += 1
    else:
        decision["evidence"][0].update(field="post.iptm", value=0.223457)
    with pytest.raises(ValueError):
        screening.apply_screening(directory, proposal)
    assert executor.calls == []
    assert not (directory / "steps/0000/receipt.json").exists()


@pytest.mark.parametrize("status", ["failed", "running"])
def test_incomplete_execution_never_retries_automatically(executor, monkeypatch, status):
    directory = executor.prepare()
    proposal = submission(screening.observe_screening(directory))
    attempts = []

    def failure(*args, **kwargs):
        attempts.append(True)
        args[2].mkdir(parents=True, exist_ok=True)
        (args[2] / "partial.log").write_text("Synthetic partial output.\n")
        raise RuntimeError("synthetic interrupted evaluator")

    monkeypatch.setattr(screening, "evaluate_selected", failure)
    with pytest.raises(RuntimeError, match="interrupted"):
        screening.apply_screening(directory, proposal)
    receipt_path = directory / "steps/0000/receipt.json"
    receipt = read_json(receipt_path)
    assert receipt["status"] == "failed"
    assert receipt["tool_wall_seconds"] >= 0
    if status == "running":
        receipt["status"] = "running"
        write_json(receipt_path, receipt)
    for action in [
        lambda: screening.observe_screening(directory),
        lambda: screening.run_screening(directory),
        lambda: screening.apply_screening(directory, proposal),
    ]:
        with pytest.raises(ValueError):
            action()
    assert attempts == [True]
    assert (directory / "steps/0000/tools/partial.log").read_text() == "Synthetic partial output.\n"


def test_pending_request_tampering_is_detected_before_execution(executor):
    directory = executor.prepare()
    request = screening.observe_screening(directory)
    saved = deepcopy(request)
    saved["payload"]["visible_state"]["candidates"][0]["pre"]["monomer_plddt"] = 0
    write_json(directory / "steps/0000/request.json", saved)
    with pytest.raises(ValueError, match="request"):
        screening.apply_screening(directory, submission(request))
    assert executor.calls == []


@pytest.mark.parametrize(
    "artifact",
    ["request.json", "submission.json", "result.json", "receipt.json", "extra", "missing"],
)
def test_completed_step_tampering_is_detected_before_another_request(executor, artifact):
    directory = executor.prepare()
    request = screening.observe_screening(directory)
    screening.apply_screening(directory, submission(request))
    step = directory / "steps/0000"
    if artifact == "extra":
        (step / "unexpected.log").write_text("Unrecorded output.\n")
    elif artifact == "missing":
        (step / "result.json").unlink()
    elif artifact == "receipt.json":
        receipt = read_json(step / artifact)
        receipt["tool_wall_seconds"] += 1.0
        write_json(step / artifact, receipt)
    else:
        path = step / artifact
        path.write_text(path.read_text() + " ")
    with pytest.raises(ValueError):
        screening.observe_screening(directory)
    assert len(executor.calls) == 1


def test_changed_source_is_rejected(executor):
    directory = executor.prepare()
    executor.source.write_text("Changed synthetic source.\n")
    with pytest.raises(ValueError, match="source"):
        screening.observe_screening(directory)
    assert executor.calls == []


@pytest.mark.parametrize("strategy", ["fixed", "heuristic"])
def test_baselines_use_recorded_observe_apply_boundary(executor, monkeypatch, strategy):
    directory = executor.prepare(strategy=strategy)
    original = screening.apply_screening
    calls = []

    def apply(folder, proposal):
        calls.append(deepcopy(proposal))
        return original(folder, proposal)

    monkeypatch.setattr(screening, "apply_screening", apply)
    report = screening.run_screening(directory)
    assert report["status"] == "completed"
    assert report["evaluated_count"] == 2
    assert report["selection_order"] == (
        ["synthetic-1", "synthetic-2"] if strategy == "fixed" else ["synthetic-3", "synthetic-2"]
    )
    assert [proposal["decision"]["action"] for proposal in calls] == [
        "evaluate",
        "evaluate",
        "stop",
    ]
    assert report["decisions"] == calls
    assert report["acceptance"] is None
    assert report["binding_validated"] is False
    assert report["new_model_inference"] is False
    assert report["actor_metadata_verified"] is False


def test_quota_stops_on_observe_and_completed_history_is_reused(executor):
    directory = executor.prepare(max_evaluations=1)
    screening.apply_screening(directory, submission(screening.observe_screening(directory)))
    report = screening.observe_screening(directory)
    assert report["status"] == "completed"
    assert report["stop_reason"] == "candidate_limit_reached"
    assert report["evaluated_count"] == 1
    assert report["decisions"][-1]["actor"]["harness"] == "executor"
    assert read_json(directory / "report.json") == report
    before = {path: path.read_bytes() for path in directory.rglob("*") if path.is_file()}
    assert screening.observe_screening(directory) == report
    assert screening.run_screening(directory) == report
    assert all(path.read_bytes() == content for path, content in before.items())
    assert len(executor.calls) == 1
    (directory / "report.json").write_text("Changed completion report.\n")
    with pytest.raises(ValueError):
        screening.observe_screening(directory)


def test_explicit_stop_creates_report_without_evaluation(executor):
    directory = executor.prepare()
    proposal = submission(screening.observe_screening(directory), stop=True)
    assert screening.apply_screening(directory, proposal)["status"] == "applied"
    report = screening.observe_screening(directory)
    assert report["evaluated_count"] == 0
    assert report["stop_reason"] == "User requested stop."
    assert report["new_model_inference"] is False
    assert executor.calls == []


def test_live_budget_overrun_stops_before_a_second_tool_call(executor, monkeypatch):
    directory = executor.prepare(live=True, tool_wall_budget_seconds=2.0)
    values = iter([10.0, 13.0])
    monkeypatch.setattr(screening, "time", SimpleNamespace(monotonic=lambda: next(values, 13.0)))
    screening.apply_screening(directory, submission(screening.observe_screening(directory)))
    report = screening.observe_screening(directory)
    assert report["stop_reason"] == "tool_wall_budget_exhausted"
    assert report["tool_wall_seconds"] == 3.0
    assert report["evaluated_count"] == 1
    assert report["new_model_inference"] is True
    assert executor.calls == [{"ids": ["synthetic-1"], "remaining": 2.0, "replay": False}]


def test_replay_rejects_live_time_budget(executor):
    with pytest.raises(ValueError, match="replay"):
        executor.prepare(tool_wall_budget_seconds=2.0)
    assert executor.calls == []


def test_cli_prepare_observe_apply_and_completion_exchange(executor, tmp_path, capsys):
    assert (
        main(
            [
                "screen",
                "prepare",
                str(tmp_path / "synthetic-monomer"),
                "--strategy",
                "harness",
                "--feedback",
                str(executor.feedback),
                "--batch-size",
                "2",
                "--max-evaluations",
                "1",
                "--artifacts",
                str(tmp_path / "cli-sessions"),
            ]
        )
        == 0
    )
    directory = Path(capsys.readouterr().out.strip())
    assert directory.is_dir()
    assert main(["screen", "observe", str(directory)]) == 0
    request = json.loads(capsys.readouterr().out)
    decision = tmp_path / "host-decision.json"
    write_json(decision, submission(request))
    assert main(["screen", "apply", str(directory), "--decision", str(decision)]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "applied"
    assert main(["screen", "run", str(directory)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "completed"
    assert report["selection_order"] == ["synthetic-1"]
    assert len(executor.calls) == 1


def test_cli_stdin_submission_preserves_text_and_is_idempotent(executor, monkeypatch, capsys):
    directory = executor.prepare(max_evaluations=1)
    proposal = submission(screening.observe_screening(directory))
    proposal["decision"]["reason"] = "诊断：literal '$HOME' and `command`\nsecond line"
    for status in ["applied", "already_applied"]:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(proposal)))
        assert main(["screen", "apply", str(directory), "--decision", "-"]) == 0
        assert json.loads(capsys.readouterr().out)["status"] == status
    stored = read_json(directory / "steps" / "0000" / "submission.json")
    assert stored["decision"]["reason"] == proposal["decision"]["reason"]
    assert len(executor.calls) == 1


@pytest.mark.parametrize("text", ["not JSON", "[]"])
def test_cli_invalid_stdin_never_executes(executor, monkeypatch, text):
    directory = executor.prepare()
    screening.observe_screening(directory)
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    with pytest.raises(SystemExit) as error:
        main(["screen", "apply", str(directory), "--decision", "-"])
    assert error.value.code == 2
    assert executor.calls == []

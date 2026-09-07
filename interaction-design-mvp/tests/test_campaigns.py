"""Synthetic controller fixtures are never evidence about real binder quality."""

from __future__ import annotations

import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from interaction_design.assets import sha256_file
from interaction_design.campaign_tools import LocalCampaignTools, ReplayTools, load_feedback
from interaction_design.campaigns import prepare_campaign, run_campaign
from interaction_design.cli import main
from interaction_design.decisions import (
    Decision,
    DecisionConfig,
    choose_decision,
    frontier,
    objective_vector,
    validate_decision,
    visible_state,
)
from interaction_design.evaluation.complex import default_policy
from interaction_design.evaluation.feedback import feedback_policy_path
from interaction_design.evaluation.jobs import file_record, read_json
from interaction_design.persistence import write_json
from interaction_design.specs import InteractionDesignSpec


def observation(seed, *, iptm=0.8, contacted=4, clashes=0, rmsd=1.0):
    return {
        "candidate_id": f"synthetic-{seed}",
        "generation_seed": seed,
        "confidence": {"iptm": iptm},
        "geometry": {
            "predicted": {
                "hotspot_count": 4,
                "hotspots_contacted": contacted,
                "clash_residue_pair_count": clashes,
            }
        },
        "design_consistency": {"binder_rmsd_after_target_alignment_angstrom": rmsd},
        "model_policy": default_policy(),
        "acceptance": None,
        "binding_validated": False,
        "fixture_synthetic": True,
    }


@pytest.fixture
def feedback(tmp_path):
    folder = tmp_path / "synthetic-feedback"
    rows = [observation(1)] + [
        observation(i, iptm=0.7, contacted=3, clashes=1, rmsd=2) for i in range(2, 5)
    ]
    write_json(
        folder / "observations.json",
        {
            "candidate_count": 4,
            "observations": rows,
            "policy": read_json(feedback_policy_path()),
            "model_policy": default_policy(),
            "inputs": [],
        },
    )
    write_json(folder / "status.json", {"status": "completed"})
    write_json(
        folder / "manifest.json",
        {"artifacts": [file_record(p, folder) for p in sorted(folder.iterdir())]},
    )
    return folder


def replay_campaign(tmp_path, feedback, **options):
    return prepare_campaign(
        DecisionConfig(first_seed=1, batch_size=1, max_rounds=4, **options),
        artifacts=tmp_path / "campaigns",
        feedback=feedback,
        replay=True,
    )


def test_pareto_keeps_tradeoffs_and_collapses_exact_ties():
    a = observation(1)
    dominated = observation(2, iptm=0.7, contacted=2, clashes=1, rmsd=2)
    tradeoff = observation(3, iptm=0.9, contacted=2)
    tied = observation(4)
    assert {r["candidate_id"] for r in frontier([tied, tradeoff, dominated, a])} == {
        "synthetic-1",
        "synthetic-3",
    }
    state = visible_state([a], [{"observations": [tied]}], 1.0)
    assert state["consecutive_rounds_without_frontier_extension"] == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("iptm", float("nan")),
        ("iptm", 1.1),
        ("contacted", 5),
        ("clashes", -1),
        ("rmsd", float("inf")),
    ],
)
def test_decisions_reject_invalid_observations(field, value):
    with pytest.raises(ValueError):
        objective_vector(observation(1, **{field: value}))


def test_guard_rejects_repeated_seeds_changed_schedule_and_exhausted_budget():
    config = DecisionConfig(first_seed=1, batch_size=1, tool_wall_budget_seconds=10.0)
    state = visible_state([], [], 10.0)
    assert choose_decision(config, state).reason == "tool_wall_budget_exhausted"
    with pytest.raises(ValueError, match="limits"):
        validate_decision(Decision(action="sample", seeds=[1], reason="test"), config, state)
    state = visible_state([observation(1)], [], 0)
    with pytest.raises(ValueError, match="repeat"):
        validate_decision(Decision(action="sample", seeds=[1], reason="test"), config, state)
    with pytest.raises(ValueError, match="frozen"):
        validate_decision(Decision(action="sample", seeds=[99], reason="test"), config, state)
    with pytest.raises(ValidationError):
        DecisionConfig(batch_size=True)


def test_feedback_stops_on_stagnation_while_fixed_continues(tmp_path, feedback):
    paths = [replay_campaign(tmp_path, feedback, strategy=s) for s in ["fixed", "feedback"]]
    reports = [read_json(run_campaign(p)) for p in paths]
    assert [r["newly_observed_candidate_count"] for r in reports] == [4, 2]
    assert reports[1]["decisions"][-1]["reason"] == "no_new_diagnostic_tradeoff"
    assert reports[0]["decisions"][-1]["reason"] == "round_limit_reached"
    for report in reports:
        assert report["new_model_inference"] is False
        assert report["acceptance"] is None
    for i in range(4):
        context = read_json(paths[0] / "steps" / f"{i:04d}" / "intent.json")["visible_state"]
        assert context["observed_seeds"] == list(range(1, i + 1))


def test_completed_campaign_is_verified_and_reused_without_tools(tmp_path, feedback):
    path = replay_campaign(tmp_path, feedback)
    report = run_campaign(path)
    before = {p: sha256_file(p) for p in path.rglob("*") if p.is_file()}

    class Unexpected:
        def sample(self, *args):
            pytest.fail("completed campaign must not call tools")

    assert run_campaign(path, tools=Unexpected()) == report
    assert before == {p: sha256_file(p) for p in before}
    report.write_text(report.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        run_campaign(path)


def test_policy_cannot_mutate_executor_context_and_resume_skips_finished_step(tmp_path, feedback):
    path = replay_campaign(tmp_path, feedback)

    def policy(config, state):
        if state["completed_rounds"] == 0:
            return choose_decision(config, state)
        config.first_seed = 99
        state["observed_seeds"] = []
        return Decision(action="sample", seeds=[100], reason="invalid external proposal")

    with pytest.raises(ValueError, match="frozen"):
        run_campaign(path, policy=policy)
    first_receipt = path / "steps/0000/receipt.json"
    before = first_receipt.read_bytes()
    assert read_json(path / "status.json")["status"] == "decision_rejected"
    assert list((path / "rejections").glob("*.json"))
    report = read_json(run_campaign(path))
    assert report["newly_observed_candidate_count"] == 4
    assert first_receipt.read_bytes() == before


@pytest.mark.parametrize("failure", ["raise", "duplicate", "protocol"])
def test_failed_tool_call_is_recorded_and_never_automatically_repeated(tmp_path, feedback, failure):
    path = replay_campaign(tmp_path, feedback)
    original = ReplayTools(load_feedback(feedback))

    class Broken:
        calls = 0

        def sample(self, seeds, directory, remaining):
            self.calls += 1
            if failure == "raise":
                (directory / "partial.log").write_text("synthetic interrupted work")
                raise RuntimeError("synthetic failure")
            result = deepcopy(original.sample(seeds, directory, remaining))
            if failure == "duplicate":
                result["observations"] *= 2
            else:
                result["model_policy"]["seed"] = 999
            return result

    broken = Broken()
    with pytest.raises((ValueError, RuntimeError)):
        run_campaign(path, tools=broken)
    receipt = read_json(path / "steps/0000/receipt.json")
    assert receipt["status"] == "failed" and receipt["tool_wall_seconds"] >= 0
    with pytest.raises(ValueError, match="inspect"):
        run_campaign(path, tools=broken)
    assert broken.calls == 1


def test_unfinished_step_blocks_duplicate_execution(tmp_path, feedback):
    path = replay_campaign(tmp_path, feedback)
    write_json(path / "steps/0000/receipt.json", {"status": "running"})
    with pytest.raises(ValueError, match="unfinished"):
        run_campaign(path)


def test_stop_receipt_can_recover_missing_final_report(tmp_path, feedback):
    path = replay_campaign(tmp_path, feedback)
    run_campaign(path)
    (path / "completed.json").unlink()
    (path / "report.json").unlink()
    assert run_campaign(path).is_file()


def test_frozen_sources_and_strategy_are_checked(tmp_path, feedback):
    path = replay_campaign(tmp_path, feedback)
    strategy = path / "strategy.json"
    strategy.write_text(strategy.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        run_campaign(path)
    path2 = replay_campaign(tmp_path, feedback)
    source = feedback / "observations.json"
    source.write_text(source.read_text() + " ")
    with pytest.raises(ValueError, match="source changed"):
        run_campaign(path2)


def test_replay_cli_and_budget_boundary(tmp_path, feedback):
    assert (
        main(
            [
                "campaign",
                "replay",
                str(feedback),
                "--batch-size",
                "1",
                "--strategy",
                "feedback",
                "--artifacts",
                str(tmp_path / "cli"),
            ]
        )
        == 0
    )
    with pytest.raises(ValueError, match="simulate"):
        prepare_campaign(
            DecisionConfig(first_seed=1, batch_size=1, tool_wall_budget_seconds=10.0),
            feedback=feedback,
            replay=True,
        )


@pytest.fixture
def local_inputs(tmp_path):
    reference = tmp_path / "synthetic-reference.pdb"
    reference.write_text("synthetic fixture; not submitted to a predictor")
    task = InteractionDesignSpec.model_validate(
        {
            "name": "synthetic-campaign",
            "model": "odesign_base_prot_flex",
            "design_modality": "protein",
            "reference_structure": str(reference),
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
                    "segments": [{"kind": "fixed", "chain": "B", "start": 1, "end": 8}],
                },
            ],
            "hotspots": [{"chain": "B", "residue": 2}],
            "evaluation": {"binder_molecule": "binder"},
        }
    )
    model_dirs = []
    for name in ["model_revision", "esmc_revision"]:
        path = tmp_path / default_policy()[name]
        write_json(path / "config.json", {})
        model_dirs.append(str(path))
    ccd = tmp_path / "synthetic-ccd.pkl"
    ccd.write_text("not unpickled")
    runtime = {
        "generation": {
            "odesign_repo": str(tmp_path),
            "data_root": str(tmp_path),
            "checkpoint_root": str(tmp_path),
            "python_executable": sys.executable,
        },
        "complex": {
            "python": sys.executable,
            "model_dir": model_dirs[0],
            "esmc_dir": model_dirs[1],
            "ccd": str(ccd),
        },
    }
    return task, runtime


def test_local_adapter_preserves_constraints_and_passes_remaining_time(
    tmp_path, feedback, local_inputs, monkeypatch
):
    task, runtime = local_inputs
    before = task.model_dump(mode="json")
    import interaction_design.campaign_tools as module

    seen = {}

    class FakeWorkflow:
        def __init__(self, generator):
            assert generator.executor.timeout_seconds == 10.0

        def run(self, spec, *, evaluate):
            seen["task"] = spec.model_dump(mode="json")
            assert evaluate is False
            return SimpleNamespace(run_dir=tmp_path / "synthetic-generation")

    def prepare(run, *, artifacts, budget_seconds):
        assert budget_seconds == 6.0
        return tmp_path / "synthetic-complex"

    monkeypatch.setattr(module, "DesignWorkflow", FakeWorkflow)
    monkeypatch.setattr(module, "prepare_complex_batch", prepare)
    monkeypatch.setattr(module, "run_complex_batch", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "write_interface_feedback", lambda *args, **kwargs: feedback)
    ticks = iter([10.0, 14.0])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    tools = LocalCampaignTools(before, runtime)
    tools.sample([1, 2, 3, 4], tmp_path / "step", 10.0)
    assert seen["task"]["generation"].pop("seeds") == [1, 2, 3, 4]
    expected = deepcopy(before)
    expected["generation"].pop("seeds")
    assert seen["task"] == expected
    assert task.model_dump(mode="json") == before


def test_live_budget_stops_after_completed_tool_and_records_cost(
    tmp_path, feedback, local_inputs, monkeypatch
):
    task, runtime = local_inputs
    path = prepare_campaign(
        DecisionConfig(first_seed=1, batch_size=1, max_rounds=4, tool_wall_budget_seconds=1.0),
        task=task,
        runtime=runtime,
        artifacts=tmp_path / "live-fixture",
    )
    import interaction_design.campaigns as module

    ticks = iter([0.0, 1.2, 1.2])
    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: next(ticks)))
    replay = ReplayTools(load_feedback(feedback))

    class SyntheticTools:
        def sample(self, seeds, directory, remaining):
            assert remaining == 1.0
            return replay.sample(seeds, directory, None)

    report = read_json(run_campaign(path, tools=SyntheticTools()))
    assert report["decisions"][-1]["reason"] == "tool_wall_budget_exhausted"
    assert report["state"]["tool_wall_seconds_spent"] == 1.2
    assert report["newly_observed_candidate_count"] == 1

from __future__ import annotations

import copy
import json

import pytest

from interaction_design.adaptive import (
    apply_adaptive,
    history_adaptive,
    observe_adaptive,
    prepare_adaptive,
    reflect_adaptive,
    report_adaptive,
    run_adaptive,
    validate_input,
)
from interaction_design.persistence import write_json


@pytest.fixture
def data():
    return {
        "schema_version": "molclaw-adaptive-input-v1",
        "purpose": "development",
        "source": {"dataset": "synthetic"},
        "pools": [
            {
                "pool_id": f"p{p}",
                "target": f"target{p}",
                "candidates": [
                    {
                        "candidate_id": f"c{i}",
                        "source_id": f"uuid{p}_{i}",
                        "label": i == 0,
                        "predictions": [
                            {
                                "seed": seed,
                                "iptm": 0.2,
                                "ipsae": 0.8 if i == 0 else 0.1,
                                "sc_dockq": None,
                            }
                            for seed in range(5)
                        ],
                    }
                    for i in range(3)
                ],
            }
            for p in range(2)
        ],
    }


def prepare(tmp_path, data, policy="harness", budget=3):
    source = write_json(tmp_path / "input.json", data)
    output = tmp_path / "session"
    prepare_adaptive(source, output, quota=2, budget_per_candidate=budget, policy=policy)
    return output


def evaluate(observation, cid="c0"):
    return {
        "request_sha256": observation["request_sha256"],
        "action": "evaluate",
        "candidate_id": cid,
        "reason": "test acquisition",
    }


def select(observation, ids=None):
    return {
        "request_sha256": observation["request_sha256"],
        "action": "select",
        "candidate_ids": ["c0", "c1"] if ids is None else ids,
        "reason": "test selection",
    }


def test_public_projection_does_not_expose_future_or_outcomes(tmp_path, data):
    session = prepare(tmp_path, data)
    observation = observe_adaptive(session, "p0")
    serialized = json.dumps(observation)
    assert all(word not in serialized for word in ("target0", "uuid", "label", "source"))
    assert all(len(item["predictions"]) == 1 for item in observation["candidates"])
    assert observation["budget"] == {
        "limit": 9,
        "used": 3,
        "remaining": 6,
        "unit": "prediction_query",
    }


def test_hidden_values_cannot_change_initial_observation(data):
    from interaction_design.adaptive import _initial

    changed = copy.deepcopy(data["pools"][0])
    changed["target"] = "hidden"
    for candidate in changed["candidates"]:
        candidate["label"] = not candidate["label"]
        candidate["source_id"] = "hidden"
        for row in candidate["predictions"][1:]:
            row["ipsae"] = 0.999
    protocol = {"quota": 2, "budget_per_candidate": 3, "session_id": "test"}
    assert _initial(changed, protocol) == _initial(data["pools"][0], protocol)


def test_acquisition_reveals_next_seed_and_retry_does_not_charge(tmp_path, data):
    session = prepare(tmp_path, data)
    before = observe_adaptive(session, "p0")
    action = evaluate(before)
    apply_adaptive(session, "p0", action)
    after = observe_adaptive(session, "p0")
    assert after["budget"]["used"] == 4
    assert [row["seed"] for row in after["candidates"][0]["predictions"]] == [0, 1]
    assert after["candidates"][1:] == before["candidates"][1:]
    assert apply_adaptive(session, "p0", action)["status"] == "already_applied"
    assert observe_adaptive(session, "p0") == after
    stale = evaluate(before, "c1")
    with pytest.raises(ValueError, match="stale"):
        apply_adaptive(session, "p0", stale)
    assert observe_adaptive(session, "p0") == after


def test_another_session_cannot_reuse_action(tmp_path, data):
    first = prepare(tmp_path / "first", data)
    second = prepare(tmp_path / "second", data)
    with pytest.raises(ValueError, match="foreign"):
        apply_adaptive(second, "p0", evaluate(observe_adaptive(first, "p0")))


def test_exhausted_budget_cannot_reveal(tmp_path, data):
    session = prepare(tmp_path, data, budget=1)
    before = observe_adaptive(session, "p0")
    with pytest.raises(ValueError, match="budget exhausted"):
        apply_adaptive(session, "p0", evaluate(before))
    assert observe_adaptive(session, "p0") == before
    apply_adaptive(session, "p0", select(before))


def test_all_seeds_stop_and_missing_score_still_costs_query(tmp_path, data):
    data["pools"][0]["candidates"][0]["predictions"][1]["ipsae"] = None
    session = prepare(tmp_path, data, budget=5)
    for _ in range(4):
        apply_adaptive(session, "p0", evaluate(observe_adaptive(session, "p0")))
    final = observe_adaptive(session, "p0")
    assert final["budget"]["used"] == 7
    assert final["candidates"][0]["predictions"][1]["ipsae"] is None
    with pytest.raises(ValueError, match="already observed"):
        apply_adaptive(session, "p0", evaluate(final))


@pytest.mark.parametrize("ids", [["c0"], ["c0", "c0"], ["c0", "missing"], ["c0", 1], "c0"])
def test_invalid_selection_is_atomic(tmp_path, data, ids):
    session = prepare(tmp_path, data)
    ledger = (session / "ledger.json").read_bytes()
    with pytest.raises(ValueError):
        apply_adaptive(session, "p0", select(observe_adaptive(session, "p0"), ids))
    assert (session / "ledger.json").read_bytes() == ledger


def test_all_pools_required_and_report_reproduces(tmp_path, data):
    session = prepare(tmp_path, data)
    for pid in ("p0", "p1"):
        with pytest.raises(ValueError, match="every pool"):
            report_adaptive(session)
        assert not (session / "report.json").exists()
        apply_adaptive(session, pid, select(observe_adaptive(session, pid)))
    report = report_adaptive(session)
    assert report["total_hits"] == 2
    assert report["total_selected"] == 4
    assert report["macro_precision_at_quota"] == 0.5
    assert report["total_prediction_queries"] == 6
    assert report_adaptive(session) == report


def test_tampered_result_and_private_data_are_rejected(tmp_path, data):
    session = prepare(tmp_path, data)
    apply_adaptive(session, "p0", evaluate(observe_adaptive(session, "p0")))
    ledger = json.loads((session / "ledger.json").read_text())
    ledger["events"]["p0"][0]["result_sha256"] = "changed"
    write_json(session / "ledger.json", ledger)
    with pytest.raises(ValueError, match="event result"):
        observe_adaptive(session, "p0")
    (session / "private-input.json").write_text("{}")
    with pytest.raises(ValueError, match="immutable file"):
        observe_adaptive(session, "p0")


def test_event_write_failure_can_retry_without_double_charge(tmp_path, data, monkeypatch):
    import interaction_design.adaptive as module

    session = prepare(tmp_path, data)
    initial = observe_adaptive(session, "p0")
    action = evaluate(initial)
    original = module.write_json

    def fail(*args, **kwargs):
        raise OSError("simulated write interruption")

    monkeypatch.setattr(module, "write_json", fail)
    with pytest.raises(OSError):
        apply_adaptive(session, "p0", action)
    assert observe_adaptive(session, "p0") == initial
    monkeypatch.setattr(module, "write_json", original)
    apply_adaptive(session, "p0", action)
    assert observe_adaptive(session, "p0")["budget"]["used"] == 4


@pytest.mark.parametrize("policy", ["uniform", "fixed_top", "boundary", "single"])
def test_baselines_finish_resume_and_respect_declared_cost(tmp_path, data, policy):
    session = prepare(tmp_path, data, policy=policy)
    run_adaptive(session)
    report = report_adaptive(session)
    assert report["total_prediction_queries"] == (6 if policy == "single" else 18)
    before = (session / "ledger.json").read_bytes()
    run_adaptive(session)
    assert (session / "ledger.json").read_bytes() == before
    assert report_adaptive(session) == report
    if policy == "uniform":
        assert all(set(row["evaluation_counts"].values()) == {3} for row in report["by_target"])


def test_runner_rejects_harness_and_preserves_partial_baseline(tmp_path, data):
    session = prepare(tmp_path, data)
    with pytest.raises(ValueError, match="requires host"):
        run_adaptive(session)
    apply_adaptive(session, "p0", select(observe_adaptive(session, "p0")))
    with pytest.raises(ValueError, match="already committed"):
        apply_adaptive(session, "p0", evaluate(observe_adaptive(session, "p0")))


@pytest.mark.parametrize(
    ("policy", "wrong_candidate"),
    [("uniform", "c1"), ("fixed_top", "c1"), ("boundary", "c0"), ("single", "c0")],
)
def test_public_apply_rejects_deviation_from_declared_baseline(
    tmp_path, data, policy, wrong_candidate
):
    session = prepare(tmp_path, data, policy=policy)
    before = observe_adaptive(session, "p0")
    ledger_bytes = (session / "ledger.json").read_bytes()
    with pytest.raises(ValueError, match="declared baseline policy"):
        apply_adaptive(session, "p0", evaluate(before, wrong_candidate))
    assert (session / "ledger.json").read_bytes() == ledger_bytes
    assert observe_adaptive(session, "p0") == before


@pytest.mark.parametrize("policy", ["uniform", "fixed_top", "boundary"])
def test_baseline_cannot_select_early_through_public_apply(tmp_path, data, policy):
    session = prepare(tmp_path, data, policy=policy)
    with pytest.raises(ValueError, match="declared baseline policy"):
        apply_adaptive(session, "p0", select(observe_adaptive(session, "p0")))


def test_single_baseline_cannot_override_common_final_ranker(tmp_path, data):
    session = prepare(tmp_path, data, policy="single")
    before = observe_adaptive(session, "p0")
    with pytest.raises(ValueError, match="declared baseline policy"):
        apply_adaptive(session, "p0", select(before, ["c1", "c2"]))


@pytest.mark.parametrize("policy", ["uniform", "fixed_top", "boundary", "single"])
def test_declared_baseline_allows_public_action_and_resumes_without_reallocation(
    tmp_path, data, policy
):
    from interaction_design.adaptive_policies import choose_action

    session = prepare(tmp_path, data, policy=policy)
    action = choose_action(observe_adaptive(session, "p0"), policy)
    action["reason"] = "Equivalent declared action, with independently written explanation."
    assert apply_adaptive(session, "p0", action)["status"] == "applied"
    prefix = json.loads((session / "ledger.json").read_text())["events"]["p0"]
    run_adaptive(session)
    events = json.loads((session / "ledger.json").read_text())["events"]["p0"]
    assert events[: len(prefix)] == prefix
    assert apply_adaptive(session, "p0", action)["status"] == "already_applied"
    report = report_adaptive(session)
    assert report["total_prediction_queries"] == (6 if policy == "single" else 18)
    assert report["policy"] == policy


@pytest.mark.parametrize(
    ("policy", "wrong_candidate"),
    [("uniform", "c1"), ("fixed_top", "c1"), ("boundary", "c0"), ("single", "c0")],
)
def test_replay_rejects_valid_hash_event_that_violates_declared_baseline(
    tmp_path, data, policy, wrong_candidate
):
    from interaction_design.adaptive import _transition

    session = prepare(tmp_path, data, policy=policy)
    before = observe_adaptive(session, "p0")
    action = evaluate(before, wrong_candidate)
    # A hash-valid event allowed for a harness must not be accepted under a fixed
    # declared policy, even if introduced without using the public apply path.
    forged_result = _transition(before, data["pools"][0], action, "harness")
    ledger = json.loads((session / "ledger.json").read_text())
    ledger["events"]["p0"].append(
        {"submission": action, "result_sha256": forged_result["request_sha256"]}
    )
    write_json(session / "ledger.json", ledger)
    with pytest.raises(ValueError, match="declared baseline policy"):
        observe_adaptive(session, "p0")
    with pytest.raises(ValueError, match="declared baseline policy"):
        run_adaptive(session)
    with pytest.raises(ValueError, match="declared baseline policy"):
        report_adaptive(session)
    assert not (session / "report.json").exists()


@pytest.mark.parametrize("value", [True, "0.1", -0.1, 1.1, float("inf"), float("nan")])
def test_invalid_predictions_rejected(data, value):
    data["pools"][0]["candidates"][0]["predictions"][0]["ipsae"] = value
    with pytest.raises(ValueError, match="scores"):
        validate_input(data)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: d.update(purpose="external_test"),
        lambda d: d["pools"].append(copy.deepcopy(d["pools"][0])),
        lambda d: d["pools"][0]["candidates"][0].update(label=None),
        lambda d: d["pools"][0]["candidates"][0]["predictions"].pop(),
        lambda d: d["pools"][0]["candidates"][0]["predictions"][0].update(seed=True),
        lambda d: d["pools"][0]["candidates"][0]["predictions"][0].update(label=True),
        lambda d: d["pools"][0]["candidates"].append(copy.deepcopy(d["pools"][0]["candidates"][0])),
    ],
)
def test_input_invariants(data, mutation):
    mutation(data)
    with pytest.raises(ValueError):
        validate_input(data)


def planned(observation):
    action = evaluate(observation)
    action["plan"] = {
        "question": "Does c0 retain its observed score?",
        "expectation": "Another seed may contradict the initial high score.",
        "evidence": [{"candidate_id": "c0", "seed": 0, "field": "ipsae", "value": 0.8}],
    }
    return action


def reflection(observation):
    return {
        "request_sha256": observation["request_sha256"],
        "outcome": "inconclusive",
        "summary": "Two identical scores do not establish calibration.",
        "evidence": [{"candidate_id": "c0", "seed": 1, "field": "ipsae", "value": 0.8}],
    }


def test_planned_round_requires_review_and_retries_preserve_cost(tmp_path, data):
    session = prepare(tmp_path, data)
    action = planned(observe_adaptive(session, "p0"))
    apply_adaptive(session, "p0", action)
    after = observe_adaptive(session, "p0")
    assert history_adaptive(session, "p0")["pending_reflection"]
    with pytest.raises(ValueError, match="must be an object"):
        reflect_adaptive(session, "p0", None)
    with pytest.raises(ValueError, match="pending reflection"):
        apply_adaptive(session, "p0", select(after))
    review = reflection(after)
    assert reflect_adaptive(session, "p0", review)["status"] == "reflected"
    assert observe_adaptive(session, "p0") == after
    assert not history_adaptive(session, "p0")["pending_reflection"]
    assert reflect_adaptive(session, "p0", review)["status"] == "already_reflected"
    with pytest.raises(ValueError, match="cannot be replaced"):
        reflect_adaptive(session, "p0", {**review, "summary": "replacement"})
    apply_adaptive(session, "p0", select(after))
    apply_adaptive(session, "p1", select(observe_adaptive(session, "p1")))
    report_adaptive(session)
    assert reflect_adaptive(session, "p0", review)["status"] == "already_reflected"
    assert apply_adaptive(session, "p0", action)["status"] == "already_applied"
    assert observe_adaptive(session, "p0")["budget"]["used"] == 4


@pytest.mark.parametrize(
    "field,value",
    [
        ("seed", 4),
        ("value", 0.799),
        ("value", True),
        ("field", "label"),
        ("candidate_id", "unknown"),
    ],
)
def test_plan_rejects_unseen_or_incorrect_evidence(tmp_path, data, field, value):
    session = prepare(tmp_path, data)
    before = observe_adaptive(session, "p0")
    action = planned(before)
    action["plan"]["evidence"][0][field] = value
    with pytest.raises(ValueError, match="evidence"):
        apply_adaptive(session, "p0", action)
    assert observe_adaptive(session, "p0") == before


def test_reflection_requires_new_evidence_and_current_hash(tmp_path, data):
    session = prepare(tmp_path, data)
    before = observe_adaptive(session, "p0")
    apply_adaptive(session, "p0", planned(before))
    after = observe_adaptive(session, "p0")
    for field, value, message in [("seed", 0, "newly revealed"), ("seed", 4, "visible")]:
        review = reflection(after)
        review["evidence"][0][field] = value
        with pytest.raises(ValueError, match=message):
            reflect_adaptive(session, "p0", review)
    with pytest.raises(ValueError, match="foreign reflection"):
        reflect_adaptive(session, "p0", reflection(before))
    assert history_adaptive(session, "p0")["pending_reflection"]


def test_missing_observation_can_be_reflected_without_becoming_zero(tmp_path, data):
    data["pools"][0]["candidates"][0]["predictions"][1]["ipsae"] = None
    session = prepare(tmp_path, data)
    apply_adaptive(session, "p0", planned(observe_adaptive(session, "p0")))
    after = observe_adaptive(session, "p0")
    review = reflection(after)
    review["evidence"][0]["value"] = None
    reflect_adaptive(session, "p0", review)
    row = history_adaptive(session, "p0")["rounds"][0]
    assert row["revealed_prediction"]["ipsae"] is None
    assert row["budget"]["used"] == 4


def test_required_review_cannot_be_omitted_or_used_by_baseline(tmp_path, data):
    source = write_json(tmp_path / "input.json", data)
    session = tmp_path / "session"
    with pytest.raises(ValueError, match="only for the harness"):
        prepare_adaptive(source, session, quota=2, policy="uniform", require_review=True)
    prepare_adaptive(source, session, quota=2, require_review=True)
    before = observe_adaptive(session, "p0")
    with pytest.raises(ValueError, match="requires an evaluation plan"):
        apply_adaptive(session, "p0", evaluate(before))
    apply_adaptive(session, "p0", planned(before))


def test_reflection_write_failure_and_invalid_persisted_evidence(tmp_path, data, monkeypatch):
    import interaction_design.adaptive as module

    session = prepare(tmp_path, data)
    apply_adaptive(session, "p0", planned(observe_adaptive(session, "p0")))
    review = reflection(observe_adaptive(session, "p0"))
    before = (session / "ledger.json").read_bytes()
    with monkeypatch.context() as patch:

        def fail(*args):
            raise OSError("interrupted")

        patch.setattr(module, "write_json", fail)
        with pytest.raises(OSError):
            reflect_adaptive(session, "p0", review)
    assert (session / "ledger.json").read_bytes() == before
    reflect_adaptive(session, "p0", review)
    ledger = json.loads((session / "ledger.json").read_text())
    ledger["events"]["p0"][0]["reflection"]["evidence"][0]["seed"] = 4
    write_json(session / "ledger.json", ledger)
    with pytest.raises(ValueError, match="visible prediction"):
        observe_adaptive(session, "p0")


def test_export_preserves_visibility_and_never_reveals_a_report(tmp_path, data):
    from interaction_design.adaptive_export import export_adaptive

    data["pools"][0]["candidates"][0]["predictions"][4]["ipsae"] = 0.987654321
    session = prepare(tmp_path, data)
    apply_adaptive(session, "p0", planned(observe_adaptive(session, "p0")))
    before = (session / "ledger.json").read_bytes()
    output = tmp_path / "view"
    export_adaptive(session, output)
    assert (session / "ledger.json").read_bytes() == before
    for name in ("replay.json", "index.html", "ledger.json"):
        value = (output / name).read_text()
        assert all(hidden not in value for hidden in ("target0", "uuid", "0.987654321", '"label":'))
    assert not (session / "report.json").exists()
    assert not (output / "report.json").exists()
    assert not (output / "private-input.json").exists()
    assert not (output / "source.json").exists()
    with pytest.raises(ValueError, match="fresh directory"):
        export_adaptive(session, output)
    reflect_adaptive(session, "p0", reflection(observe_adaptive(session, "p0")))
    for pid in ("p0", "p1"):
        apply_adaptive(session, pid, select(observe_adaptive(session, pid)))
    export_adaptive(session, tmp_path / "committed-unrevealed")
    assert not (session / "report.json").exists()
    report = report_adaptive(session)
    done = tmp_path / "done"
    export_adaptive(session, done)
    assert json.loads((done / "report.json").read_text()) == report
    assert "0.987654321" not in (done / "replay.json").read_text()
    from interaction_design.adaptive import _digest

    manifest = json.loads((done / "manifest.json").read_text())
    assert all(_digest(done / name) == digest for name, digest in manifest["files"].items())
    report["total_hits"] = 999
    write_json(session / "report.json", report)
    with pytest.raises(ValueError, match="existing report differs"):
        export_adaptive(session, tmp_path / "tampered")


def test_html_treats_host_text_as_data(tmp_path, data):
    import re

    from interaction_design.adaptive_export import export_adaptive

    session = prepare(tmp_path, data)
    action = evaluate(observe_adaptive(session, "p0"))
    attack = '</script><script>alert("x")</script>&<img src=x onerror=alert(1)>'
    action["reason"] = attack
    apply_adaptive(session, "p0", action)
    output = tmp_path / "view"
    export_adaptive(session, output)
    html = (output / "index.html").read_text()
    assert attack not in html
    embedded = re.search(r'<script id="replay-data" type="application/json">(.*?)</script>', html)
    payload = json.loads(embedded[1])
    assert payload["pools"][0]["rounds"][0]["submission"]["reason"] == attack
    assert "innerHTML" not in html


def test_unified_cli_exposes_loop_and_stdin_submission(tmp_path, data, capsys, monkeypatch):
    import io

    from interaction_design.cli import main

    source = write_json(tmp_path / "input.json", data)
    session = tmp_path / "session"
    assert (
        main(
            [
                "adaptive",
                "prepare",
                "--imported",
                str(source),
                "--output",
                str(session),
                "--quota",
                "2",
                "--require-review",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["adaptive", "observe", str(session), "--pool", "p0"]) == 0
    before = json.loads(capsys.readouterr().out)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(planned(before))))
    assert main(["adaptive", "apply", str(session), "--pool", "p0", "--submission", "-"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(
        "sys.stdin", io.StringIO(json.dumps(reflection(observe_adaptive(session, "p0"))))
    )
    assert main(["adaptive", "reflect", str(session), "--pool", "p0", "--submission", "-"]) == 0
    capsys.readouterr()
    assert main(["adaptive", "history", str(session), "--pool", "p0"]) == 0
    assert not json.loads(capsys.readouterr().out)["pending_reflection"]
    assert main(["adaptive", "export", str(session), "--output", str(tmp_path / "view")]) == 0

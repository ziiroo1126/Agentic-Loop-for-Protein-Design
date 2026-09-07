"""Allocation behavior and observation-only invariants for development baselines."""

from __future__ import annotations

from copy import deepcopy

import pytest

from interaction_design.adaptive_policies import POLICIES, choose_action, rank_candidates


def observation(scores=None, *, quota=2, remaining=8):
    scores = scores or {"a": [0.9], "b": [0.7], "c": [0.4], "d": [0.2]}
    candidates = [
        {
            "candidate_id": candidate_id,
            "predictions": [
                {"seed": seed, "iptm": None, "ipsae": value, "sc_dockq": None}
                for seed, value in enumerate(values)
            ],
            "remaining_predictions": 5 - len(values),
        }
        for candidate_id, values in scores.items()
    ]
    used = sum(len(row["predictions"]) for row in candidates)
    return {
        "schema_version": "molclaw-adaptive-observation-v1",
        "pool_id": "pool_01",
        "step": 0,
        "quota": quota,
        "budget": {
            "limit": used + remaining,
            "used": used,
            "remaining": remaining,
            "unit": "prediction_query",
        },
        "candidates": candidates,
        "status": "running",
        "request_sha256": "a" * 64,
    }


def append_prediction(state, candidate_id, value):
    row = next(row for row in state["candidates"] if row["candidate_id"] == candidate_id)
    row["predictions"].append({"seed": len(row["predictions"]), "ipsae": value})
    row["remaining_predictions"] -= 1
    state["budget"]["used"] += 1
    state["budget"]["remaining"] -= 1
    state["step"] += 1


def test_ranking_uses_nonnull_medians_and_ids_for_ties():
    state = observation({"f": [None], "b": [0.1, 0.9, 0.7], "d": [None, 0.7], "a": [0.8], "e": []})
    assert rank_candidates(state) == ["a", "b", "d", "e", "f"]
    state["candidates"].reverse()
    assert rank_candidates(state) == ["a", "b", "d", "e", "f"]


@pytest.mark.parametrize("policy", POLICIES)
def test_extra_fields_and_irrelevant_scores_cannot_change_decisions(policy):
    state = observation()
    altered = deepcopy(state)
    altered["private_labels"] = {"a": False, "b": False, "c": False, "d": True}
    altered["future_predictions"] = {"a": [0.0] * 4, "d": [1.0] * 4}
    altered["source_target"] = "a different target"
    for row in altered["candidates"]:
        row["label"] = row["candidate_id"] == "d"
        row["unrevealed_predictions"] = [{"seed": 4, "ipsae": 1.0}]
        row["predictions"][0]["iptm"] = 1.0
        row["predictions"][0]["sc_dockq"] = 1.0
    assert choose_action(altered, policy) == choose_action(state, policy)
    assert rank_candidates(altered) == rank_candidates(state)


@pytest.mark.parametrize("policy", POLICIES)
def test_no_policy_changes_the_input(policy):
    state = observation()
    original = deepcopy(state)
    choose_action(state, policy)
    rank_candidates(state)
    assert state == original


def test_uniform_balances_observation_counts_through_the_budget():
    state = observation()
    selected = []
    for _ in range(8):
        action = choose_action(state, "uniform")
        selected.append(action["candidate_id"])
        append_prediction(state, action["candidate_id"], None)
        counts = [len(row["predictions"]) for row in state["candidates"]]
        assert max(counts) - min(counts) <= 1
    assert selected == ["a", "b", "c", "d"] * 2
    assert choose_action(state, "uniform")["action"] == "select"


def test_fixed_top_is_seed_zero_locked_while_final_ranking_can_change():
    state = observation(remaining=4)
    for _ in range(4):
        action = choose_action(state, "fixed_top")
        assert action["candidate_id"] == "a"
        append_prediction(state, "a", 0.0)
    assert choose_action(state, "fixed_top")["candidate_ids"] == ["b", "c"]
    state["budget"]["limit"] += 1
    state["budget"]["remaining"] += 1
    assert choose_action(state, "fixed_top")["candidate_id"] == "b"


def test_fixed_top_uses_explicit_seed_zero_even_if_prediction_order_changes():
    state = observation({"a": [0.9, 0.0], "b": [0.8, 1.0]})
    for row in state["candidates"]:
        row["predictions"].reverse()
    assert choose_action(state, "fixed_top")["candidate_id"] == "a"


def test_boundary_initially_targets_one_of_the_two_ranks_next_to_quota():
    assert choose_action(observation(), "boundary")["candidate_id"] == "b"


def test_boundary_can_target_a_more_uncertain_candidate_away_from_boundary():
    state = observation({"a": [0.9, 0.9], "b": [0.7, 0.7], "c": [0.4, 0.4], "d": [0, 1]})
    # All other observed standard errors are zero. Candidate d has nonzero SE.
    assert choose_action(state, "boundary")["candidate_id"] == "d"


def test_boundary_uses_sample_standard_error_and_normalized_rank_distance():
    state = observation({"a": [0.8, 1.0], "b": [0.7, 0.7], "c": [0.5, 0.56], "d": [0.1, 0.1]})
    # a: SE=.1, distance=.375 -> .2353; c: SE=.03, distance=.125 -> .1714.
    assert choose_action(state, "boundary")["candidate_id"] == "a"


def test_boundary_uncertainty_counts_valid_scores_only():
    state = observation({"a": [0.9, 0.9], "b": [None, 0.7], "c": [0.4, 0.4], "d": [0.1, 0.1]})
    assert choose_action(state, "boundary")["candidate_id"] == "b"


@pytest.mark.parametrize("policy", POLICIES)
def test_all_policies_select_the_same_top_quota_when_budget_exhausted(policy):
    state = observation({"a": [None], "b": [0.7], "c": [0.9]}, remaining=0)
    action = choose_action(state, policy)
    assert action["action"] == "select"
    assert action["candidate_ids"] == ["c", "b"]
    assert action["request_sha256"] == state["request_sha256"]


@pytest.mark.parametrize("policy", POLICIES)
def test_no_more_predictions_stops_even_when_budget_remains(policy):
    state = observation()
    for row in state["candidates"]:
        row["remaining_predictions"] = 0
    assert choose_action(state, policy)["action"] == "select"


@pytest.mark.parametrize("policy", ["uniform", "fixed_top", "boundary"])
def test_exhausted_candidates_are_never_evaluated(policy):
    state = observation()
    for row in state["candidates"]:
        row["remaining_predictions"] = int(row["candidate_id"] == "d")
    assert choose_action(state, policy)["candidate_id"] == "d"


def test_single_does_not_spend_remaining_budget():
    action = choose_action(observation(), "single")
    assert action["action"] == "select"
    assert action["candidate_ids"] == ["a", "b"]


@pytest.mark.parametrize("policy", POLICIES)
def test_candidate_order_and_all_missing_scores_are_deterministic(policy):
    state = observation({"c": [None], "a": [None], "b": [None]})
    action = choose_action(state, policy)
    state["candidates"].reverse()
    assert choose_action(state, policy) == action
    if action["action"] == "select":
        assert action["candidate_ids"] == ["a", "b"]


def test_rank_ties_are_resolved_by_id_even_for_zero_uncertainty():
    state = observation({"d": [0.1] * 2, "c": [0.4] * 2, "b": [0.7] * 2, "a": [0.9] * 2})
    assert choose_action(state, "boundary")["candidate_id"] == "a"


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), -0.1, 1.1, "0.8"])
def test_invalid_ipsae_is_rejected(value):
    with pytest.raises(ValueError, match="ipsae"):
        choose_action(observation({"a": [value]}, quota=1), "uniform")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema_version", "other", "schema"),
        ("status", "committed", "running"),
        ("request_sha256", "", "request_sha256"),
        ("quota", True, "quota"),
        ("quota", 0, "quota"),
        ("quota", 5, "quota"),
        ("candidates", [], "candidates"),
    ],
)
def test_invalid_observation_is_rejected(field, value, message):
    state = observation()
    state[field] = value
    with pytest.raises(ValueError, match=message):
        choose_action(state, "uniform")


@pytest.mark.parametrize("field", ["limit", "used", "remaining"])
@pytest.mark.parametrize("value", [-1, True, 1.2])
def test_budget_counts_must_be_nonnegative_integers(field, value):
    state = observation()
    state["budget"][field] = value
    with pytest.raises(ValueError, match="budget"):
        choose_action(state, "uniform")


def test_inconsistent_budget_is_rejected():
    state = observation()
    state["budget"]["remaining"] -= 1
    with pytest.raises(ValueError, match="inconsistent"):
        choose_action(state, "uniform")


def test_wrong_cost_unit_is_rejected():
    state = observation()
    state["budget"]["unit"] = "gpu_seconds"
    with pytest.raises(ValueError, match="prediction_query"):
        choose_action(state, "uniform")


def test_duplicate_candidates_and_seeds_are_rejected():
    state = observation()
    state["candidates"].append(deepcopy(state["candidates"][0]))
    with pytest.raises(ValueError, match="candidate_id"):
        rank_candidates(state)
    state = observation()
    state["candidates"][0]["predictions"] *= 2
    with pytest.raises(ValueError, match="seed"):
        rank_candidates(state)


def test_missing_seed_zero_rejected_for_fixed_allocation():
    state = observation()
    state["candidates"][-1]["predictions"][0]["seed"] = 1
    with pytest.raises(ValueError, match="seed 0"):
        choose_action(state, "fixed_top")


@pytest.mark.parametrize("value", [-1, True, None])
def test_invalid_remaining_prediction_counts_are_rejected(value):
    state = observation()
    state["candidates"][0]["remaining_predictions"] = value
    with pytest.raises(ValueError, match="remaining_predictions"):
        choose_action(state, "uniform")


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError, match="unknown adaptive policy"):
        choose_action(observation(), "full")

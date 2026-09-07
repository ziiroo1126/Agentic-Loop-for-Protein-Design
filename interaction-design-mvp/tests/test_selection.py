"""Selection contracts using synthetic features only, without loading complex results."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from interaction_design.selection import (
    POST_FIELDS,
    PRE_FIELDS,
    Evidence,
    SelectionConfig,
    SelectionDecision,
    choose_selection,
    decision_schema,
    post_features,
    pre_vector,
    selection_state,
    validate_selection,
)


def candidate(key, seed, sequence="AAAA", *, plddt=80.0, rmsd=1.0, coverage=0.5, clashes=0):
    return {
        "candidate_id": key,
        "seed": seed,
        "length": len(sequence),
        "sequence": sequence,
        "structure_path": f"/synthetic/private/{key}.cif",
        "pre": {
            "monomer_plddt": plddt,
            "monomer_design_rmsd": rmsd,
            "generated_hotspot_coverage": coverage,
            "generated_clash_residue_pairs": clashes,
        },
    }


def observation(key):
    return {
        "candidate_id": key,
        "confidence": {"iptm": 0.87654321},
        "geometry": {"predicted": {"hotspot_coverage": 0.66666667, "clash_residue_pair_count": 2}},
        "design_consistency": {"binder_rmsd_after_target_alignment_angstrom": 1.23456789},
        "structure_path": "/synthetic/private/observed-complex.cif",
        "sequence": "PRIVATE_OBSERVATION_SEQUENCE",
    }


@pytest.fixture
def pool():
    # Unsorted seeds and a seed tie make a fixed policy's order observable.
    return [
        candidate("synthetic-z", 30, "AAAA", plddt=95.0),
        candidate("synthetic-b", 7, "AAAC", plddt=82.0),
        candidate("synthetic-a", 7, "CCCC", plddt=81.0),
        candidate("synthetic-c", 19, "CCCA", plddt=90.0),
    ]


@pytest.fixture
def state(pool):
    return selection_state(pool, [observation("synthetic-z")], SelectionConfig(), spent=1.0)


def evaluation(ids, *, evidence=None):
    return SelectionDecision(
        action="evaluate",
        candidate_ids=ids,
        reason="Synthetic decision for selection contract testing.",
        evidence=(
            [Evidence(candidate_id="synthetic-a", field="pre.monomer_plddt", value=81.0)]
            if evidence is None
            else evidence
        ),
    )


def test_fixed_selection_orders_by_seed_then_id_and_skips_evaluated(pool):
    config = SelectionConfig(strategy="fixed", batch_size=3)
    state = selection_state(pool, [observation("synthetic-a")], config, spent=0.0)
    decision = choose_selection(config, state)
    assert decision.candidate_ids == ["synthetic-b", "synthetic-c", "synthetic-z"]
    validate_selection(decision, state)
    reverse_state = selection_state(list(reversed(pool)), [], config, spent=0.0)
    assert choose_selection(config, reverse_state).candidate_ids == [
        "synthetic-a",
        "synthetic-b",
        "synthetic-c",
    ]


def test_heuristic_uses_pareto_front_before_sequence_diversity():
    pool = [
        candidate("evaluated", 1, "AAAA"),
        candidate("dominated-diverse", 2, "CCCC", plddt=75.0, rmsd=2.0, coverage=0.25),
        candidate("dominant-redundant", 3, "AAAA", plddt=90.0, rmsd=0.5, coverage=0.75),
    ]
    config = SelectionConfig(strategy="heuristic", batch_size=1)
    state = selection_state(pool, [observation("evaluated")], config, spent=0.0)
    decision = choose_selection(config, state)
    assert decision.candidate_ids == ["dominant-redundant"]
    validate_selection(decision, state)


def test_heuristic_prefers_diversity_from_evaluated_on_same_front():
    pool = [
        candidate("evaluated", 1, "AAAA"),
        candidate("high-confidence-copy", 2, "AAAA", plddt=95.0, rmsd=2.0),
        candidate("diverse-tradeoff", 3, "CCCC", plddt=80.0, rmsd=0.5),
    ]
    config = SelectionConfig(strategy="heuristic", batch_size=1)
    state = selection_state(pool, [observation("evaluated")], config, spent=0.0)
    decision = choose_selection(config, state)
    assert decision.candidate_ids == ["diverse-tradeoff"]
    validate_selection(decision, state)


def test_heuristic_updates_sequence_diversity_within_batch():
    pool = [
        candidate("a-first", 9, "AAAA"),
        candidate("b-copy", 1, "AAAA"),
        candidate("c-diverse", 2, "CCCC"),
    ]
    config = SelectionConfig(strategy="heuristic", batch_size=2)
    state = selection_state(pool, [], config, spent=0.0)
    decision = choose_selection(config, state)
    assert decision.candidate_ids == ["a-first", "c-diverse"]
    validate_selection(decision, state)


def test_harness_decision_requires_external_decider(pool):
    config = SelectionConfig(strategy="harness")
    state = selection_state(pool, [], config, spent=0.0)
    with pytest.raises(ValueError):
        choose_selection(config, state)


@pytest.mark.parametrize("field", PRE_FIELDS)
@pytest.mark.parametrize(
    "value", [None, "80", True, False, float("nan"), float("inf"), -float("inf")]
)
def test_pre_features_reject_missing_or_nonfinite_numeric_values(field, value):
    item = candidate("invalid", 1)
    item["pre"][field] = value
    with pytest.raises(ValueError):
        pre_vector(item)


@pytest.mark.parametrize("field", PRE_FIELDS)
def test_pre_features_reject_absent_fields(field):
    item = candidate("missing", 1)
    del item["pre"][field]
    with pytest.raises(ValueError):
        pre_vector(item)


def test_pre_features_reject_absent_pre():
    item = candidate("missing", 1)
    del item["pre"]
    with pytest.raises(ValueError):
        pre_vector(item)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("monomer_plddt", -0.1),
        ("monomer_plddt", 100.1),
        ("monomer_design_rmsd", -0.1),
        ("generated_hotspot_coverage", -0.1),
        ("generated_hotspot_coverage", 1.1),
        ("generated_clash_residue_pairs", -1),
    ],
)
def test_pre_features_reject_out_of_range_values(field, value):
    item = candidate("invalid", 1)
    item["pre"][field] = value
    with pytest.raises(ValueError):
        pre_vector(item)


def test_pre_vector_accepts_range_boundaries_and_orients_objectives():
    assert pre_vector(candidate("lower", 1, plddt=0, rmsd=0, coverage=0, clashes=0)) == (
        0,
        0,
        0,
        0,
    )
    assert pre_vector(candidate("upper", 2, plddt=100, rmsd=2, coverage=1, clashes=3)) == (
        100,
        -2,
        1,
        -3,
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("confidence", "iptm"), float("nan")),
        (("confidence", "iptm"), float("inf")),
        (("confidence", "iptm"), True),
        (("confidence", "iptm"), 1.01),
        (("confidence", "iptm"), -0.01),
        (("geometry", "predicted", "hotspot_coverage"), -0.01),
        (("geometry", "predicted", "hotspot_coverage"), 1.01),
        (("geometry", "predicted", "clash_residue_pair_count"), -1),
        (("geometry", "predicted", "clash_residue_pair_count"), True),
        (("design_consistency", "binder_rmsd_after_target_alignment_angstrom"), -0.01),
    ],
)
def test_post_features_reject_invalid_numeric_values(path, value):
    row = observation("invalid")
    target = row
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        post_features(row)


def test_post_features_reject_missing_values():
    row = observation("missing")
    del row["confidence"]["iptm"]
    with pytest.raises(ValueError):
        post_features(row)


@pytest.mark.parametrize("ids", [["unknown"], ["synthetic-a", "synthetic-a"], ["synthetic-z"]])
def test_validator_rejects_unknown_duplicate_and_evaluated_ids(state, ids):
    with pytest.raises(ValueError, match="unknown|duplicate|already evaluated"):
        validate_selection(evaluation(ids), state)


@pytest.mark.parametrize("ids", [[], ["synthetic-a", "synthetic-b", "synthetic-c"]])
def test_validator_rejects_empty_or_oversized_batch(state, ids):
    with pytest.raises(ValueError, match="size|limits"):
        validate_selection(evaluation(ids), state)


def test_validator_enforces_remaining_quota_smaller_than_batch(pool):
    config = SelectionConfig(batch_size=3, max_evaluations=2)
    state = selection_state(pool, [observation("synthetic-z")], config, spent=0.0)
    with pytest.raises(ValueError, match="size|limits"):
        validate_selection(evaluation(["synthetic-a", "synthetic-b"]), state)
    validate_selection(evaluation(["synthetic-a"]), state)


@pytest.mark.parametrize("strategy", ["fixed", "heuristic"])
def test_policy_truncates_batch_at_remaining_quota(pool, strategy):
    config = SelectionConfig(strategy=strategy, batch_size=3, max_evaluations=2)
    state = selection_state(pool, [observation("synthetic-z")], config, spent=0.0)
    decision = choose_selection(config, state)
    assert decision.action == "evaluate"
    assert len(decision.candidate_ids) == 1
    validate_selection(decision, state)


@pytest.mark.parametrize("strategy", ["fixed", "heuristic", "harness"])
@pytest.mark.parametrize("exhaustion", ["quota", "candidates", "wall-budget", "empty-pool"])
def test_exhausted_limits_force_stop(pool, strategy, exhaustion):
    config = SelectionConfig(strategy=strategy, max_evaluations=1)
    observed = [observation("synthetic-z")]
    spent = 0.0
    if exhaustion == "candidates":
        pool = pool[:1]
        config = SelectionConfig(strategy=strategy, max_evaluations=8)
    elif exhaustion == "wall-budget":
        observed = []
        config = SelectionConfig(strategy=strategy, tool_wall_budget_seconds=3.0)
        spent = 3.0
    elif exhaustion == "empty-pool":
        pool, observed = [], []
    state = selection_state(pool, observed, config, spent=spent)
    decision = choose_selection(config, state)
    assert decision.action == "stop"
    assert decision.candidate_ids == []
    assert state["remaining_evaluations"] >= 0
    validate_selection(decision, state)
    with pytest.raises(ValueError, match="size|limits"):
        validate_selection(evaluation(["synthetic-a"]), state)


def test_wall_budget_allows_selection_just_before_boundary(pool):
    config = SelectionConfig(tool_wall_budget_seconds=3.0)
    state = selection_state(pool, [], config, spent=2.999)
    assert state["stop_required"] is None
    validate_selection(choose_selection(config, state), state)


def test_stop_cannot_carry_candidate_ids(state):
    decision = SelectionDecision(
        action="stop", candidate_ids=["synthetic-a"], reason="Stop now.", evidence=[]
    )
    with pytest.raises(ValueError, match="stop cannot select"):
        validate_selection(decision, state)


def test_early_stop_without_ids_is_valid(state):
    decision = SelectionDecision(action="stop", candidate_ids=[], reason="Stop now.", evidence=[])
    validate_selection(decision, state)


def test_evaluation_requires_visible_evidence(state):
    with pytest.raises(ValueError, match="evidence"):
        validate_selection(evaluation(["synthetic-a"], evidence=[]), state)


@pytest.mark.parametrize("field", POST_FIELDS)
def test_validator_rejects_pending_candidate_post_evidence(state, field):
    hidden_value = post_features(observation("synthetic-a"))[field]
    evidence = Evidence(candidate_id="synthetic-a", field=f"post.{field}", value=hidden_value)
    with pytest.raises(ValueError, match="unobserved"):
        validate_selection(evaluation(["synthetic-a"], evidence=[evidence]), state)


@pytest.mark.parametrize(
    ("key", "field", "value"),
    [
        ("synthetic-a", "pre.monomer_plddt", 81.000001),
        ("synthetic-z", "post.iptm", 0.87654321),
        ("unknown", "pre.monomer_plddt", 81.0),
    ],
)
def test_validator_rejects_inexact_or_unknown_evidence(state, key, field, value):
    evidence = Evidence(candidate_id=key, field=field, value=value)
    with pytest.raises(ValueError, match="unobserved|differs"):
        validate_selection(evaluation(["synthetic-a"], evidence=[evidence]), state)


@pytest.mark.parametrize("field", ["pre.iptm", "post.monomer_plddt", "sequence", "pre.foo.bar"])
def test_validator_rejects_unknown_evidence_fields(state, field):
    evidence = Evidence(candidate_id="synthetic-a", field=field, value=81.0)
    with pytest.raises(ValueError, match="unknown evidence field"):
        validate_selection(evaluation(["synthetic-a"], evidence=[evidence]), state)


def test_validator_accepts_exact_visible_pre_and_observed_post_evidence(state):
    evidence = []
    for item in state["candidates"]:
        for phase in ("pre", "post"):
            evidence.extend(
                Evidence(candidate_id=item["candidate_id"], field=f"{phase}.{field}", value=value)
                for field, value in item.get(phase, {}).items()
            )
    validate_selection(evaluation(["synthetic-a"], evidence=evidence), state)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "81"])
def test_evidence_model_rejects_nonfinite_or_coerced_numbers(value):
    with pytest.raises(ValidationError):
        Evidence(candidate_id="synthetic-a", field="pre.monomer_plddt", value=value)


def test_state_reveals_only_observed_post_values_and_numeric_sequence_similarity(pool):
    hidden = observation("synthetic-a")
    pool[2]["post"] = post_features(hidden)
    pool[2]["future_result"] = hidden
    config = SelectionConfig()
    state = selection_state(pool, [observation("synthetic-z")], config, spent=2.5)
    by_id = {item["candidate_id"]: item for item in state["candidates"]}
    assert by_id["synthetic-z"]["post"] == {
        "iptm": 0.876543,
        "hotspot_coverage": 0.666667,
        "clash_residue_pairs": 2,
        "binder_rmsd_after_target_alignment": 1.234568,
    }
    assert "post" not in by_id["synthetic-a"]
    assert by_id["synthetic-b"]["max_sequence_identity_to_evaluated"] == 0.75
    assert by_id["synthetic-a"]["max_sequence_identity_to_evaluated"] == 0.0
    assert state["pairwise_sequence_identity"]["synthetic-a"]["synthetic-b"] == 0.25
    assert state["tool_wall_seconds_spent"] == 2.5
    assert state["acceptance"] is None
    assert state["binding_validated"] is False
    serialized = json.dumps(state, allow_nan=False)
    for private in ["AAAA", "AAAC", "CCCC", "CCCA", "/synthetic/private", "PRIVATE_OBSERVATION"]:
        assert private not in serialized
    pool[2]["post"]["iptm"] = 0.000001
    pool[2]["future_result"]["confidence"]["iptm"] = 0.999999
    assert selection_state(pool, [observation("synthetic-z")], config, spent=2.5) == state


def test_state_pre_projection_excludes_extra_private_fields(pool):
    pool[0]["pre"].update(
        sequence="PRIVATE_PRE_SEQUENCE",
        structure_path="/synthetic/private/pre.cif",
        post={"iptm": 0.999999},
    )
    state = selection_state(pool, [], SelectionConfig(), spent=0.0)
    assert set(state["candidates"][0]["pre"]) == set(PRE_FIELDS)
    serialized = json.dumps(state)
    assert "PRIVATE_PRE_SEQUENCE" not in serialized
    assert "/synthetic/private" not in serialized
    assert "iptm" not in serialized


@pytest.mark.parametrize("strategy", ["fixed", "heuristic"])
def test_selection_functions_do_not_mutate_inputs(pool, strategy):
    observed = [observation("synthetic-z")]
    config = SelectionConfig(strategy=strategy)
    before_pool, before_observed = deepcopy(pool), deepcopy(observed)
    before_config = config.model_dump()
    pre_vector(pool[0])
    post_features(observed[0])
    state = selection_state(pool, observed, config, spent=0.0)
    before_state = deepcopy(state)
    decision = choose_selection(config, state)
    before_decision = decision.model_dump()
    validate_selection(decision, state)
    assert pool == before_pool
    assert observed == before_observed
    assert config.model_dump() == before_config
    assert state == before_state
    assert decision.model_dump() == before_decision


def test_state_is_detached_from_input_feature_dicts(pool):
    state = selection_state(pool, [], SelectionConfig(), spent=0.0)
    pool[0]["pre"]["monomer_plddt"] = 1.0
    assert state["candidates"][0]["pre"]["monomer_plddt"] == 95.0
    state["candidates"][1]["pre"]["monomer_plddt"] = 2.0
    assert pool[1]["pre"]["monomer_plddt"] == 82.0


def test_decision_schema_covers_only_visible_feature_names():
    schema = decision_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"action", "candidate_ids", "reason", "evidence"}
    evidence = schema["properties"]["evidence"]["items"]
    assert evidence["additionalProperties"] is False
    assert set(evidence["required"]) == {"candidate_id", "field", "value"}
    assert set(evidence["properties"]["field"]["enum"]) == {
        *(f"pre.{field}" for field in PRE_FIELDS),
        *(f"post.{field}" for field in POST_FIELDS),
    }

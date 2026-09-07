"""Hand-computed and exhaustive checks of retrospective benchmark statistics."""

from __future__ import annotations

import itertools
import random

import pytest

from interaction_design.benchmark_metrics import macro_summary, ranking_metrics, selection_metrics


@pytest.mark.parametrize(
    ("scores", "auroc", "average_precision"),
    [
        ([4.0, 3.0, 2.0, 1.0], 1.0, 1.0),
        ([1.0, 2.0, 3.0, 4.0], 0.0, 5 / 12),
        ([1.0, 1.0, 1.0, 1.0], 0.5, 0.5),
        ([4.0, 2.0, 2.0, 1.0], 7 / 8, 5 / 6),
    ],
)
def test_ranking_hand_computed_cases(scores, auroc, average_precision):
    result = ranking_metrics(scores, [True, True, False, False])
    assert result["n"] == 4
    assert result["positives"] == 2
    assert result["auroc"] == pytest.approx(auroc)
    assert result["average_precision"] == pytest.approx(average_precision)


def test_auroc_matches_exhaustive_positive_negative_pair_comparisons():
    labels = [True, False, True, False]
    for scores in itertools.product(range(3), repeat=4):
        pair_credits = [
            float(scores[i] > scores[j]) + 0.5 * (scores[i] == scores[j])
            for i in [0, 2]
            for j in [1, 3]
        ]
        result = ranking_metrics(list(scores), labels)
        assert result["auroc"] == sum(pair_credits) / 4


def test_grouped_average_precision_is_invariant_to_tie_order():
    # Top tie: 1/2 positive, then bottom tie: 2/3 positive.
    # AP = (1/3)(1/2) + (2/3)(3/5) = 17/30.
    pairs = [(9.0, True), (9.0, False), (2.0, True), (2.0, True), (2.0, False)]
    for permutation in itertools.permutations(pairs):
        scores, labels = zip(*permutation, strict=True)
        result = ranking_metrics(list(scores), list(labels))
        assert result["average_precision"] == pytest.approx(17 / 30)
        assert result["auroc"] == pytest.approx(5 / 12)


@pytest.mark.parametrize(
    ("scores", "labels", "average_precision"),
    [([], [], None), ([1.0], [False], None), ([1.0, 2.0], [True, True], 1.0)],
)
def test_ranking_empty_and_single_class(scores, labels, average_precision):
    assert ranking_metrics(scores, labels) == {
        "n": len(labels),
        "positives": sum(labels),
        "auroc": None,
        "average_precision": average_precision,
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "1", None])
def test_ranking_rejects_nonfinite_and_nonnumeric_scores(value):
    with pytest.raises(ValueError, match="finite numbers"):
        ranking_metrics([value], [True])


@pytest.mark.parametrize("label", [0, 1, None, "True", [], {}])
def test_ranking_rejects_nonboolean_labels(label):
    with pytest.raises(ValueError, match="booleans"):
        ranking_metrics([1.0], [label])


def test_ranking_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="equal lengths"):
        ranking_metrics([1.0], [])


@pytest.fixture
def rows():
    return [
        {"candidate_id": "p1", "label": True, "scores": None},
        {"candidate_id": "p2", "label": True, "scores": {"arbitrary": float("nan")}},
        {"candidate_id": "n1", "label": False},
        {"candidate_id": "n2", "label": False},
    ]


def test_selection_metrics_use_full_pool_denominators(rows):
    assert selection_metrics(["p1"], rows) == {
        "selected": 1,
        "hits": 1,
        "precision": 1.0,
        "recall": 0.5,
        "enrichment": 2.0,
        "pool_size": 4,
        "pool_positives": 2,
    }
    mixed = selection_metrics(["p1", "n1", "n2"], rows)
    assert mixed["precision"] == pytest.approx(1 / 3)
    assert mixed["recall"] == 0.5
    assert mixed["enrichment"] == pytest.approx(2 / 3)
    assert selection_metrics(["n1"], rows)["enrichment"] == 0.0


def test_selecting_complete_pool_has_unit_recall_and_enrichment(rows):
    result = selection_metrics([row["candidate_id"] for row in rows], rows)
    assert result["precision"] == 0.5
    assert result["recall"] == 1.0
    assert result["enrichment"] == 1.0


def test_empty_selection_and_empty_pool(rows):
    assert selection_metrics([], rows) == {
        "selected": 0,
        "hits": 0,
        "precision": None,
        "recall": 0.0,
        "enrichment": None,
        "pool_size": 4,
        "pool_positives": 2,
    }
    assert selection_metrics([], []) == {
        "selected": 0,
        "hits": 0,
        "precision": None,
        "recall": None,
        "enrichment": None,
        "pool_size": 0,
        "pool_positives": 0,
    }


def test_zero_positive_pool_has_undefined_recall_and_enrichment():
    result = selection_metrics(["negative"], [{"candidate_id": "negative", "label": False}])
    assert result["hits"] == 0
    assert result["precision"] == 0.0
    assert result["recall"] is None
    assert result["enrichment"] is None


@pytest.mark.parametrize("selected_ids", [["p1", "p1"], ["missing"], [None], [1], [[]], [""]])
def test_selection_rejects_duplicate_unknown_and_invalid_ids(rows, selected_ids):
    with pytest.raises(ValueError):
        selection_metrics(selected_ids, rows)


def test_selection_rejects_duplicate_pool_ids(rows):
    with pytest.raises(ValueError, match="duplicate candidate_id"):
        selection_metrics([], rows + [rows[0]])


@pytest.mark.parametrize(
    "row",
    [
        {"candidate_id": "bad", "label": 1},
        {"candidate_id": "bad", "label": None},
        {"candidate_id": "bad"},
        {"label": True},
        {"candidate_id": [], "label": True},
        {"candidate_id": "", "label": True},
        None,
    ],
)
def test_selection_validates_all_pool_rows_even_when_nothing_selected(row):
    with pytest.raises(ValueError):
        selection_metrics([], [row])


def test_macro_empty_singleton_and_constant_targets():
    assert macro_summary([]) == {"n": 0, "mean": None, "ci95": None}
    assert macro_summary([0.75]) == {"n": 1, "mean": 0.75, "ci95": None}
    assert macro_summary([0.5, 0.5, 0.5]) == {"n": 3, "mean": 0.5, "ci95": [0.5, 0.5]}


def test_macro_two_target_bootstrap_has_known_endpoints():
    # All bootstrap means for [0, 1] lie in {0, 0.5, 1}; the end masses are 25%.
    assert macro_summary([0.0, 1.0]) == {"n": 2, "mean": 0.5, "ci95": [0.0, 1.0]}


def test_macro_bootstrap_percentiles_interpolate_between_order_statistics():
    # seed=4 produces resamples [0, 0], [0, 0], [0, 0], [1, 1].
    # Three zero means and one unit mean imply 97.5th percentile at 0.925.
    result = macro_summary([0.0, 1.0], seed=4, replicates=4)
    assert result == {"n": 2, "mean": 0.5, "ci95": [0.0, pytest.approx(0.925)]}


def test_macro_is_deterministic_order_invariant_and_does_not_change_global_rng():
    state = random.getstate()
    values = [0.05, 0.25, 0.5, 0.65, 0.8, 1.0]
    first = macro_summary(values, seed=11, replicates=80)
    assert first == macro_summary(values, seed=11, replicates=80)
    assert first == macro_summary(list(reversed(values)), seed=11, replicates=80)
    assert first != macro_summary(values, seed=12, replicates=80)
    assert random.getstate() == state
    assert first["n"] == len(values)
    assert first["mean"] == pytest.approx(sum(values) / len(values))
    assert 0.05 <= first["ci95"][0] <= first["ci95"][1] <= 1.0


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf"), True, "1", None])
def test_macro_rejects_nonfinite_and_nonnumeric_values(value):
    with pytest.raises(ValueError, match="finite numbers"):
        macro_summary([value])


@pytest.mark.parametrize("replicates", [0, -1, True, 2.0, None])
def test_macro_rejects_invalid_replicate_counts_even_for_empty_input(replicates):
    with pytest.raises(ValueError, match="replicates"):
        macro_summary([], replicates=replicates)


@pytest.mark.parametrize("seed", [True, 2.0, "2", None])
def test_macro_rejects_noninteger_seeds(seed):
    with pytest.raises(ValueError, match="seed"):
        macro_summary([0.0, 1.0], seed=seed)

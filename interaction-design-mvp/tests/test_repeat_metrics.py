"""Exhaustive random-selection references and hand-computed repeat overlaps."""

from __future__ import annotations

import itertools
import math
from collections import Counter

import pytest

from interaction_design.repeat_metrics import random_hit_distribution, selection_stability


def _enumerated_distribution(pools, quota):
    per_pool_hits = [
        [
            sum(index < pool["positives"] for index in selected)
            for selected in itertools.combinations(range(pool["n"]), quota)
        ]
        for pool in pools
    ]
    counts = Counter(sum(hits) for hits in itertools.product(*per_pool_hits))
    total = sum(counts.values())
    return {hits: count / total for hits, count in sorted(counts.items())}


def _assert_distribution_matches_enumeration(pools, quota):
    expected = _enumerated_distribution(pools, quota)
    result = random_hit_distribution(pools, quota)
    observed = {row["hits"]: row["probability"] for row in result["pmf"]}
    assert observed == pytest.approx(expected)
    assert list(observed) == sorted(expected)
    assert math.fsum(observed.values()) == pytest.approx(1.0)
    mean = math.fsum(hits * probability for hits, probability in expected.items())
    variance = math.fsum((hits - mean) ** 2 * probability for hits, probability in expected.items())
    assert result["expected_hits"] == pytest.approx(mean)
    assert result["variance"] == pytest.approx(variance)
    lower, upper = result["central_95_interval"]
    assert type(lower) is int and type(upper) is int
    assert math.fsum(p for hits, p in expected.items() if hits < lower) <= 0.025 + 1e-12
    assert math.fsum(p for hits, p in expected.items() if hits > upper) <= 0.025 + 1e-12


def test_all_small_single_pool_distributions_match_exhaustive_selection():
    for n in range(8):
        for positives in range(n + 1):
            for quota in range(n + 1):
                _assert_distribution_matches_enumeration([{"n": n, "positives": positives}], quota)


@pytest.mark.parametrize("quota", [0, 1, 2, 3])
def test_independent_pool_convolution_matches_exhaustive_joint_selection(quota):
    for positives in itertools.product(range(4), repeat=3):
        pools = [{"n": n, "positives": k} for n, k in zip([3, 4, 5], positives, strict=True)]
        _assert_distribution_matches_enumeration(pools, quota)


def test_two_fair_draws_have_binomial_total_distribution():
    assert random_hit_distribution([{"n": 2, "positives": 1}] * 2, 1) == {
        "expected_hits": 1.0,
        "variance": 0.5,
        "central_95_interval": [0, 2],
        "pmf": [
            {"hits": 0, "probability": 0.25},
            {"hits": 1, "probability": 0.5},
            {"hits": 2, "probability": 0.25},
        ],
    }


@pytest.mark.parametrize(
    ("pools", "quota", "hits"),
    [
        ([], 0, 0),
        ([], 7, 0),
        ([{"n": 0, "positives": 0}], 0, 0),
        ([{"n": 100, "positives": 40}], 0, 0),
        ([{"n": 100, "positives": 0}] * 3, 7, 0),
        ([{"n": 100, "positives": 100}] * 3, 7, 21),
        ([{"n": 100, "positives": 0}, {"n": 100, "positives": 100}], 7, 7),
        ([{"n": 7, "positives": 3}, {"n": 7, "positives": 4}], 7, 7),
        ([{"n": 1, "positives": 1}], 1, 1),
    ],
)
def test_empty_and_deterministic_references(pools, quota, hits):
    assert random_hit_distribution(pools, quota) == {
        "expected_hits": float(hits),
        "variance": 0.0,
        "central_95_interval": [hits, hits],
        "pmf": [{"hits": hits, "probability": 1.0}],
    }


def test_discrete_quantiles_include_exact_cdf_boundary_without_interpolation():
    # Inverse CDF picks zero when P(X <= 0) is exactly the requested quantile.
    assert random_hit_distribution([{"n": 40, "positives": 1}], 1)["central_95_interval"] == [0, 0]
    assert random_hit_distribution([{"n": 40, "positives": 39}], 1)["central_95_interval"] == [0, 1]


def test_large_combination_counts_produce_finite_normalized_probabilities():
    pools = [{"n": 10000, "positives": 800}, {"n": 8000, "positives": 4000}]
    quota = 200
    result = random_hit_distribution(pools, quota)
    expected_mean = math.fsum(quota * p["positives"] / p["n"] for p in pools)
    expected_variance = math.fsum(
        quota
        * (p["positives"] / p["n"])
        * (1 - p["positives"] / p["n"])
        * (p["n"] - quota)
        / (p["n"] - 1)
        for p in pools
    )
    probabilities = [row["probability"] for row in result["pmf"]]
    assert all(math.isfinite(p) and 0 <= p <= 1 for p in probabilities)
    assert math.fsum(probabilities) == pytest.approx(1.0)
    assert result["expected_hits"] == pytest.approx(expected_mean)
    assert result["variance"] == pytest.approx(expected_variance)
    assert result == random_hit_distribution(list(reversed(pools)), quota)


@pytest.mark.parametrize("quota", [-1, True, False, 1.0, "1", None])
def test_invalid_quota_rejected_even_with_no_pools(quota):
    with pytest.raises(ValueError, match="quota"):
        random_hit_distribution([], quota)


@pytest.mark.parametrize("pools", [None, {}, "pools", (), [None], [[]], [4]])
def test_pool_container_validation(pools):
    with pytest.raises(ValueError, match="pool"):
        random_hit_distribution(pools, 0)


@pytest.mark.parametrize("n", [-1, True, False, 1.0, "1", None])
def test_invalid_pool_size_rejected(n):
    with pytest.raises(ValueError, match="pool n"):
        random_hit_distribution([{"n": n, "positives": 0}], 0)


@pytest.mark.parametrize("positives", [-1, 4, True, False, 1.0, "1", None])
def test_invalid_positive_count_rejected(positives):
    with pytest.raises(ValueError, match="positives"):
        random_hit_distribution([{"n": 3, "positives": positives}], 0)


@pytest.mark.parametrize("pool", [{}, {"n": 3}, {"positives": 0}])
def test_missing_counts_rejected(pool):
    with pytest.raises(ValueError):
        random_hit_distribution([pool], 0)


def test_quota_must_fit_every_pool_including_empty_pool():
    with pytest.raises(ValueError, match="quota"):
        random_hit_distribution([{"n": 10, "positives": 2}, {"n": 1, "positives": 1}], 2)
    with pytest.raises(ValueError, match="quota"):
        random_hit_distribution([{"n": 0, "positives": 0}], 1)


def test_jaccard_hand_computed_pair_mean_and_all_run_intersection():
    selections = [["a", "b"], ["b", "c"], ["a", "b", "c", "d"]]
    result = selection_stability(selections)
    assert result == {
        "runs": 3,
        "pairs": 3,
        "mean_pairwise_jaccard": pytest.approx((1 / 3 + 1 / 2 + 1 / 2) / 3),
        "min_pairwise_jaccard": pytest.approx(1 / 3),
        "max_pairwise_jaccard": 0.5,
        "union_size": 4,
        "intersection_size": 1,
    }
    for reordered in itertools.permutations(selections):
        assert selection_stability([list(reversed(run)) for run in reordered]) == result


def test_single_run_pairwise_scores_are_undefined():
    assert selection_stability([["a", "b"]]) == {
        "runs": 1,
        "pairs": 0,
        "mean_pairwise_jaccard": None,
        "min_pairwise_jaccard": None,
        "max_pairwise_jaccard": None,
        "union_size": 2,
        "intersection_size": 2,
    }


@pytest.mark.parametrize(
    ("selections", "jaccard", "union", "intersection"),
    [
        ([["a", "b"], ["b", "a"], ["a", "b"]], 1.0, 2, 2),
        ([["a"], ["b"], ["c"]], 0.0, 3, 0),
    ],
)
def test_identical_and_disjoint_selections(selections, jaccard, union, intersection):
    result = selection_stability(selections)
    assert result["mean_pairwise_jaccard"] == jaccard
    assert result["min_pairwise_jaccard"] == jaccard
    assert result["max_pairwise_jaccard"] == jaccard
    assert result["union_size"] == union
    assert result["intersection_size"] == intersection


@pytest.mark.parametrize("selections", [[], None, {}, "abc", (), [[]], ["abc"], [["a"], []]])
def test_invalid_or_empty_runs_rejected(selections):
    with pytest.raises(ValueError):
        selection_stability(selections)


@pytest.mark.parametrize("candidate_id", ["", None, 1, True, [], {}, ["a"]])
def test_invalid_candidate_ids_rejected(candidate_id):
    with pytest.raises(ValueError, match="nonempty strings"):
        selection_stability([[candidate_id]])


def test_duplicate_ids_are_rejected_instead_of_silently_deduplicated():
    with pytest.raises(ValueError, match="duplicate"):
        selection_stability([["a", "b"], ["a", "a"]])

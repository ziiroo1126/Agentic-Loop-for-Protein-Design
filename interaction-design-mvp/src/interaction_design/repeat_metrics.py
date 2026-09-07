"""Exact random-selection reference and descriptive repeat-selection stability.

All calculations use the standard library. Repeated selections are not treated
as independent targets, and neither function makes significance claims.
"""

from __future__ import annotations

import math
from itertools import combinations


def random_hit_distribution(pools: list[dict], quota: int) -> dict:
    """Return total hits when selecting ``quota`` candidates from each pool.

    Each pool supplies nonnegative integer ``n`` and ``positives`` counts, with
    ``positives <= n`` and ``quota <= n``. Selection is uniform without
    replacement within each pool and independent across pools. Booleans are not
    integers for these inputs. Zero quota and empty pools are allowed and give
    zero hits with probability one; an empty pool requires zero quota.

    Hypergeometric combination counts are convolved using exact integers before
    probabilities and moments are converted to floats. ``central_95_interval``
    contains the smallest integer at which the cumulative probability reaches
    2.5% and 97.5%, respectively. This discrete reference interval may cover more
    than 95%; it is not a confidence interval on a measured method's performance.
    ``pmf`` lists possible hit counts in increasing order.
    """
    if not isinstance(pools, list):
        raise ValueError("pools must be a list of dictionaries")
    if type(quota) is not int or quota < 0:
        raise ValueError("quota must be a nonnegative integer")

    counts = {0: 1}
    total = 1
    for pool in pools:
        if not isinstance(pool, dict):
            raise ValueError("each pool must be a dictionary")
        n = pool.get("n")
        positives = pool.get("positives")
        if type(n) is not int or n < 0:
            raise ValueError("pool n must be a nonnegative integer")
        if type(positives) is not int or not 0 <= positives <= n:
            raise ValueError("pool positives must be an integer between zero and n")
        if quota > n:
            raise ValueError("quota must not exceed any pool n")

        negatives = n - positives
        pool_counts = {
            hits: math.comb(positives, hits) * math.comb(negatives, quota - hits)
            for hits in range(max(0, quota - negatives), min(quota, positives) + 1)
        }
        combined = {}
        for previous_hits, previous_count in counts.items():
            for hits, count in pool_counts.items():
                combined_hits = previous_hits + hits
                combined[combined_hits] = combined.get(combined_hits, 0) + previous_count * count
        counts = combined
        total *= math.comb(n, quota)

    first_moment = sum(hits * count for hits, count in counts.items())
    second_moment = sum(hits * hits * count for hits, count in counts.items())
    cumulative = 0
    lower = None
    upper = None
    pmf = []
    for hits, count in sorted(counts.items()):
        cumulative += count
        if lower is None and 40 * cumulative >= total:
            lower = hits
        if upper is None and 40 * cumulative >= 39 * total:
            upper = hits
        pmf.append({"hits": hits, "probability": count / total})

    return {
        "expected_hits": first_moment / total,
        "variance": (second_moment * total - first_moment * first_moment) / (total * total),
        "central_95_interval": [lower, upper],
        "pmf": pmf,
    }


def selection_stability(selections: list[list[str]]) -> dict:
    """Describe set overlap across nonempty, uniquely identified selections.

    Each run must be a nonempty list of unique nonempty string candidate IDs.
    The pairwise Jaccard score is intersection size divided by union size. Its
    mean weights every unordered pair equally, even when run sizes differ.
    At least one run is required; pairwise statistics are ``None`` for one run.
    Union and intersection sizes include all runs, independent of their order.
    """
    if not isinstance(selections, list) or not selections:
        raise ValueError("selections must be a nonempty list of runs")

    runs = []
    for selection in selections:
        if not isinstance(selection, list) or not selection:
            raise ValueError("each run must be a nonempty list")
        selected = set()
        for candidate_id in selection:
            if not isinstance(candidate_id, str) or not candidate_id:
                raise ValueError("candidate IDs must be nonempty strings")
            if candidate_id in selected:
                raise ValueError("duplicate candidate ID within a run")
            selected.add(candidate_id)
        runs.append(selected)

    jaccards = [len(left & right) / len(left | right) for left, right in combinations(runs, 2)]
    return {
        "runs": len(runs),
        "pairs": len(jaccards),
        "mean_pairwise_jaccard": math.fsum(jaccards) / len(jaccards) if jaccards else None,
        "min_pairwise_jaccard": min(jaccards) if jaccards else None,
        "max_pairwise_jaccard": max(jaccards) if jaccards else None,
        "union_size": len(set.union(*runs)),
        "intersection_size": len(set.intersection(*runs)),
    }

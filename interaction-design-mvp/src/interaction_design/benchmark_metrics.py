"""Descriptive retrospective benchmark metrics, using only the standard library.

Labels are already established binary assay outcomes; these functions neither infer
labels nor treat candidates within one target as independent target replicates.
Undefined quantities are returned as ``None`` for JSON serialization.
"""

from __future__ import annotations

import math
import random
import statistics
from itertools import groupby


def _validate_numbers(values: list[float], name: str) -> None:
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must contain finite numbers, excluding booleans")
        try:
            finite = math.isfinite(value)
        except OverflowError as error:
            raise ValueError(f"{name} must contain finite numbers") from error
        if not finite:
            raise ValueError(f"{name} must contain finite numbers")


def ranking_metrics(scores: list[float], labels: list[bool]) -> dict:
    """Return AUROC and non-interpolated average precision for one candidate pool.

    Higher scores indicate stronger positive evidence. AUROC is the fraction of
    positive/negative pairs correctly ordered, with half credit for score ties.
    Average precision integrates the precision/recall step function after adding
    each entire tied-score group; it never imposes an order within a tie.
    AUROC is undefined if either class is absent. Average precision is undefined
    without positives and is 1 when all candidates are positive. Empty inputs are
    allowed. Labels must be actual booleans, and scores must be finite numbers.
    """
    if len(scores) != len(labels):
        raise ValueError("scores and labels must have equal lengths")
    _validate_numbers(scores, "scores")
    if any(type(label) is not bool for label in labels):
        raise ValueError("labels must contain booleans")

    n = len(labels)
    positives = sum(labels)
    negatives = n - positives
    seen_positive = 0
    seen_negative = 0
    twice_correct_pairs = 0
    ap_terms = []
    ranked = sorted(zip(scores, labels, strict=True), key=lambda item: item[0], reverse=True)
    for _, group in groupby(ranked, key=lambda item: item[0]):
        group_labels = [label for _, label in group]
        group_positive = sum(group_labels)
        group_negative = len(group_labels) - group_positive
        seen_positive += group_positive
        seen_negative += group_negative
        lower_negatives = negatives - seen_negative
        twice_correct_pairs += group_positive * (2 * lower_negatives + group_negative)
        if group_positive:
            ap_terms.append(
                (group_positive / positives) * (seen_positive / (seen_positive + seen_negative))
            )

    return {
        "n": n,
        "positives": positives,
        "auroc": twice_correct_pairs / (2 * positives * negatives)
        if positives and negatives
        else None,
        "average_precision": math.fsum(ap_terms) if positives else None,
    }


def selection_metrics(selected_ids: list[str], rows: list[dict]) -> dict:
    """Describe a unique selection against its complete labeled candidate pool.

    Each row supplies a unique nonempty string ``candidate_id`` and boolean
    ``label``; all other fields, including scores, are ignored. Enrichment is
    selection precision divided by the full pool's positive fraction. Recall is
    undefined without pool positives. Precision is undefined for an empty
    selection; enrichment is undefined for an empty selection or no positives.
    Duplicate identities, unknown selections, and invalid rows are rejected.
    """
    by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each candidate row must be a dictionary")
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("candidate_id must be a nonempty string")
        if candidate_id in by_id:
            raise ValueError("duplicate candidate_id in pool")
        label = row.get("label")
        if type(label) is not bool:
            raise ValueError("candidate labels must be booleans")
        by_id[candidate_id] = label

    selected = set()
    for candidate_id in selected_ids:
        if not isinstance(candidate_id, str) or not candidate_id:
            raise ValueError("selected_ids must contain nonempty strings")
        if candidate_id in selected:
            raise ValueError("duplicate selected candidate_id")
        if candidate_id not in by_id:
            raise ValueError("unknown selected candidate_id")
        selected.add(candidate_id)

    pool_size = len(by_id)
    pool_positives = sum(by_id.values())
    hits = sum(by_id[candidate_id] for candidate_id in selected)
    precision = hits / len(selected) if selected else None
    return {
        "selected": len(selected),
        "hits": hits,
        "precision": precision,
        "recall": hits / pool_positives if pool_positives else None,
        "enrichment": precision / (pool_positives / pool_size)
        if precision is not None and pool_positives
        else None,
        "pool_size": pool_size,
        "pool_positives": pool_positives,
    }


def _percentile(sorted_values: list[float], probability: float) -> float:
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    fraction = position - lower
    return (1 - fraction) * sorted_values[lower] + fraction * sorted_values[upper]


def macro_summary(values: list[float], seed: int = 20260906, replicates: int = 2000) -> dict:
    """Summarize equally weighted metrics from independent targets.

    Each supplied value represents one independent target, not an individual
    candidate or repeated run. The caller must explicitly omit undefined target
    metrics before calling; ``n`` counts the included targets. The 95% interval is
    a percentile bootstrap interval from ``replicates`` target-level resamples of
    size ``n``, with replacement and linear interpolation between order statistics
    at 2.5% and 97.5%. Sorting before using a local seeded generator makes results
    reproducible regardless of target order, without changing global RNG state.
    These are descriptive intervals, not tests of statistical significance.

    Empty input returns a null mean and interval; a singleton returns a null
    interval. Values must be finite numbers, seed an integer, and replicates a
    positive integer. Booleans are not accepted as numbers or integer parameters.
    """
    _validate_numbers(values, "values")
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    if type(replicates) is not int or replicates < 1:
        raise ValueError("replicates must be a positive integer")
    n = len(values)
    mean = float(statistics.mean(values)) if n else None
    if n < 2:
        return {"n": n, "mean": mean, "ci95": None}

    ordered = sorted(values)
    rng = random.Random(seed)
    samples = sorted(float(statistics.mean(rng.choices(ordered, k=n))) for _ in range(replicates))
    return {
        "n": n,
        "mean": mean,
        "ci95": [_percentile(samples, 0.025), _percentile(samples, 0.975)],
    }

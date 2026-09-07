"""Transparent multi-metric filtering and ranking."""

from __future__ import annotations

import copy
import math
from collections.abc import Sequence

from evedesign.system import SystemInstance

from interaction_design.specs import EvaluationSpec, MetricRule


def _metric(instance: SystemInstance, rule: MetricRule) -> float | None:
    try:
        value = float(instance.metadata["evaluations"][rule.source][rule.metric])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(value):
        raise ValueError(f"non-finite metric {rule.source}.{rule.metric} for {instance.id}")
    return value


def _passes(value: float, rule: MetricRule) -> bool:
    if rule.threshold is None:
        return True
    if rule.higher_is_better:
        return value >= rule.threshold
    return value <= rule.threshold


def rank_instances(
    instances: Sequence[SystemInstance], evaluation: EvaluationSpec
) -> list[SystemInstance]:
    """Min-max normalize configured metrics, filter inclusively, and rank.

    Ties are stable by candidate id.  If a metric is constant, every candidate
    receives full credit for that metric because it contains no ranking signal.
    """

    if not instances:
        return []
    rules = evaluation.metric_rules
    values: dict[tuple[str, str], list[float]] = {}
    for rule in rules:
        key = (rule.source, rule.metric)
        metric_values = [_metric(instance, rule) for instance in instances]
        if evaluation.require_all_metrics and any(value is None for value in metric_values):
            missing = [
                instance.id
                for instance, value in zip(instances, metric_values, strict=True)
                if value is None
            ]
            raise ValueError(f"missing metric {rule.source}.{rule.metric} for candidates {missing}")
        values[key] = [value for value in metric_values if value is not None]

    total_weight = sum(rule.weight for rule in rules)
    ranked: list[SystemInstance] = []
    for instance in instances:
        updated = instance.copy()
        updated.metadata = copy.deepcopy(instance.metadata or {})
        contributions: dict[str, float] = {}
        pass_results: dict[str, bool] = {}
        weighted_sum = 0.0
        used_weight = 0.0
        for rule in rules:
            value = _metric(instance, rule)
            label = f"{rule.source}.{rule.metric}"
            if value is None:
                continue
            series = values[(rule.source, rule.metric)]
            low, high = min(series), max(series)
            if high == low:
                normalized = 1.0
            else:
                normalized = (value - low) / (high - low)
                if not rule.higher_is_better:
                    normalized = 1.0 - normalized
            contribution = normalized * rule.weight
            contributions[label] = contribution
            pass_results[label] = _passes(value, rule)
            weighted_sum += contribution
            used_weight += rule.weight

        denominator = total_weight if evaluation.require_all_metrics else used_weight
        rank_score = weighted_sum / denominator if denominator else 0.0
        threshold_pass = all(pass_results.values()) if pass_results else True
        updated.score = rank_score
        updated.metadata["ranking"] = {
            "score": rank_score,
            "threshold_pass": threshold_pass,
            "thresholds": pass_results,
            "weighted_contributions": contributions,
        }
        ranked.append(updated)

    ranked.sort(
        key=lambda instance: (
            not bool(instance.metadata["ranking"]["threshold_pass"]),
            -float(instance.score),
            instance.id or "",
        )
    )
    for rank, instance in enumerate(ranked, start=1):
        instance.metadata["ranking"]["rank"] = rank
    return ranked

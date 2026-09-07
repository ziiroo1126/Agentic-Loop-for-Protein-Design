"""Development allocation baselines using only revealed prediction scores.

These policies are deterministic baselines, not a calibrated reliability model.
Every policy uses the same final ranking: median observed non-null ipSAE, higher
first, with missing medians last and candidate ID resolving ties. Neither labels
nor unrevealed predictions are inputs. Extra observation fields are ignored.

The boundary heuristic uses standard error of observed ipSAE (sample standard
deviation divided by sqrt(n)). Fewer than two valid scores use the prespecified
exploration value 0.1. This value is not an estimated or calibrated uncertainty.
For one-based rank r, quota k and pool size N, its priority is
SE / (abs(r - (k + 0.5)) / N + 0.05). Thus the two ranks either side of the
selection boundary have equal distance before accounting for uncertainty.
"""

from __future__ import annotations

import math
from statistics import median, stdev

POLICIES = ("uniform", "fixed_top", "boundary", "single")
EXPLORATION_UNCERTAINTY = 0.1
BOUNDARY_DISTANCE_OFFSET = 0.05


def _candidates(observation: dict) -> list[dict]:
    if not isinstance(observation, dict):
        raise ValueError("observation must be an object")
    rows = observation.get("candidates")
    if not isinstance(rows, list) or not rows:
        raise ValueError("candidates must be a nonempty list")
    seen_ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("candidate must be an object")
        candidate_id = row.get("candidate_id")
        if not isinstance(candidate_id, str) or not candidate_id or candidate_id in seen_ids:
            raise ValueError("candidate_id must be nonempty and unique")
        seen_ids.add(candidate_id)
        predictions = row.get("predictions")
        if not isinstance(predictions, list):
            raise ValueError("predictions must be a list")
        seeds = set()
        for prediction in predictions:
            if not isinstance(prediction, dict):
                raise ValueError("prediction must be an object")
            seed = prediction.get("seed")
            if type(seed) is not int or seed < 0 or seed in seeds:
                raise ValueError("prediction seed must be a unique nonnegative integer")
            seeds.add(seed)
            if "ipsae" not in prediction:
                raise ValueError("prediction must include ipsae, possibly null")
            score = prediction["ipsae"]
            if score is not None and (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not 0 <= score <= 1
            ):
                raise ValueError("ipsae must be null or a finite number in [0, 1]")
    return rows


def _scores(row: dict) -> list[float]:
    return [p["ipsae"] for p in row["predictions"] if p["ipsae"] is not None]


def _ranking_key(row: dict) -> tuple:
    scores = _scores(row)
    return (not scores, -median(scores) if scores else 0.0, row["candidate_id"])


def rank_candidates(observation: dict) -> list[str]:
    """Rank candidates by the median of their already observed non-null ipSAE."""
    return [row["candidate_id"] for row in sorted(_candidates(observation), key=_ranking_key)]


def _validate_action_observation(observation: dict, rows: list[dict]) -> tuple[int, int]:
    if observation.get("schema_version") != "molclaw-adaptive-observation-v1":
        raise ValueError("unsupported observation schema_version")
    if observation.get("status") != "running":
        raise ValueError("actions require a running observation")
    request_hash = observation.get("request_sha256")
    if not isinstance(request_hash, str) or not request_hash:
        raise ValueError("observation requires request_sha256")
    quota = observation.get("quota")
    if type(quota) is not int or not 1 <= quota <= len(rows):
        raise ValueError("quota must be an integer between 1 and the pool size")
    budget = observation.get("budget")
    if not isinstance(budget, dict) or budget.get("unit") != "prediction_query":
        raise ValueError("budget must use prediction_query units")
    for name in ("limit", "used", "remaining"):
        if type(budget.get(name)) is not int or budget[name] < 0:
            raise ValueError("budget counts must be nonnegative integers")
    if budget["used"] + budget["remaining"] != budget["limit"]:
        raise ValueError("budget counts are inconsistent")
    for row in rows:
        remaining = row.get("remaining_predictions")
        if type(remaining) is not int or remaining < 0:
            raise ValueError("remaining_predictions must be a nonnegative integer")
    return quota, budget["remaining"]


def _initial_key(row: dict) -> tuple:
    initial = [p["ipsae"] for p in row["predictions"] if p["seed"] == 0]
    if not initial:
        raise ValueError("fixed_top requires a revealed seed 0 for every candidate")
    score = initial[0]
    return (score is None, -score if score is not None else 0.0, row["candidate_id"])


def _boundary_key(row: dict, ranks: dict[str, int], quota: int, pool_size: int) -> tuple:
    scores = _scores(row)
    uncertainty = (
        stdev(scores) / math.sqrt(len(scores)) if len(scores) >= 2 else EXPLORATION_UNCERTAINTY
    )
    distance = abs(ranks[row["candidate_id"]] - (quota + 0.5)) / pool_size
    priority = uncertainty / (distance + BOUNDARY_DISTANCE_OFFSET)
    return (-priority, row["candidate_id"])


def choose_action(observation: dict, policy: str) -> dict:
    """Return one evaluation request or a final selection without changing inputs.

    ``single`` immediately selects. Other policies spend remaining query budget
    unless every candidate is exhausted. A full five-seed reference is obtained
    using ``uniform`` with budget 5N, rather than introducing another policy.
    ``fixed_top`` allocates all possible extra predictions to each candidate in
    descending seed-0 order before moving on; its final ranking still uses all
    observed scores. ``uniform`` chooses the least observed eligible candidate.
    """
    if policy not in POLICIES:
        raise ValueError(f"unknown adaptive policy: {policy!r}")
    rows = _candidates(observation)
    quota, remaining = _validate_action_observation(observation, rows)
    ranked = [row["candidate_id"] for row in sorted(rows, key=_ranking_key)]
    eligible = [row for row in rows if row["remaining_predictions"] > 0]
    if policy == "single" or remaining == 0 or not eligible:
        reason = (
            "Single-prediction reference selects from the current revealed scores."
            if policy == "single"
            else "Prediction query budget exhausted."
            if remaining == 0
            else "No candidate has further available predictions."
        )
        return {
            "request_sha256": observation["request_sha256"],
            "action": "select",
            "candidate_ids": ranked[:quota],
            "reason": reason,
        }
    if policy == "uniform":
        selected = min(eligible, key=lambda row: (len(row["predictions"]), row["candidate_id"]))
        reason = "Evaluate the eligible candidate with the fewest revealed predictions."
    elif policy == "fixed_top":
        initial_order = sorted(rows, key=_initial_key)
        selected = next(row for row in initial_order if row["remaining_predictions"] > 0)
        reason = "Allocate by the fixed seed-0 ipSAE order, exhausting each candidate in turn."
    else:
        ranks = {candidate_id: index + 1 for index, candidate_id in enumerate(ranked)}
        selected = min(eligible, key=lambda row: _boundary_key(row, ranks, quota, len(rows)))
        reason = (
            "Evaluate the largest observed standard-error / rank-boundary-distance priority; "
            "fewer than two valid scores use the prespecified, uncalibrated 0.1 exploration value."
        )
    return {
        "request_sha256": observation["request_sha256"],
        "action": "evaluate",
        "candidate_id": selected["candidate_id"],
        "reason": reason,
    }

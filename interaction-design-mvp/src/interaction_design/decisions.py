"""Shared observations and bounded decisions for resampling baselines."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class DecisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    version: Literal["resample-or-stop-v1"] = "resample-or-stop-v1"
    strategy: Literal["fixed", "feedback"] = "fixed"
    first_seed: int = Field(default=59, ge=0, le=2**31 - 1)
    batch_size: int = Field(default=2, ge=1, le=64)
    max_rounds: int = Field(default=2, ge=1, le=100)
    patience: int = Field(default=1, ge=1)
    tool_wall_budget_seconds: float | None = Field(default=None, gt=0)


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["sample", "stop"]
    seeds: list[int] = Field(default_factory=list)
    reason: str = Field(min_length=1)


def objective_vector(row: dict) -> tuple[float, ...]:
    """All axes maximize; these are diagnostic objectives, not acceptance rules."""
    geometry = row["geometry"]["predicted"]
    confidence = row["confidence"]["iptm"]
    hotspots, total = geometry["hotspots_contacted"], geometry["hotspot_count"]
    clashes = geometry["clash_residue_pair_count"]
    rmsd = row["design_consistency"]["binder_rmsd_after_target_alignment_angstrom"]
    if (
        type(total) is not int
        or type(hotspots) is not int
        or type(clashes) is not int
        or not 0 <= hotspots <= total
        or total < 1
        or clashes < 0
        or isinstance(confidence, bool)
        or isinstance(rmsd, bool)
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
        or not math.isfinite(rmsd)
        or rmsd < 0
    ):
        raise ValueError("invalid decision metrics or missing requested hotspots")
    return float(confidence), hotspots / total, -float(clashes), -float(rmsd)


def frontier(rows: list[dict]) -> list[dict]:
    """Keep Pareto-nondominated observations, with candidate-ID tie breaking."""
    result = []
    for row in sorted(rows, key=lambda x: x["candidate_id"]):
        vector = objective_vector(row)
        if any(
            all(a >= b for a, b in zip(objective_vector(old), vector, strict=True))
            for old in result
        ):
            continue
        result = [
            old
            for old in result
            if not all(a >= b for a, b in zip(vector, objective_vector(old), strict=True))
        ]
        result.append(row)
    return result


def visible_state(initial: list[dict], rounds: list[dict], spent: float) -> dict:
    """Summarize only observations already returned by completed tool calls."""
    seen = list(initial)
    stale = 0
    latest_novel = []
    for result in rounds:
        previous_vectors = [objective_vector(x) for x in frontier(seen)]
        latest_novel = [
            row["candidate_id"]
            for row in result["observations"]
            if not any(
                all(a >= b for a, b in zip(old, objective_vector(row), strict=True))
                for old in previous_vectors
            )
        ]
        stale = 0 if latest_novel else stale + 1
        seen.extend(result["observations"])
    return {
        "completed_rounds": len(rounds),
        "observed_candidate_ids": [x["candidate_id"] for x in seen],
        "observed_seeds": [x["generation_seed"] for x in seen],
        "frontier": [
            {"candidate_id": row["candidate_id"], "objectives": list(objective_vector(row))}
            for row in frontier(seen)
        ],
        "latest_novel_candidate_ids": latest_novel,
        "consecutive_rounds_without_frontier_extension": stale,
        "tool_wall_seconds_spent": spent,
        "acceptance": None,
        "binding_validated": False,
    }


def next_seeds(config: DecisionConfig, state: dict) -> list[int]:
    first = config.first_seed + state["completed_rounds"] * config.batch_size
    return list(range(first, first + config.batch_size))


def required_stop(config: DecisionConfig, state: dict) -> str | None:
    if config.tool_wall_budget_seconds is not None and (
        state["tool_wall_seconds_spent"] >= config.tool_wall_budget_seconds
    ):
        return "tool_wall_budget_exhausted"
    if state["completed_rounds"] >= config.max_rounds:
        return "round_limit_reached"
    return None


def choose_decision(config: DecisionConfig, state: dict) -> Decision:
    reason = required_stop(config, state)
    if reason:
        return Decision(action="stop", reason=reason)
    if config.strategy == "feedback" and (
        state["consecutive_rounds_without_frontier_extension"] >= config.patience
    ):
        return Decision(action="stop", reason="no_new_diagnostic_tradeoff")
    return Decision(
        action="sample",
        seeds=next_seeds(config, state),
        reason="fixed_sampling_schedule"
        if config.strategy == "fixed"
        else "collect_next_batch_after_feedback",
    )


def validate_decision(decision: Decision, config: DecisionConfig, state: dict) -> None:
    """Executor-side checks also apply to future externally proposed decisions."""
    if decision.action == "stop":
        if decision.seeds:
            raise ValueError("stop cannot request seeds")
        return
    if required_stop(config, state):
        raise ValueError("sampling exceeds campaign limits")
    if decision.seeds != next_seeds(config, state):
        raise ValueError("sampling must use the next frozen seed batch")
    if set(decision.seeds) & set(state["observed_seeds"]):
        raise ValueError("sampling would repeat an observed generation seed")

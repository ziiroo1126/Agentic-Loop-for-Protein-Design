"""Candidate selection from pre-complex features and already revealed observations."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SelectionConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True
    )

    version: Literal["candidate-selection-v1"] = "candidate-selection-v1"
    strategy: Literal["fixed", "heuristic", "harness"] = "fixed"
    batch_size: int = Field(default=2, ge=1, le=32)
    max_evaluations: int = Field(default=8, ge=1, le=256)
    tool_wall_budget_seconds: float | None = Field(default=None, gt=0)


PRE_FIELDS = (
    "monomer_plddt",
    "monomer_design_rmsd",
    "generated_hotspot_coverage",
    "generated_clash_residue_pairs",
)
POST_FIELDS = (
    "iptm",
    "hotspot_coverage",
    "clash_residue_pairs",
    "binder_rmsd_after_target_alignment",
)


class Evidence(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, allow_inf_nan=False, hide_input_in_errors=True
    )
    candidate_id: str
    field: str
    value: float


class SelectionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    action: Literal["evaluate", "stop"]
    candidate_ids: list[str]
    reason: str = Field(min_length=1, max_length=1200)
    evidence: list[Evidence]


def decision_schema() -> dict:
    """Portable strict-output subset; richer constraints are enforced locally."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["evaluate", "stop"]},
            "candidate_ids": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "field": {
                            "type": "string",
                            "enum": [f"pre.{x}" for x in PRE_FIELDS]
                            + [f"post.{x}" for x in POST_FIELDS],
                        },
                        "value": {"type": "number"},
                    },
                    "required": ["candidate_id", "field", "value"],
                },
            },
        },
        "required": ["action", "candidate_ids", "reason", "evidence"],
    }


def pre_vector(candidate: dict) -> tuple[float, ...]:
    try:
        pre = candidate["pre"]
        values = tuple(pre[x] for x in PRE_FIELDS)
    except (KeyError, TypeError) as error:
        raise ValueError("missing candidate features") from error
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in values
    ):
        raise ValueError("invalid candidate features")
    plddt, rmsd, coverage, clashes = values
    if not 0 <= plddt <= 100 or rmsd < 0 or not 0 <= coverage <= 1 or clashes < 0:
        raise ValueError("candidate features outside supported ranges")
    return plddt, -rmsd, coverage, -clashes


def post_features(row: dict) -> dict:
    try:
        geometry = row["geometry"]["predicted"]
        values = [
            row["confidence"]["iptm"],
            geometry["hotspot_coverage"],
            geometry["clash_residue_pair_count"],
            row["design_consistency"]["binder_rmsd_after_target_alignment_angstrom"],
        ]
    except (KeyError, TypeError) as error:
        raise ValueError("missing observed complex features") from error
    if any(
        isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
        for x in values
    ):
        raise ValueError("invalid observed complex features")
    if (
        not 0 <= values[0] <= 1
        or not 0 <= values[1] <= 1
        or type(values[2]) is not int
        or values[2] < 0
        or values[3] < 0
    ):
        raise ValueError("observed complex features outside supported ranges")
    return {name: round(value, 6) for name, value in zip(POST_FIELDS, values, strict=True)}


def identity(a: str, b: str) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("selection diversity requires equal-length sequences")
    return sum(x == y for x, y in zip(a, b, strict=True)) / len(a)


def selection_state(
    pool: list[dict], observed: list[dict], config: SelectionConfig, spent: float
) -> dict:
    revealed = {row["candidate_id"]: row for row in observed}
    sequence_by_id = {x["candidate_id"]: x["sequence"] for x in pool}
    if (
        len(revealed) != len(observed)
        or len(sequence_by_id) != len(pool)
        or revealed.keys() - sequence_by_id.keys()
    ):
        raise ValueError("duplicate or unknown observation identities")
    candidates = []
    for candidate in pool:
        key = candidate["candidate_id"]
        pre_vector(candidate)
        visible = {k: candidate[k] for k in ["candidate_id", "seed", "length"]}
        visible["pre"] = {k: candidate["pre"][k] for k in PRE_FIELDS}
        visible["evaluated"] = key in revealed
        visible["max_sequence_identity_to_evaluated"] = (
            round(
                max(identity(sequence_by_id[key], sequence_by_id[other]) for other in revealed), 6
            )
            if revealed
            else None
        )
        if key in revealed:
            visible["post"] = post_features(revealed[key])
        candidates.append(visible)
    # Sequence similarities are cheap pre-complex information, shared by all policies.
    return {
        "candidates": candidates,
        "pairwise_sequence_identity": {
            a["candidate_id"]: {
                b["candidate_id"]: round(identity(a["sequence"], b["sequence"]), 6) for b in pool
            }
            for a in pool
        },
        "remaining_evaluations": max(0, config.max_evaluations - len(observed)),
        "batch_size": config.batch_size,
        "tool_wall_seconds_spent": spent,
        "stop_required": (
            "candidate_limit_reached"
            if len(observed) >= min(config.max_evaluations, len(pool))
            else "tool_wall_budget_exhausted"
            if config.tool_wall_budget_seconds is not None
            and spent >= config.tool_wall_budget_seconds
            else None
        ),
        "acceptance": None,
        "binding_validated": False,
    }


def stop_decision(reason: str) -> SelectionDecision:
    return SelectionDecision(action="stop", candidate_ids=[], reason=reason, evidence=[])


def choose_selection(config: SelectionConfig, state: dict) -> SelectionDecision:
    if state["stop_required"]:
        return stop_decision(state["stop_required"])
    if config.strategy == "harness":
        raise ValueError("harness strategy requires an external observe/apply decision")
    available = [x for x in state["candidates"] if not x["evaluated"]]
    count = min(config.batch_size, state["remaining_evaluations"], len(available))
    chosen = []
    if config.strategy == "fixed":
        chosen = sorted(available, key=lambda x: (x["seed"], x["candidate_id"]))[:count]
    else:
        compared = [x["candidate_id"] for x in state["candidates"] if x["evaluated"]]
        while len(chosen) < count:
            front = [
                a
                for a in available
                if not any(
                    all(x >= y for x, y in zip(pre_vector(b), pre_vector(a), strict=True))
                    and any(x > y for x, y in zip(pre_vector(b), pre_vector(a), strict=True))
                    for b in available
                )
            ]

            def order(candidate):
                similarities = state["pairwise_sequence_identity"][candidate["candidate_id"]]
                redundancy = max((similarities[key] for key in compared), default=0.0)
                return (
                    redundancy,
                    -candidate["pre"]["monomer_plddt"],
                    candidate["pre"]["monomer_design_rmsd"],
                    candidate["candidate_id"],
                )

            selected = min(front, key=order)
            chosen.append(selected)
            compared.append(selected["candidate_id"])
            available.remove(selected)
    return SelectionDecision(
        action="evaluate",
        candidate_ids=[x["candidate_id"] for x in chosen],
        reason="fixed_seed_order"
        if config.strategy == "fixed"
        else "pre_complex_pareto_then_sequence_diversity",
        evidence=[
            Evidence(
                candidate_id=x["candidate_id"],
                field="pre.monomer_plddt",
                value=float(x["pre"]["monomer_plddt"]),
            )
            for x in chosen
        ],
    )


def validate_selection(decision: SelectionDecision, state: dict) -> None:
    candidates = {x["candidate_id"]: x for x in state["candidates"]}
    if decision.action == "stop":
        if decision.candidate_ids:
            raise ValueError("stop cannot select candidates")
    else:
        ids = decision.candidate_ids
        if (
            state["stop_required"]
            or not ids
            or len(ids) > min(state["batch_size"], state["remaining_evaluations"])
        ):
            raise ValueError("evaluation exceeds the allowed action size or limits")
        if len(set(ids)) != len(ids) or any(
            key not in candidates or candidates[key]["evaluated"] for key in ids
        ):
            raise ValueError(
                "selection contains unknown, duplicate or already evaluated candidates"
            )
        if not decision.evidence:
            raise ValueError("evaluation decision must cite visible evidence")
    for evidence in decision.evidence:
        parts = evidence.field.split(".")
        if (
            len(parts) != 2
            or parts[0] not in {"pre", "post"}
            or parts[1] not in (PRE_FIELDS if parts[0] == "pre" else POST_FIELDS)
        ):
            raise ValueError("unknown evidence field")
        candidate = candidates.get(evidence.candidate_id, {})
        value = candidate.get(parts[0], {}).get(parts[1])
        if value is None or float(value) != evidence.value:
            raise ValueError("cited evidence is unobserved or differs from the visible value")

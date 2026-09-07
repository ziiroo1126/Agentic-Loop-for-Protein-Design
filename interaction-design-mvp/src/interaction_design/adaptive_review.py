"""Short host-authored decision summaries, grounded in paid observations."""

from __future__ import annotations


def _text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 1200:
        raise ValueError(f"{label} must be nonempty text of at most 1200 characters")


def validate_evidence(evidence: object, observation: dict) -> None:
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
        raise ValueError("one through twelve visible evidence references are required")
    visible = {
        (candidate["candidate_id"], row["seed"], field): row[field]
        for candidate in observation["candidates"]
        for row in candidate["predictions"]
        for field in ("iptm", "ipsae", "sc_dockq")
    }
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"candidate_id", "seed", "field", "value"}:
            raise ValueError("invalid evidence fields")
        if (
            not isinstance(item["candidate_id"], str)
            or type(item["seed"]) is not int
            or not isinstance(item["field"], str)
            or (item["value"] is not None and type(item["value"]) not in (int, float))
        ):
            raise ValueError("invalid evidence types")
        key = (item["candidate_id"], item["seed"], item["field"])
        if key not in visible or item["value"] != visible[key]:
            raise ValueError("evidence differs from a visible prediction")


def validate_plan(plan: object, observation: dict) -> None:
    if not isinstance(plan, dict) or set(plan) != {"question", "expectation", "evidence"}:
        raise ValueError("plan requires question, expectation and evidence")
    _text(plan["question"], "question")
    _text(plan["expectation"], "expectation")
    validate_evidence(plan["evidence"], observation)


def pending_reflection(event: dict | None) -> bool:
    return bool(event and "plan" in event["submission"] and "reflection" not in event)


def validate_reflection(reflection: object, event: dict, observation: dict) -> None:
    if "plan" not in event["submission"] or event["submission"]["action"] != "evaluate":
        raise ValueError("reflection requires a planned evaluation")
    fields = {"request_sha256", "outcome", "summary", "evidence"}
    if not isinstance(reflection, dict) or set(reflection) != fields:
        raise ValueError("invalid reflection fields")
    if reflection["request_sha256"] != observation["request_sha256"]:
        raise ValueError("stale or foreign reflection hash")
    if reflection["outcome"] not in ("supported", "refuted", "inconclusive"):
        raise ValueError("invalid reflection outcome")
    _text(reflection["summary"], "summary")
    validate_evidence(reflection["evidence"], observation)
    cid = event["submission"]["candidate_id"]
    candidate = next(item for item in observation["candidates"] if item["candidate_id"] == cid)
    latest_seed = candidate["predictions"][-1]["seed"]
    if not any(
        item["candidate_id"] == cid and item["seed"] == latest_seed
        for item in reflection["evidence"]
    ):
        raise ValueError("reflection must reference the newly revealed prediction")

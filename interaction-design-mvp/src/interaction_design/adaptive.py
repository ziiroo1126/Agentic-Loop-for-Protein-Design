"""Development-only sequential acquisition of cached, per-seed observations.

Private predictions and outcomes belong to the broker. Policies receive an explicit
projection of paid observations. This is an access protocol, not OS isolation.
An atomic event ledger supports restart, idempotency and deterministic replay.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import shutil
import statistics
import sys
import uuid
from pathlib import Path

from interaction_design.adaptive_review import (
    pending_reflection,
    validate_plan,
    validate_reflection,
)
from interaction_design.evaluation.jobs import job_lock, read_json
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json

INPUT_VERSION = "molclaw-adaptive-input-v1"
SESSION_VERSION = "molclaw-adaptive-development-v2"
IMPLEMENTATION = (
    "adaptive.py",
    "adaptive_policies.py",
    "adaptive_review.py",
    "persistence.py",
    "manifest.py",
)
FIELDS = ("iptm", "ipsae", "sc_dockq")
LIMITATIONS = [
    "Development replay on previously disclosed data; not independent validation.",
    "Prediction queries are simulated acquisition cost, not measured GPU time or savings.",
    "No new prediction, protein generation or wet-lab experiment was performed.",
    "Private-file restrictions are a protocol, not filesystem access control.",
    "Seeds and candidates do not constitute independent biological targets.",
]


def _identifier(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value) is not None


def validate_input(data: dict) -> None:
    """Reject ambiguous joins, partial seeds and unknown endpoints before preparing."""
    if not isinstance(data, dict) or data.get("schema_version") != INPUT_VERSION:
        raise ValueError("unsupported adaptive input")
    if data.get("purpose") != "development":
        raise ValueError("this development executor cannot certify an independent test")
    if not isinstance(data.get("source"), dict) or not data["source"]:
        raise ValueError("source provenance is required")
    pools = data.get("pools")
    if not isinstance(pools, list) or not pools:
        raise ValueError("nonempty pools are required")
    pool_ids, targets, source_ids = set(), set(), set()
    for pool in pools:
        if not isinstance(pool, dict) or not _identifier(pool.get("pool_id")):
            raise ValueError("invalid pool ID")
        if pool["pool_id"] in pool_ids:
            raise ValueError("duplicate pool ID")
        pool_ids.add(pool["pool_id"])
        target = pool.get("target")
        if not isinstance(target, str) or not target.strip() or target in targets:
            raise ValueError("a target must occur in exactly one pool")
        targets.add(target)
        candidates = pool.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise ValueError("nonempty candidates are required")
        ids = set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or not _identifier(candidate.get("candidate_id")):
                raise ValueError("invalid candidate ID")
            cid = candidate["candidate_id"]
            if cid in ids:
                raise ValueError("duplicate candidate ID")
            ids.add(cid)
            source_id = candidate.get("source_id")
            if not isinstance(source_id, str) or not source_id or source_id in source_ids:
                raise ValueError("duplicate or missing source ID")
            source_ids.add(source_id)
            if type(candidate.get("label")) is not bool:
                raise ValueError("a known boolean endpoint is required")
            predictions = candidate.get("predictions")
            if not isinstance(predictions, list) or len(predictions) != 5:
                raise ValueError("exactly five sequential seeds are required")
            for seed, prediction in enumerate(predictions):
                if not isinstance(prediction, dict) or set(prediction) != {"seed", *FIELDS}:
                    raise ValueError("unexpected prediction fields")
                if type(prediction["seed"]) is not int or prediction["seed"] != seed:
                    raise ValueError("seed order must be 0 through 4")
                for field in FIELDS:
                    value = prediction[field]
                    if value is not None and (
                        type(value) not in (float, int)
                        or not math.isfinite(value)
                        or not 0 <= value <= 1
                    ):
                        raise ValueError("scores must be finite numbers in [0, 1] or null")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_adaptive(
    imported: Path,
    output: Path,
    *,
    quota: int = 10,
    budget_per_candidate: int = 3,
    policy: str = "harness",
    require_review: bool = False,
) -> dict:
    data = read_json(imported)
    validate_input(data)
    if type(quota) is not int or not 1 <= quota <= min(
        len(pool["candidates"]) for pool in data["pools"]
    ):
        raise ValueError("quota must fit every candidate pool")
    if type(budget_per_candidate) is not int or not 1 <= budget_per_candidate <= 5:
        raise ValueError("budget per candidate must be an integer from 1 through 5")
    if policy not in {"harness", "uniform", "fixed_top", "boundary", "single"}:
        raise ValueError("unsupported development policy")
    if type(require_review) is not bool or (require_review and policy != "harness"):
        raise ValueError("required review is available only for the harness policy")
    if output.exists():
        raise ValueError("output must be a fresh directory")
    protocol = {
        "version": SESSION_VERSION,
        "purpose": "development",
        "quota": quota,
        "session_id": uuid.uuid4().hex,
        "budget_per_candidate": budget_per_candidate,
        "policy": policy,
        "require_review": require_review,
        "seed_order": list(range(5)),
        "cost_unit": "prediction_query",
        "initial_queries_per_candidate": 1,
        "score_fields": list(FIELDS),
        "pools": sorted(pool["pool_id"] for pool in data["pools"]),
        "limitations": LIMITATIONS,
    }
    output.mkdir(parents=True)
    shutil.copyfile(imported, output / "private-input.json")
    write_json(output / "protocol.json", protocol)
    files = ["private-input.json", "protocol.json"]
    implementation = output / "implementation"
    implementation.mkdir()
    for name in IMPLEMENTATION:
        shutil.copyfile(Path(__file__).with_name(name), implementation / name)
        files.append(f"implementation/{name}")
    manifest = {
        "version": SESSION_VERSION,
        "files": {name: _digest(output / name) for name in files},
    }
    write_json(output / "manifest.json", manifest)
    write_json(
        output / "ledger.json",
        {
            "manifest_sha256": canonical_sha256(manifest),
            "events": {pool: [] for pool in protocol["pools"]},
        },
    )
    return {
        "status": "running",
        "session": str(output),
        "pools": protocol["pools"],
        "policy": policy,
        "purpose": "development",
        "cost_unit": "prediction_query",
    }


def _load(session: Path) -> tuple[dict, dict, dict]:
    manifest = read_json(session / "manifest.json")
    if manifest.get("version") != SESSION_VERSION:
        raise ValueError("unsupported adaptive session")
    names = {"private-input.json", "protocol.json"} | {
        f"implementation/{name}" for name in IMPLEMENTATION
    }
    if set(manifest.get("files", {})) != names:
        raise ValueError("unexpected immutable file set")
    for name, digest in manifest["files"].items():
        if _digest(session / name) != digest:
            raise ValueError(f"immutable file changed: {name}")
        if name.startswith("implementation/") and (
            _digest(Path(__file__).with_name(Path(name).name)) != digest
        ):
            raise ValueError("implementation changed; use the archived implementation")
    data = read_json(session / "private-input.json")
    protocol = read_json(session / "protocol.json")
    ledger = read_json(session / "ledger.json")
    if ledger.get("manifest_sha256") != canonical_sha256(manifest):
        raise ValueError("ledger belongs to another manifest")
    if set(ledger) != {"manifest_sha256", "events"}:
        raise ValueError("unexpected ledger fields")
    if set(ledger["events"]) != set(protocol["pools"]):
        raise ValueError("ledger pool routing changed")
    if protocol["version"] != SESSION_VERSION or protocol["pools"] != sorted(
        pool["pool_id"] for pool in data["pools"]
    ):
        raise ValueError("protocol pool routing differs from input")
    return data, protocol, ledger


def _initial(pool: dict, protocol: dict) -> dict:
    count = len(pool["candidates"])
    return {
        "schema_version": "molclaw-adaptive-observation-v1",
        "pool_id": pool["pool_id"],
        "session_id": protocol["session_id"],
        "step": 0,
        "quota": protocol["quota"],
        "status": "running",
        "budget": {
            "limit": count * protocol["budget_per_candidate"],
            "used": count,
            "remaining": count * (protocol["budget_per_candidate"] - 1),
            "unit": "prediction_query",
        },
        "candidates": [
            {
                "candidate_id": candidate["candidate_id"],
                "predictions": [copy.deepcopy(candidate["predictions"][0])],
                "remaining_predictions": 4,
            }
            for candidate in sorted(pool["candidates"], key=lambda item: item["candidate_id"])
        ],
    }


def _seal(observation: dict) -> dict:
    observation.pop("request_sha256", None)
    observation["request_sha256"] = canonical_sha256(observation)
    return observation


def _transition(
    observation: dict, pool: dict, submission: dict, policy: str, require_review: bool = False
) -> dict:
    if observation["status"] != "running":
        raise ValueError("pool is already committed")
    if not isinstance(submission, dict):
        raise ValueError("submission must be an object")
    common = {"request_sha256", "action", "reason"}
    action = submission.get("action")
    specific = {"candidate_id"} if action == "evaluate" else {"candidate_ids"}
    optional = {"plan"} if action == "evaluate" and policy == "harness" else set()
    if (
        action not in {"evaluate", "select"}
        or not common | specific <= submission.keys()
        or not submission.keys() <= common | specific | optional
    ):
        raise ValueError("unexpected action or submission fields")
    if submission["request_sha256"] != observation["request_sha256"]:
        raise ValueError("stale or foreign observation hash")
    if not isinstance(submission["reason"], str) or not submission["reason"].strip():
        raise ValueError("nonempty action reason is required")
    if action == "evaluate" and require_review and "plan" not in submission:
        raise ValueError("this session requires an evaluation plan")
    if "plan" in submission:
        validate_plan(submission["plan"], observation)
    if policy != "harness":
        from interaction_design.adaptive_policies import choose_action

        expected = choose_action(copy.deepcopy(observation), policy)
        # Public apply and persisted replay must follow the same declared rule as
        # the in-process runner. Human wording may differ, scientific actions may
        # not; otherwise a feedback-driven action could masquerade as a baseline.
        if {key: value for key, value in submission.items() if key != "reason"} != {
            key: value for key, value in expected.items() if key != "reason"
        }:
            raise ValueError("submission differs from declared baseline policy")
    result = copy.deepcopy(observation)
    candidates = {candidate["candidate_id"]: candidate for candidate in result["candidates"]}
    if action == "evaluate":
        cid = submission["candidate_id"]
        if not isinstance(cid, str) or cid not in candidates:
            raise ValueError("unknown candidate")
        if result["budget"]["remaining"] <= 0:
            raise ValueError("prediction budget exhausted")
        candidate = candidates[cid]
        if candidate["remaining_predictions"] == 0:
            raise ValueError("all candidate seeds are already observed")
        private = next(item for item in pool["candidates"] if item["candidate_id"] == cid)
        candidate["predictions"].append(
            copy.deepcopy(private["predictions"][len(candidate["predictions"])])
        )
        candidate["remaining_predictions"] -= 1
        result["budget"]["used"] += 1
        result["budget"]["remaining"] -= 1
    else:
        ids = submission["candidate_ids"]
        if not isinstance(ids, list) or any(not isinstance(cid, str) for cid in ids):
            raise ValueError("selection must be a list of candidate IDs")
        if (
            len(ids) != result["quota"]
            or len(set(ids)) != len(ids)
            or not set(ids) <= candidates.keys()
        ):
            raise ValueError("selection must contain exactly quota unique known candidates")
        result["status"] = "committed"
        result["selected_candidate_ids"] = list(ids)
    result["step"] += 1
    return _seal(result)


def _walk(pool: dict, protocol: dict, events: list):
    """Yield validated public states; never project the pool's private fields."""
    observation = _seal(_initial(pool, protocol))
    if not isinstance(events, list):
        raise ValueError("invalid event ledger")
    previous = None
    yield observation, None
    for event in events:
        if not isinstance(event, dict) or set(event) not in (
            {"submission", "result_sha256"},
            {"submission", "result_sha256", "reflection"},
        ):
            raise ValueError("invalid event")
        if pending_reflection(previous):
            raise ValueError("complete the pending reflection before the next action")
        observation = _transition(
            observation, pool, event["submission"], protocol["policy"], protocol["require_review"]
        )
        if event["result_sha256"] != observation["request_sha256"]:
            raise ValueError("event result differs from paid observation replay")
        if "reflection" in event:
            validate_reflection(event["reflection"], event, observation)
        previous = event
        yield observation, event


def _replay(pool: dict, protocol: dict, events: list) -> dict:
    for state, _ in _walk(pool, protocol, events):
        observation = state
    return observation


def _pool(data: dict, pool_id: str) -> dict:
    for pool in data["pools"]:
        if pool["pool_id"] == pool_id:
            return pool
    raise ValueError("unknown pool")


def observe_adaptive(session: Path, pool_id: str) -> dict:
    data, protocol, ledger = _load(session)
    pool = _pool(data, pool_id)
    return _replay(pool, protocol, ledger["events"][pool_id])


def apply_adaptive(session: Path, pool_id: str, submission: dict) -> dict:
    with job_lock(session):
        data, protocol, ledger = _load(session)
        pool = _pool(data, pool_id)
        events = ledger["events"][pool_id]
        observation = _replay(pool, protocol, events)
        for event in events:
            if event["submission"] == submission:
                # A lost response can be retried even after subsequent valid actions.
                return {
                    "status": "already_applied",
                    "pool_id": pool_id,
                    "result_sha256": event["result_sha256"],
                    "current_request_sha256": observation["request_sha256"],
                }
        if (session / "report.json").exists():
            raise ValueError("session outcomes have already been revealed")
        if pending_reflection(events[-1] if events else None):
            raise ValueError("complete the pending reflection before the next action")
        result = _transition(
            observation, pool, submission, protocol["policy"], protocol["require_review"]
        )
        events.append(
            {"submission": copy.deepcopy(submission), "result_sha256": result["request_sha256"]}
        )
        # One atomic replacement commits action and charging together.
        write_json(session / "ledger.json", ledger)
        return {
            "status": "applied",
            "pool_id": pool_id,
            "step": result["step"],
            "result_sha256": result["request_sha256"],
            "budget": result["budget"],
        }


def reflect_adaptive(session: Path, pool_id: str, reflection: dict) -> dict:
    """Attach one review to its existing event, before any later action or reveal."""
    if not isinstance(reflection, dict):
        raise ValueError("reflection must be an object")
    with job_lock(session):
        data, protocol, ledger = _load(session)
        events = ledger["events"][_pool(data, pool_id)["pool_id"]]
        observation = _replay(_pool(data, pool_id), protocol, events)
        for event in events:
            if event.get("reflection") == reflection:
                return {"status": "already_reflected", "pool_id": pool_id}
        if (session / "report.json").exists():
            raise ValueError("session outcomes have already been revealed")
        if not events or not pending_reflection(events[-1]):
            raise ValueError("no pending reflection; existing reviews cannot be replaced")
        validate_reflection(reflection, events[-1], observation)
        events[-1]["reflection"] = copy.deepcopy(reflection)
        write_json(session / "ledger.json", ledger)
        return {"status": "reflected", "pool_id": pool_id, "step": observation["step"]}


def _history(pool: dict, protocol: dict, events: list) -> dict:
    rounds = []
    for observation, event in _walk(pool, protocol, events):
        if event is None:
            initial = observation
            continue
        row = {"step": observation["step"], "budget": observation["budget"], **event}
        if event["submission"]["action"] == "evaluate":
            cid = event["submission"]["candidate_id"]
            candidate = next(c for c in observation["candidates"] if c["candidate_id"] == cid)
            row["revealed_prediction"] = candidate["predictions"][-1]
        rounds.append(copy.deepcopy(row))
    return {
        "pool_id": pool["pool_id"],
        "policy": protocol["policy"],
        "require_review": protocol["require_review"],
        "initial_observation": initial,
        "rounds": rounds,
        "status": observation["status"],
        "pending_reflection": pending_reflection(events[-1] if events else None),
    }


def history_adaptive(session: Path, pool_id: str) -> dict:
    data, protocol, ledger = _load(session)
    return _history(_pool(data, pool_id), protocol, ledger["events"][pool_id])


def run_adaptive(session: Path) -> dict:
    """Execute declared pure baselines, passing only the paid public observation."""
    from interaction_design.adaptive_policies import choose_action

    with job_lock(session):
        data, protocol, ledger = _load(session)
        policy = protocol["policy"]
        if policy == "harness":
            raise ValueError("harness policy requires host observe/apply actions")
        for pool in data["pools"]:
            events = ledger["events"][pool["pool_id"]]
            observation = _replay(pool, protocol, events)
            while observation["status"] != "committed":
                if (session / "report.json").exists():
                    raise ValueError("session outcomes have already been revealed")
                submission = choose_action(copy.deepcopy(observation), policy)
                observation = _transition(observation, pool, submission, policy)
                events.append(
                    {"submission": submission, "result_sha256": observation["request_sha256"]}
                )
                # Same transition and atomic event commit as host apply, without
                # repeatedly replaying the full history for an in-process baseline.
                write_json(session / "ledger.json", ledger)
    return {"status": "committed", "pools": protocol["pools"], "policy": policy}


def _report(data: dict, protocol: dict, ledger: dict) -> dict:
    observations = {
        pool["pool_id"]: _replay(pool, protocol, ledger["events"][pool["pool_id"]])
        for pool in data["pools"]
    }
    if any(item["status"] != "committed" for item in observations.values()):
        raise ValueError("report requires every pool selection to be committed")
    rows = []
    for pool in data["pools"]:
        observation = observations[pool["pool_id"]]
        ids = observation["selected_candidate_ids"]
        candidates = {item["candidate_id"]: item for item in pool["candidates"]}
        hits = sum(candidates[cid]["label"] for cid in ids)
        rows.append(
            {
                "pool_id": pool["pool_id"],
                "target": pool["target"],
                "candidates": len(candidates),
                "quota": protocol["quota"],
                "hits": hits,
                "precision_at_quota": hits / protocol["quota"],
                "selected_candidate_ids": ids,
                "budget": observation["budget"],
                "evaluation_counts": {
                    item["candidate_id"]: len(item["predictions"])
                    for item in observation["candidates"]
                },
                "events": len(ledger["events"][pool["pool_id"]]),
            }
        )
    report = {
        "version": SESSION_VERSION,
        "purpose": "development",
        "policy": protocol["policy"],
        "by_target": rows,
        "macro_precision_at_quota": statistics.mean(row["precision_at_quota"] for row in rows),
        "total_hits": sum(row["hits"] for row in rows),
        "total_selected": sum(row["quota"] for row in rows),
        "total_prediction_queries": sum(row["budget"]["used"] for row in rows),
        "ledger_sha256": canonical_sha256(ledger),
        "limitations": LIMITATIONS,
    }
    return report


def report_adaptive(session: Path) -> dict:
    with job_lock(session):
        data, protocol, ledger = _load(session)
        report = _report(data, protocol, ledger)
        path = session / "report.json"
        if path.exists() and read_json(path) != report:
            raise ValueError("existing report differs from replayed ledger")
        write_json(path, report)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--imported", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--quota", type=int, default=10)
    prepare.add_argument("--budget-per-candidate", type=int, default=3)
    prepare.add_argument("--require-review", action="store_true")
    prepare.add_argument(
        "--policy",
        default="harness",
        choices=("harness", "uniform", "fixed_top", "boundary", "single"),
    )
    for command in ("observe", "apply", "reflect", "history", "run", "report", "export"):
        child = commands.add_parser(command)
        child.add_argument("session", type=Path)
        if command in {"observe", "apply", "reflect", "history"}:
            child.add_argument("--pool", required=True)
        if command in {"apply", "reflect"}:
            child.add_argument("--submission", type=Path, required=True)
        if command == "export":
            child.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare_adaptive(
                args.imported,
                args.output,
                quota=args.quota,
                budget_per_candidate=args.budget_per_candidate,
                policy=args.policy,
                require_review=args.require_review,
            )
        elif args.command == "observe":
            result = observe_adaptive(args.session, args.pool)
        elif args.command in {"apply", "reflect"}:
            submission = (
                json.load(sys.stdin) if str(args.submission) == "-" else read_json(args.submission)
            )
            operation = apply_adaptive if args.command == "apply" else reflect_adaptive
            result = operation(args.session, args.pool, submission)
        elif args.command == "history":
            result = history_adaptive(args.session, args.pool)
        elif args.command == "export":
            from interaction_design.adaptive_export import export_adaptive

            result = export_adaptive(args.session, args.output)
        elif args.command == "run":
            result = run_adaptive(args.session)
        else:
            result = report_adaptive(args.session)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"adaptive: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Harness-neutral, persisted observe/apply candidate screening.

The executor owns observations; host models submit evidence-grounded proposals.
Saved-result replay is an access protocol, not a filesystem security sandbox.
"""

from __future__ import annotations

import math
import shutil
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from interaction_design.assets import sha256_file
from interaction_design.campaign_tools import load_feedback
from interaction_design.evaluation.jobs import (
    checked_path,
    file_record,
    job_lock,
    read_json,
    timestamp_id,
)
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.selection import (
    SelectionConfig,
    SelectionDecision,
    choose_selection,
    decision_schema,
    post_features,
    selection_state,
    stop_decision,
    validate_selection,
)
from interaction_design.selection_catalogue import (
    build_catalogue,
    evaluate_selected,
    validate_complex_runtime,
    validate_replay,
)

SOURCE_FILES = ("screening.py", "selection.py", "selection_catalogue.py")


class Actor(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    harness: str = Field(min_length=1, max_length=120)
    model: str | None
    agent_id: str | None


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    decision: SelectionDecision
    actor: Actor


def prepare_screening(
    monomer_job: Path,
    config: SelectionConfig,
    *,
    feedback: Path | None = None,
    runtime: dict | None = None,
    artifacts: Path = Path("artifacts/screening"),
) -> Path:
    if (feedback is None) == (runtime is None):
        raise ValueError("provide exactly one of saved feedback or complex runtime")
    if feedback is not None and config.tool_wall_budget_seconds is not None:
        raise ValueError("replay cannot simulate live tool wall-time budgets")
    catalogue = build_catalogue(monomer_job.resolve())
    selection_state(catalogue["pool"], [], config, 0.0)
    payload = load_feedback(feedback) if feedback is not None else None
    if payload is not None:
        validate_replay(catalogue, payload)
    else:
        runtime = validate_complex_runtime(runtime, catalogue["model_policy"])
    directory = artifacts.resolve() / timestamp_id()
    directory.mkdir(parents=True)
    write_json(directory / "configuration.json", config.model_dump(mode="json"))
    write_json(directory / "catalogue.json", catalogue)
    write_json(
        directory / ("replay.json" if payload is not None else "runtime.json"),
        payload if payload is not None else runtime,
    )
    for name in SOURCE_FILES:
        shutil.copyfile(Path(__file__).with_name(name), directory / name)
    write_json(
        directory / "manifest.json",
        {
            "stage": "candidate_screening",
            "mode": "saved_complex_replay" if payload is not None else "local_complex",
            "sources": catalogue["sources"]
            + (
                [
                    {
                        "path": str(feedback.resolve() / "observations.json"),
                        "sha256": sha256_file(feedback / "observations.json"),
                    }
                ]
                if feedback is not None
                else []
            ),
            "inputs": [file_record(p, directory) for p in sorted(directory.iterdir())],
        },
    )
    write_json(directory / "status.json", {"status": "prepared"})
    write_json(directory / "progress.json", {"receipts": []})
    return directory


def _load(directory: Path) -> tuple[dict, SelectionConfig, dict]:
    manifest = read_json(directory / "manifest.json")
    if manifest["stage"] != "candidate_screening":
        raise ValueError("not a screening session")
    for record in manifest["inputs"]:
        checked_path(directory, record)
    for record in manifest["sources"]:
        if sha256_file(Path(record["path"])) != record["sha256"]:
            raise ValueError("screening source changed")
    for name in SOURCE_FILES:
        if sha256_file(directory / name) != sha256_file(Path(__file__).with_name(name)):
            raise ValueError("screening implementation changed; prepare a new session")
    return (
        manifest,
        SelectionConfig.model_validate(read_json(directory / "configuration.json")),
        read_json(directory / "catalogue.json"),
    )


def _request(directory: Path, index: int, config: SelectionConfig, state: dict) -> dict:
    payload = {
        "session_id": directory.name,
        "step": index,
        "configuration": config.model_dump(mode="json"),
        "visible_state": state,
        "instructions": [
            "Choose evaluate or stop using only this visible state.",
            "Cite exact visible numeric values in evidence; unseen post metrics are unavailable.",
            "Pre metrics describe monomer refolding and the generated design geometry.",
            "Post metrics describe predicted complexes, without experimental binding validation.",
            "Do not inspect replay files or unselected complex outputs.",
        ],
    }
    digest = canonical_sha256(payload)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["request_sha256", "decision", "actor"],
        "properties": {
            "request_sha256": {"type": "string", "enum": [digest]},
            "decision": decision_schema(),
            "actor": {
                "type": "object",
                "additionalProperties": False,
                "required": ["harness", "model", "agent_id"],
                "properties": {
                    "harness": {"type": "string"},
                    "model": {"type": ["string", "null"]},
                    "agent_id": {"type": ["string", "null"]},
                },
            },
        },
    }
    return {"request_sha256": digest, "payload": payload, "response_schema": schema}


def _check_result(result: dict, ids: list[str], catalogue: dict, mode: str) -> None:
    rows = result["observations"]
    if [row["candidate_id"] for row in rows] != ids:
        raise ValueError("tool results differ from the requested candidate order or set")
    if (
        result["model_policy"] != catalogue["model_policy"]
        or result["geometry_policy"] != catalogue["geometry_policy"]
        or result["mode"] != mode
        or result["new_model_inference"] is not (mode == "local_complex")
    ):
        raise ValueError("tool changed the frozen protocol or execution mode")
    by_id = {row["candidate_id"]: row for row in catalogue["pool"]}
    for row in rows:
        post_features(row)
        if (
            row["generation_seed"] != by_id[row["candidate_id"]]["seed"]
            or row["model_policy"] != catalogue["model_policy"]
            or row["acceptance"] is not None
            or row["binding_validated"] is not False
        ):
            raise ValueError("tool changed candidate identity or diagnostic-only acceptance")


def _files(step: Path) -> list[Path]:
    return [
        p
        for p in sorted(step.rglob("*"))
        if p.is_file() and p != step / "receipt.json" and p.name != ".lock"
    ]


def _history(directory: Path, manifest: dict, config: SelectionConfig, catalogue: dict):
    observed, intents, spent, pending = [], [], 0.0, None
    steps = sorted((directory / "steps").glob("*"))
    progress = read_json(directory / "progress.json")["receipts"]
    for record in progress:
        checked_path(directory, record)
    for index, step in enumerate(steps):
        if step.name != f"{index:04d}" or not step.is_dir():
            raise ValueError("missing or unexpected screening step")
        if intents and intents[-1]["decision"]["action"] == "stop":
            raise ValueError("steps exist after stop")
        request = _request(
            directory, index, config, selection_state(catalogue["pool"], observed, config, spent)
        )
        if read_json(step / "request.json") != request:
            raise ValueError("saved request differs from reconstructed visible history")
        if not (step / "receipt.json").exists():
            if index != len(steps) - 1 or _files(step) != [step / "request.json"]:
                raise ValueError("unfinished intent; inspect preserved work before continuing")
            pending = request
            break
        receipt = read_json(step / "receipt.json")
        if receipt["status"] != "completed":
            raise ValueError("failed or running step; automatic retry is disabled")
        if index >= len(progress) or progress[index] != file_record(
            step / "receipt.json", directory
        ):
            raise ValueError("completed receipt lacks its progress record; inspect preserved work")
        names = [r["path"] for r in receipt["artifacts"]]
        if (
            len(set(names)) != len(names)
            or set(names) != {str(p.relative_to(step)) for p in _files(step)}
            or not {"request.json", "submission.json"} <= set(names)
        ):
            raise ValueError("step receipt lacks exact artifact coverage")
        for record in receipt["artifacts"]:
            checked_path(step, record)
        submission = Submission.model_validate(read_json(step / "submission.json"))
        if submission.request_sha256 != request["request_sha256"]:
            raise ValueError("submission request identity mismatch")
        validate_selection(submission.decision, request["payload"]["visible_state"])
        if submission.decision.action == "evaluate":
            if "result.json" not in names:
                raise ValueError("evaluation receipt lacks result")
            result = read_json(step / "result.json")
            _check_result(result, submission.decision.candidate_ids, catalogue, manifest["mode"])
            observed.extend(result["observations"])
        elapsed = receipt["tool_wall_seconds"]
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            raise ValueError("invalid recorded tool time")
        spent += elapsed
        intents.append(submission.model_dump(mode="json"))
    if len(progress) != len(intents):
        raise ValueError("progress record differs from completed steps")
    return observed, intents, spent, pending


def _finish(
    directory: Path,
    manifest: dict,
    config: SelectionConfig,
    catalogue: dict,
    observed: list[dict],
    intents: list[dict],
    spent: float,
) -> dict:
    report = {
        "status": "completed",
        "mode": manifest["mode"],
        "strategy": config.strategy,
        "candidate_count": len(catalogue["pool"]),
        "evaluated_count": len(observed),
        "selection_order": [r["candidate_id"] for r in observed],
        "observations": [
            {"candidate_id": r["candidate_id"], "post": post_features(r)} for r in observed
        ],
        "decisions": intents,
        "stop_reason": intents[-1]["decision"]["reason"],
        "new_model_inference": manifest["mode"] == "local_complex" and bool(observed),
        "tool_wall_seconds": spent,
        "host_token_usage": None,
        "actor_metadata_verified": False,
        "acceptance": None,
        "binding_validated": False,
        "scope": "development selection trace; no held-out policy comparison or binding labels",
    }
    if (directory / "completed.json").exists():
        for record in read_json(directory / "completed.json")["artifacts"]:
            checked_path(directory, record)
        if read_json(directory / "report.json") != report:
            raise ValueError("completion report differs from recorded history")
    else:
        write_json(directory / "report.json", report)
        records = [file_record(directory / "report.json", directory)] + [
            file_record(p, directory) for p in sorted((directory / "steps").glob("*/receipt.json"))
        ]
        write_json(directory / "completed.json", {"artifacts": records})
        write_json(directory / "status.json", {"status": "completed"})
    return report


def observe_screening(directory: Path) -> dict:
    directory = directory.resolve()
    with job_lock(directory):
        manifest, config, catalogue = _load(directory)
        observed, intents, spent, pending = _history(directory, manifest, config, catalogue)
        if intents and intents[-1]["decision"]["action"] == "stop":
            return _finish(directory, manifest, config, catalogue, observed, intents, spent)
        state = selection_state(catalogue["pool"], observed, config, spent)
        request = pending or _request(directory, len(intents), config, state)
        if pending is None:
            write_json(directory / "steps" / f"{len(intents):04d}" / "request.json", request)
    if state["stop_required"]:
        apply_screening(
            directory,
            {
                "request_sha256": request["request_sha256"],
                "decision": stop_decision(state["stop_required"]).model_dump(mode="json"),
                "actor": {"harness": "executor", "model": None, "agent_id": None},
            },
        )
        return observe_screening(directory)
    return request


def apply_screening(directory: Path, proposal: dict) -> dict:
    directory = directory.resolve()
    with job_lock(directory):
        manifest, config, catalogue = _load(directory)
        observed, intents, spent, pending = _history(directory, manifest, config, catalogue)
        try:
            submission = Submission.model_validate(proposal)
            normalized = submission.model_dump(mode="json")
            for index, prior in enumerate(intents):
                if prior["request_sha256"] == submission.request_sha256:
                    if prior != normalized:
                        raise ValueError("completed request has a different submission")
                    return {"status": "already_applied", "step": index}
            if pending is None or submission.request_sha256 != pending["request_sha256"]:
                raise ValueError("stale or missing observe request")
            validate_selection(submission.decision, pending["payload"]["visible_state"])
        except ValueError as error:
            write_json(
                directory / "rejections" / f"{timestamp_id()}.json",
                {"error": str(error), "proposal": proposal},
            )
            raise
        step = directory / "steps" / f"{len(intents):04d}"
        write_json(step / "submission.json", normalized)
        write_json(step / "receipt.json", {"status": "running"})
        write_json(directory / "status.json", {"status": "running", "step": step.name})
        started = time.monotonic()
        elapsed = 0.0
        result = None
        try:
            if submission.decision.action == "evaluate":
                remaining = (
                    None
                    if config.tool_wall_budget_seconds is None
                    else config.tool_wall_budget_seconds - spent
                )
                replay = manifest["mode"] == "saved_complex_replay"
                result = evaluate_selected(
                    catalogue,
                    submission.decision.candidate_ids,
                    step / "tools",
                    remaining,
                    replay=read_json(directory / "replay.json") if replay else None,
                    runtime=None if replay else read_json(directory / "runtime.json"),
                )
                elapsed = time.monotonic() - started
                _check_result(
                    result, submission.decision.candidate_ids, catalogue, manifest["mode"]
                )
                write_json(step / "result.json", result)
            write_json(
                step / "receipt.json",
                {
                    "status": "completed",
                    "tool_wall_seconds": elapsed,
                    "artifacts": [file_record(p, step) for p in _files(step)],
                },
            )
            write_json(
                directory / "progress.json",
                {
                    "receipts": [
                        file_record(p, directory)
                        for p in sorted((directory / "steps").glob("*/receipt.json"))
                    ],
                },
            )
            write_json(directory / "status.json", {"status": "awaiting_observation"})
        except BaseException as error:
            write_json(
                step / "receipt.json",
                {
                    "status": "failed",
                    "tool_wall_seconds": time.monotonic() - started,
                    "error": str(error),
                    "artifacts": [file_record(p, step) for p in _files(step)],
                },
            )
            write_json(directory / "status.json", {"status": "failed", "step": step.name})
            raise
        return {
            "status": "applied",
            "step": len(intents),
            "candidate_ids": submission.decision.candidate_ids,
            "observations": []
            if result is None
            else [
                {"candidate_id": r["candidate_id"], "post": post_features(r)}
                for r in result["observations"]
            ],
            "next": "screen observe",
        }


def run_screening(directory: Path) -> dict:
    while True:
        request = observe_screening(directory)
        if request.get("status") == "completed":
            return request
        config = SelectionConfig.model_validate(request["payload"]["configuration"])
        if config.strategy == "harness":
            return request
        decision = choose_selection(config, request["payload"]["visible_state"])
        apply_screening(
            directory,
            {
                "request_sha256": request["request_sha256"],
                "decision": decision.model_dump(mode="json"),
                "actor": {"harness": "builtin", "model": None, "agent_id": config.strategy},
            },
        )

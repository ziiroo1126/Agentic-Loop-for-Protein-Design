"""Persisted sample/observe/stop campaigns with interchangeable decision policies."""

from __future__ import annotations

import math
import shutil
import time
from copy import deepcopy
from pathlib import Path

from interaction_design.assets import sha256_file
from interaction_design.campaign_tools import LocalCampaignTools, ReplayTools, load_feedback
from interaction_design.decisions import (
    Decision,
    DecisionConfig,
    choose_decision,
    objective_vector,
    validate_decision,
    visible_state,
)
from interaction_design.evaluation.complex import default_policy
from interaction_design.evaluation.feedback import feedback_policy_path
from interaction_design.evaluation.jobs import (
    checked_path,
    file_record,
    job_lock,
    read_json,
    timestamp_id,
)
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.specs import FixedSegment, GeneratedSegment, InteractionDesignSpec

SOURCE_FILES = ("campaigns.py", "campaign_tools.py", "decisions.py")
OBJECTIVES = [
    {"name": "iptm", "direction": "maximize"},
    {"name": "requested_hotspot_coverage", "direction": "maximize"},
    {"name": "clash_residue_pairs", "direction": "minimize"},
    {"name": "binder_rmsd_after_target_alignment", "direction": "minimize"},
]


def _task_identity(task: dict) -> str:
    value = InteractionDesignSpec.model_validate(task).model_dump(mode="json")
    value.pop("name")
    value.pop("description")
    value["generation"].pop("seeds")
    return canonical_sha256(value)


def _validate_task(spec: InteractionDesignSpec) -> None:
    if (
        len(spec.molecules) != 2
        or any(m.type != "protein" or m.cyclic for m in spec.molecules)
        or not spec.hotspots
        or spec.generation.samples_per_seed != 1
        or spec.generation.inverse_fold_topk != 1
    ):
        raise ValueError(
            "campaign v1 requires two protein chains, hotspots and one sample per seed"
        )
    binder = spec.molecules[spec.binder_index]
    target = spec.molecules[1 - spec.binder_index]
    if (
        binder.role != "design"
        or target.role != "context"
        or not all(
            isinstance(s, GeneratedSegment) and s.min_length == s.max_length
            for s in binder.segments
        )
        or not all(isinstance(s, FixedSegment) for s in target.segments)
    ):
        raise ValueError("campaign v1 requires a fixed-length generated binder and fixed target")


def prepare_campaign(
    config: DecisionConfig,
    *,
    artifacts: Path = Path("artifacts/campaigns"),
    feedback: Path | None = None,
    task: InteractionDesignSpec | None = None,
    runtime: dict | None = None,
    replay: bool = False,
) -> Path:
    payload = load_feedback(feedback) if feedback else None
    schedule = set(
        range(config.first_seed, config.first_seed + config.batch_size * config.max_rounds)
    )
    if max(schedule) > 2**31 - 1:
        raise ValueError("campaign seed schedule exceeds the supported range")
    model_policy, geometry_policy = default_policy(), read_json(feedback_policy_path())
    if replay:
        if not payload or task is not None or runtime is not None:
            raise ValueError("replay requires saved feedback and no model runtime/task")
        if config.tool_wall_budget_seconds is not None:
            raise ValueError("replay cannot simulate live tool wall-time budgets")
        if not schedule <= {x["generation_seed"] for x in payload["observations"]}:
            raise ValueError("frozen replay seed schedule exceeds saved observations")
        model_policy, geometry_policy = payload["model_policy"], payload["policy"]
    else:
        if task is None or runtime is None:
            raise ValueError("local campaign requires a task and runtime")
        _validate_task(task)
        LocalCampaignTools(task.model_dump(mode="json"), runtime)
        if not task.reference_structure or not Path(task.reference_structure).is_file():
            raise ValueError("campaign target reference is unavailable")
        if payload:
            if schedule & {x["generation_seed"] for x in payload["observations"]}:
                raise ValueError("campaign schedule overlaps initial feedback seeds")
            for input_job in payload["inputs"]:
                job_manifest = read_json(Path(input_job["job"]) / "manifest.json")
                prior = read_json(Path(job_manifest["generation_run"]) / "manifest.json")
                if _task_identity(prior["task"]) != _task_identity(task.model_dump(mode="json")):
                    raise ValueError("initial feedback belongs to different task constraints")
            if not payload["inputs"]:
                raise ValueError("initial feedback lacks source tasks")
            if payload["model_policy"] != model_policy or payload["policy"] != geometry_policy:
                raise ValueError("initial feedback protocols differ from the current tools")
    directory = artifacts.resolve() / timestamp_id()
    directory.mkdir(parents=True)
    write_json(directory / "strategy.json", config.model_dump(mode="json"))
    write_json(directory / "model-policy.json", model_policy)
    write_json(directory / "geometry-policy.json", geometry_policy)
    write_json(
        directory / "initial.json",
        {"observations": [] if replay or not payload else payload["observations"]},
    )
    sources = []
    if feedback:
        sources.append(
            {
                "path": str(feedback.resolve() / "observations.json"),
                "sha256": sha256_file(feedback / "observations.json"),
            }
        )
    if replay:
        write_json(directory / "replay.json", payload)
    else:
        write_json(directory / "task.json", task.model_dump(mode="json"))
        write_json(directory / "runtime.json", runtime)
        sources.append(
            {
                "path": task.reference_structure,
                "sha256": sha256_file(Path(task.reference_structure)),
            }
        )
    for name in SOURCE_FILES:
        shutil.copyfile(Path(__file__).with_name(name), directory / name)
    write_json(
        directory / "manifest.json",
        {
            "stage": "decision_campaign",
            "mode": "saved_observation_replay" if replay else "local_models",
            "objectives": OBJECTIVES,
            "acceptance": None,
            "sources": sources,
            "inputs": [
                file_record(p, directory) for p in sorted(directory.iterdir()) if p.is_file()
            ],
        },
    )
    write_json(directory / "status.json", {"status": "prepared"})
    return directory


def _load(directory: Path) -> tuple[dict, DecisionConfig]:
    manifest = read_json(directory / "manifest.json")
    if manifest["stage"] != "decision_campaign":
        raise ValueError("not a decision campaign")
    for record in manifest["inputs"]:
        checked_path(directory, record)
    for source in manifest["sources"]:
        if sha256_file(Path(source["path"])) != source["sha256"]:
            raise ValueError("campaign source changed")
    for name in SOURCE_FILES:
        if sha256_file(directory / name) != sha256_file(Path(__file__).with_name(name)):
            raise ValueError("controller implementation changed; prepare a new campaign")
    return manifest, DecisionConfig.model_validate(read_json(directory / "strategy.json"))


def _history(
    directory: Path, config: DecisionConfig, initial: list[dict]
) -> tuple[list[dict], list[dict], float]:
    rounds, decisions, spent = [], [], 0.0
    for index, step in enumerate(sorted((directory / "steps").glob("*"))):
        if step.name != f"{index:04d}" or not (step / "receipt.json").is_file():
            raise ValueError("unfinished or missing step; inspect before any further execution")
        receipt = read_json(step / "receipt.json")
        if receipt["status"] != "completed":
            raise ValueError("failed or unfinished step; inspect preserved work before continuing")
        records = receipt["artifacts"]
        paths = [x["path"] for x in records]
        if len(set(paths)) != len(paths) or "intent.json" not in paths:
            raise ValueError("step receipt lacks unique required artifacts")
        for record in records:
            checked_path(step, record)
        elapsed = receipt["tool_wall_seconds"]
        if isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid recorded tool time")
        intent = read_json(step / "intent.json")
        state = visible_state(initial, rounds, spent)
        if intent["visible_state"] != state:
            raise ValueError("recorded decision context differs from observed history")
        decision = Decision.model_validate(intent["decision"])
        validate_decision(decision, config, state)
        if decisions and decisions[-1]["decision"]["action"] == "stop":
            raise ValueError("steps exist after a stop decision")
        decisions.append(intent)
        if intent["decision"]["action"] == "sample":
            if "result.json" not in paths:
                raise ValueError("sample receipt lacks its result")
            result = read_json(step / "result.json")
            _check_result(result, decision.seeds, state, directory)
            rounds.append(result)
        spent += elapsed
    return rounds, decisions, spent


def _check_result(result: dict, seeds: list[int], state: dict, directory: Path) -> None:
    rows = result["observations"]
    if len(rows) != len(seeds) or sorted(x["generation_seed"] for x in rows) != sorted(seeds):
        raise ValueError("tool results do not cover the exact requested seed batch")
    ids = [x["candidate_id"] for x in rows]
    if len(set(ids)) != len(ids) or set(ids) & set(state["observed_candidate_ids"]):
        raise ValueError("tool returned repeated candidate identities")
    if result["model_policy"] != read_json(directory / "model-policy.json") or (
        result["geometry_policy"] != read_json(directory / "geometry-policy.json")
    ):
        raise ValueError("tool changed a frozen evaluation protocol")
    for row in rows:
        objective_vector(row)
        if row["acceptance"] is not None or row["binding_validated"] is not False:
            raise ValueError("tool result must retain diagnostic-only acceptance")


def _receipt(step: Path, status: str, elapsed: float, error: str | None = None) -> None:
    write_json(
        step / "receipt.json",
        {
            "status": status,
            "tool_wall_seconds": elapsed,
            "error": error,
            "artifacts": [
                file_record(p, step)
                for p in sorted(step.rglob("*"))
                if p.is_file() and p.name not in {".lock", "receipt.json"}
            ],
        },
    )


def run_campaign(directory: Path, *, tools=None, policy=None) -> Path:
    """Execute built-in baselines; injectable tools/policy share the same checked boundary."""
    directory = directory.resolve()
    with job_lock(directory):
        manifest, config = _load(directory)
        initial = read_json(directory / "initial.json")["observations"]
        rounds, decisions, spent = _history(directory, config, initial)
        stopped = bool(decisions and decisions[-1]["decision"]["action"] == "stop")
        if (directory / "completed.json").is_file():
            completed = read_json(directory / "completed.json")
            if not stopped or len(completed["steps"]) != len(decisions):
                raise ValueError("completion receipt differs from the recorded decisions")
            for record in [completed["report"], *completed["steps"]]:
                checked_path(directory, record)
            return directory / "report.json"
        if tools is None and not stopped:
            tools = (
                ReplayTools(read_json(directory / "replay.json"))
                if manifest["mode"] == "saved_observation_replay"
                else LocalCampaignTools(
                    read_json(directory / "task.json"), read_json(directory / "runtime.json")
                )
            )
        chooser = choose_decision if policy is None else policy
        while not stopped:
            state = visible_state(initial, rounds, spent)
            try:
                proposal = chooser(config.model_copy(deep=True), deepcopy(state))
                decision = Decision.model_validate(
                    proposal.model_dump() if isinstance(proposal, Decision) else proposal
                )
                validate_decision(decision, config, state)
            except Exception as error:
                write_json(
                    directory / "rejections" / f"{timestamp_id()}.json",
                    {"error": str(error), "visible_state": state},
                )
                write_json(
                    directory / "status.json", {"status": "decision_rejected", "error": str(error)}
                )
                raise
            step = directory / "steps" / f"{len(decisions):04d}"
            step.mkdir(parents=True)
            intent = {"decision": decision.model_dump(mode="json"), "visible_state": state}
            write_json(step / "intent.json", intent)
            write_json(step / "receipt.json", {"status": "running"})
            write_json(directory / "status.json", {"status": "running", "step": step.name})
            started = time.monotonic()
            elapsed = 0.0
            try:
                if decision.action == "sample":
                    remaining = (
                        None
                        if config.tool_wall_budget_seconds is None
                        else config.tool_wall_budget_seconds - spent
                    )
                    result = tools.sample(decision.seeds, step, remaining)
                    elapsed = time.monotonic() - started
                    _check_result(result, decision.seeds, state, directory)
                    write_json(step / "result.json", result)
                    rounds.append(result)
                _receipt(step, "completed", elapsed)
            except BaseException as error:
                elapsed = time.monotonic() - started
                _receipt(step, "failed", elapsed, str(error))
                write_json(
                    directory / "status.json",
                    {
                        "status": "failed",
                        "step": step.name,
                        "error": str(error),
                        "tool_wall_seconds_spent": spent + elapsed,
                    },
                )
                raise
            spent += elapsed
            decisions.append(intent)
            if decision.action == "stop":
                break
        report = {
            "mode": manifest["mode"],
            "strategy": config.model_dump(mode="json"),
            "objectives": OBJECTIVES,
            "state": visible_state(initial, rounds, spent),
            "initial_candidate_count": len(initial),
            "newly_observed_candidate_count": sum(len(x["observations"]) for x in rounds),
            "new_model_inference": any(x["new_model_inference"] for x in rounds),
            "decisions": [x["decision"] for x in decisions],
            "acceptance": None,
            "binding_validated": False,
            "scope": "development controller trace; no measured policy advantage",
        }
        path = write_json(directory / "report.json", report)
        lines = [
            "# Decision campaign",
            "",
            f"Mode: {manifest['mode']}; strategy: {config.strategy}.",
            "",
            "| Step | Action | Seeds | Reason |",
            "| --- | --- | --- | --- |",
        ]
        for index, intent in enumerate(decisions):
            decision = intent["decision"]
            lines.append(
                f"| {index} | {decision['action']} | {decision['seeds']} | {decision['reason']} |"
            )
        lines += [
            "",
            "Diagnostic Pareto tradeoffs are not biological acceptance or measured Agent gains.",
            "",
        ]
        (directory / "report.md").write_text("\n".join(lines))
        write_json(
            directory / "completed.json",
            {
                "report": file_record(path, directory),
                "steps": [
                    file_record(p, directory)
                    for p in sorted((directory / "steps").glob("*/receipt.json"))
                ],
            },
        )
        write_json(directory / "status.json", {"status": "completed"})
        return path

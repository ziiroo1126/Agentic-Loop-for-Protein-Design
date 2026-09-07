"""Persistent task-to-export orchestration over the existing scientific tools.

Completed stages are validated and reused. An interrupted or failed stage is kept
for inspection and never silently resubmitted. Host decisions remain in screening.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from interaction_design.assets import default_asset_lock, load_odesign_revision, sha256_file
from interaction_design.evaluation.jobs import (
    checked_path,
    file_record,
    job_lock,
    read_json,
    timestamp_id,
)
from interaction_design.evaluation.monomer import (
    prepare_monomer_batch,
    run_monomer_batch,
)
from interaction_design.evaluation.monomer_report import read_monomer_batch
from interaction_design.generator import ODesignGenerator
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.pipeline_config import validate_pipeline_runtime, validate_pipeline_task
from interaction_design.runtime import LocalODesignExecutor
from interaction_design.screening import (
    apply_screening,
    observe_screening,
    prepare_screening,
    run_screening,
)
from interaction_design.selection import SelectionConfig
from interaction_design.specs import InteractionDesignSpec
from interaction_design.workflow import DesignWorkflow

STAGES = ("generation", "monomer", "screening")
SOURCES = ("pipeline.py", "pipeline_config.py")


def prepare_pipeline(
    task: InteractionDesignSpec,
    runtime: dict,
    selection: SelectionConfig,
    *,
    artifacts: Path = Path("artifacts/pipelines"),
) -> Path:
    """Freeze a validated local protein-binder task without running any models."""
    task = task.model_copy(deep=True)
    validate_pipeline_task(task)
    runtime = validate_pipeline_runtime(task, runtime)
    original_task_sha256 = canonical_sha256(task.model_dump(mode="json"))
    directory = artifacts.resolve() / timestamp_id()
    directory.mkdir(parents=True, exist_ok=False)
    reference = Path(task.reference_structure)
    target = directory / "inputs" / ("target" + reference.suffix.lower())
    target.parent.mkdir()
    shutil.copyfile(reference, target)
    if sha256_file(reference) != sha256_file(target):
        raise ValueError("reference changed while preparing pipeline")
    task.reference_structure = str(target)
    write_json(directory / "task.json", task.model_dump(mode="json"))
    write_json(directory / "runtime.json", runtime)
    write_json(directory / "selection.json", selection.model_dump(mode="json"))
    for name in SOURCES:
        shutil.copyfile(Path(__file__).with_name(name), directory / name)
    write_json(
        directory / "manifest.json",
        {
            "stage": "binder_pipeline",
            "version": "binder-pipeline-v1",
            "mode": "local_models",
            "original_task_sha256": original_task_sha256,
            "inputs": [
                file_record(p, directory) for p in sorted(directory.rglob("*")) if p.is_file()
            ],
            "evaluation": {
                "monomer": "ESMFold v1",
                "complex": "ESMFold2",
                "legacy_task_metric_rules_used": False,
                "acceptance": None,
                "binding_validated": False,
            },
        },
    )
    write_json(directory / "progress.json", {"receipts": []})
    write_json(directory / "status.json", {"status": "prepared"})
    return directory


def _within(directory: Path, relative: str) -> Path:
    path = (directory / relative).resolve()
    if Path(relative).is_absolute() or not path.is_relative_to(directory):
        raise ValueError("pipeline stage path escapes its directory")
    return path


def _load(directory: Path) -> tuple[InteractionDesignSpec, dict, SelectionConfig]:
    manifest = read_json(directory / "manifest.json")
    if (
        manifest.get("stage") != "binder_pipeline"
        or manifest.get("version") != "binder-pipeline-v1"
    ):
        raise ValueError("not a supported binder pipeline")
    for record in manifest["inputs"]:
        checked_path(directory, record)
    for name in SOURCES:
        if sha256_file(directory / name) != sha256_file(Path(__file__).with_name(name)):
            raise ValueError("pipeline implementation changed; use its saved code or prepare anew")
    return (
        InteractionDesignSpec.model_validate(read_json(directory / "task.json")),
        read_json(directory / "runtime.json"),
        SelectionConfig.model_validate(read_json(directory / "selection.json")),
    )


def _validate_generation(directory: Path, path: Path, task: InteractionDesignSpec) -> None:
    manifest = read_json(path / "manifest.json")
    if manifest.get("stage") != "generation" or manifest.get("status") != "generated":
        raise ValueError("pipeline generation is incomplete")
    if manifest["task_sha256"] != canonical_sha256(task.model_dump(mode="json")) or manifest[
        "task"
    ] != task.model_dump(mode="json"):
        raise ValueError("generation differs from frozen pipeline task")
    for record in manifest["artifacts"]:
        checked_path(path, record)


def _history(directory: Path, task: InteractionDesignSpec) -> list[dict]:
    anchors = read_json(directory / "progress.json")["receipts"]
    paths = [directory / "stages" / f"{name}.json" for name in STAGES]
    actual = list((directory / "stages").glob("*.json"))
    if set(actual) - set(paths):
        raise ValueError("unexpected pipeline stage receipt")
    completed = []
    for index, name in enumerate(STAGES):
        path = paths[index]
        if not path.exists():
            if any(p.exists() for p in paths[index + 1 :]) or len(anchors) != index:
                raise ValueError("missing pipeline stage receipt")
            break
        receipt = read_json(path)
        if receipt.get("stage") != name:
            raise ValueError("pipeline stage identity changed")
        if receipt["status"] != "completed":
            if len(anchors) != index or any(p.exists() for p in paths[index + 1 :]):
                raise ValueError("inconsistent unfinished pipeline history")
            raise ValueError(
                f"pipeline {name} is {receipt['status']}; "
                "inspect its saved records before retrying; "
                "automatic resubmission is disabled"
            )
        if index >= len(anchors) or anchors[index] != file_record(path, directory):
            raise ValueError("pipeline stage receipt is unanchored or changed")
        checked_path(directory, anchors[index])
        for record in receipt["artifacts"]:
            checked_path(directory, record)
        result = receipt["result"]
        if name == "generation":
            _validate_generation(directory, _within(directory, result["generation_run"]), task)
        elif name == "monomer":
            job = _within(directory, result["monomer_job"])
            if Path(read_json(job / "manifest.json")["generation_run"]).resolve() != _within(
                directory, completed[0]["result"]["generation_run"]
            ):
                raise ValueError("monomer job belongs to a different generation")
            read_monomer_batch(job)
        else:
            _within(directory, result["screening_session"])
        completed.append(receipt)
    if len(anchors) != len(completed):
        raise ValueError("unexpected pipeline progress anchor")
    return completed


def _stage(directory: Path, name: str, operation) -> dict:
    path = directory / "stages" / f"{name}.json"
    if path.exists():
        raise ValueError("pipeline stage already exists; refusing a duplicate execution")
    write_json(path, {"stage": name, "status": "running"})
    write_json(directory / "status.json", {"status": "running", "stage": name})
    started = time.monotonic()
    try:
        result, artifacts = operation()
        receipt = {
            "stage": name,
            "status": "completed",
            "elapsed_seconds": time.monotonic() - started,
            "result": result,
            "artifacts": [file_record(p, directory) for p in artifacts],
        }
        write_json(path, receipt)
        progress = read_json(directory / "progress.json")
        progress["receipts"].append(file_record(path, directory))
        write_json(directory / "progress.json", progress)
    except BaseException as error:
        write_json(
            path,
            {
                "stage": name,
                "status": "failed",
                "elapsed_seconds": time.monotonic() - started,
                "error_type": type(error).__name__,
                "error": str(error),
                "artifacts_preserved": True,
            },
        )
        write_json(directory / "status.json", {"status": "failed", "stage": name})
        raise
    write_json(directory / "status.json", {"status": "ready", "finished_stage": name})
    return receipt


def _generate(directory: Path, task: InteractionDesignSpec, runtime: dict):
    options = dict(runtime["generation"])
    for name in ("odesign_repo", "data_root", "checkpoint_root"):
        options[name] = Path(options[name])
    options["expected_revision"] = load_odesign_revision(default_asset_lock())
    result = DesignWorkflow(
        ODesignGenerator(LocalODesignExecutor(**options), directory / "generation")
    ).run(task, evaluate=False)
    _validate_generation(directory, result.run_dir, task)
    return (
        {"generation_run": str(result.run_dir.relative_to(directory))},
        [result.manifest],
    )


def _monomer(directory: Path, generation: Path, runtime: dict):
    job = prepare_monomer_batch(generation)
    options = runtime["monomer"]
    run_monomer_batch(
        job,
        python=Path(options["python"]),
        model_dir=Path(options["model_dir"]),
        gpu=options.get("gpu", 0),
        timeout=options.get("timeout_seconds", 900),
    )
    read_monomer_batch(job)
    return (
        {"monomer_job": str(job.relative_to(directory))},
        [job / "manifest.json", job / "completed.json"],
    )


def _screen(directory: Path, monomer: Path, runtime: dict, selection: SelectionConfig):
    session = prepare_screening(
        monomer, selection, runtime=runtime["complex"], artifacts=directory / "screening"
    )
    return (
        {"screening_session": str(session.relative_to(directory))},
        [session / "manifest.json"],
    )


def _view(directory: Path, history: list[dict], *, run: bool = False) -> dict:
    base = {
        "pipeline_dir": str(directory),
        "completed_stages": [r["stage"] for r in history],
        "stage_wall_seconds": {r["stage"]: r["elapsed_seconds"] for r in history},
    }
    if len(history) < len(STAGES):
        return {**base, "status": "prepared" if not history else "ready", "next": "pipeline run"}
    session = _within(directory, history[-1]["result"]["screening_session"])
    observed = run_screening(session) if run else observe_screening(session)
    result = {**base, "screening_session": str(session)}
    if observed.get("status") == "completed":
        result.update(status="completed", report=observed, next="pipeline export")
    else:
        result.update(status="awaiting_selection", request=observed, next="pipeline apply")
    status = {"status": result["status"], "screening_session": str(session.relative_to(directory))}
    if read_json(directory / "status.json") != status:
        write_json(directory / "status.json", status)
    return result


def run_pipeline(directory: Path) -> dict:
    directory = directory.resolve()
    with job_lock(directory):
        task, runtime, selection = _load(directory)
        history = _history(directory, task)
        if len(history) < len(STAGES):
            validate_pipeline_task(task)
            validate_pipeline_runtime(task, runtime)
        if not history:
            history.append(
                _stage(directory, "generation", lambda: _generate(directory, task, runtime))
            )
        if len(history) == 1:
            generation = _within(directory, history[0]["result"]["generation_run"])
            history.append(
                _stage(directory, "monomer", lambda: _monomer(directory, generation, runtime))
            )
        if len(history) == 2:
            monomer = _within(directory, history[1]["result"]["monomer_job"])
            history.append(
                _stage(
                    directory, "screening", lambda: _screen(directory, monomer, runtime, selection)
                )
            )
        return _view(directory, history, run=True)


def observe_pipeline(directory: Path) -> dict:
    directory = directory.resolve()
    with job_lock(directory):
        task, _, _ = _load(directory)
        return _view(directory, _history(directory, task))


def apply_pipeline(directory: Path, submission: dict) -> dict:
    directory = directory.resolve()
    with job_lock(directory):
        task, _, _ = _load(directory)
        history = _history(directory, task)
        if len(history) != len(STAGES):
            raise ValueError("run the pipeline before submitting candidate decisions")
        session = _within(directory, history[-1]["result"]["screening_session"])
        applied = apply_screening(session, submission)
        return {**_view(directory, history), "application": applied}


def export_pipeline(directory: Path, destination: Path) -> Path:
    from interaction_design.exporting import export_screening

    directory = directory.resolve()
    with job_lock(directory):
        task, _, _ = _load(directory)
        history = _history(directory, task)
        if len(history) != len(STAGES):
            raise ValueError("pipeline must finish before exporting")
        session = _within(directory, history[-1]["result"]["screening_session"])
        return export_screening(session, destination)

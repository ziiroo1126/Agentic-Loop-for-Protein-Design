"""Serial AF3/PyRosetta execution, preflight checks, and bounded local retries."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from interaction_design.assets import sha256_file
from interaction_design.evaluation.jobs import (
    accept_af3,
    accept_rosetta,
    checked_path,
    job_lock,
    load_job,
    read_json,
    receipt,
    timestamp_id,
)
from interaction_design.manifest import git_state
from interaction_design.persistence import write_json
from interaction_design.runtime.process import run_logged


class RuntimeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class AF3Runtime(RuntimeModel):
    python: Path
    repo: Path
    model_dir: Path
    expected_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    db_dir: Path | None = None


class RosettaRuntime(RuntimeModel):
    python: Path


class EvaluationRuntime(RuntimeModel):
    af3: AF3Runtime | None = None
    rosetta: RosettaRuntime | None = None
    cuda_visible_devices: str = Field(default="0", pattern=r"^\d+$")
    cpu_threads: int = Field(default=4, ge=1, le=32)
    stage_timeout_seconds: float = Field(default=900, gt=0)


def load_runtime(path: Path) -> EvaluationRuntime:
    payload = read_json(path)
    for stage, fields in {
        "af3": ("python", "repo", "model_dir", "db_dir"),
        "rosetta": ("python",),
    }.items():
        if payload.get(stage):
            for field in fields:
                value = payload[stage].get(field)
                if value:
                    location = Path(value).expanduser()
                    payload[stage][field] = str((path.parent / location).resolve())
    return EvaluationRuntime.model_validate(payload)


def _env(config: EvaluationRuntime) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        PYTHONUNBUFFERED="1",
        CUDA_VISIBLE_DEVICES=config.cuda_visible_devices,
        OMP_NUM_THREADS=str(config.cpu_threads),
    )
    if config.af3:
        # Keep an unrelated ODesign checkout out of AF3's source namespace.
        env["PYTHONPATH"] = str(config.af3.repo / "src")
    return env


def inspect_stage(stage: str, config: EvaluationRuntime, msa_mode: str) -> dict:
    runtime = config.af3 if stage == "af3" else config.rosetta
    issues = []
    details = {"stage": stage, "issues": issues, "runtime": None}
    if runtime is None:
        issues.append(f"{stage} runtime is not configured")
        return details
    details["runtime"] = runtime.model_dump(mode="json")
    if not runtime.python.is_file() or not os.access(runtime.python, os.X_OK):
        issues.append(f"{stage} Python executable is unavailable: {runtime.python}")
    else:
        modules = ("alphafold3", "jax") if stage == "af3" else ("pyrosetta", "biotite")
        code = (
            "import sys,json,importlib.util as u,importlib.metadata as m; "
            f"modules={modules!r}; "
            "print(json.dumps({'python':sys.version,'modules':{x:u.find_spec(x) is not None "
            "for x in modules},'packages':"
            "{d.metadata['Name']:d.version for d in m.distributions()}}))"
        )
        try:
            result = subprocess.run(
                [str(runtime.python), "-c", code],
                env=_env(config),
                capture_output=True,
                text=True,
                timeout=30,
                check=True,
            )
            environment = json.loads(result.stdout)
            details["environment"] = environment
            issues.extend(
                f"{stage} missing module: {name}"
                for name, available in environment["modules"].items()
                if not available
            )
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            issues.append(f"{stage} Python probe failed: {error}")
    if stage == "af3":
        if not (runtime.repo / "run_alphafold.py").is_file():
            issues.append(f"AF3 entrypoint is missing: {runtime.repo / 'run_alphafold.py'}")
        source = git_state(runtime.repo)
        details["source"] = source
        if source is None or source["revision"] != runtime.expected_revision or source["dirty"]:
            issues.append("AF3 source must be clean and match its configured full Git revision")
        weights = sorted(p for p in runtime.model_dir.glob("*.bin*") if p.is_file())
        if not weights or any(p.stat().st_size == 0 for p in weights):
            issues.append("AF3 model parameters are missing or empty (.bin/.bin.zst)")
        if msa_mode == "search" and (
            runtime.db_dir is None
            or not runtime.db_dir.is_dir()
            or not any(runtime.db_dir.iterdir())
        ):
            issues.append("target MSA search requires a populated AF3 database directory")
    return details


def remaining_budget(job: Path, payload: dict) -> float:
    elapsed = 0.0
    for path in (job / "executions").glob("*/*/*/execution.json"):
        record = read_json(path)
        if record["status"] == "running":
            raise ValueError(
                f"unfinished execution record; inspect the process before retrying: {path}"
            )
        elapsed += float(record["elapsed_seconds"])
    return max(0.0, payload["protocol"]["budget_seconds"] - elapsed)


def check_assessment(job_dir: str | Path, config: EvaluationRuntime) -> dict:
    job = Path(job_dir).resolve()
    with job_lock(job):
        payload = load_job(job)
        needed = [
            stage
            for stage in ("af3", "rosetta")
            if any(receipt(job, c, stage) is None for c in payload["candidates"])
        ]
        checks = [inspect_stage(stage, config, payload["protocol"]["msa_mode"]) for stage in needed]
        remaining = remaining_budget(job, payload)
        result = {
            "ready": all(not c["issues"] for c in checks) and (not needed or remaining > 0),
            "checks": checks,
            "remaining_local_seconds": remaining,
        }
        write_json(job / "preflight.json", result)
        return result


def stage_command(
    stage: str, job: Path, candidate: dict, attempt: Path, payload: dict, config: EvaluationRuntime
) -> list[str]:
    if stage == "af3":
        af3 = config.af3
        assert af3 is not None
        command = [
            str(af3.python),
            str(af3.repo / "run_alphafold.py"),
            f"--json_path={checked_path(job, candidate['input'])}",
            f"--model_dir={af3.model_dir}",
            f"--output_dir={attempt / 'output'}",
            f"--run_data_pipeline={str(payload['protocol']['msa_mode'] == 'search').lower()}",
            "--run_inference=true",
            "--num_diffusion_samples=1",
        ]
        if af3.db_dir is not None:
            command.append(f"--db_dir={af3.db_dir}")
        if payload["protocol"]["msa_mode"] == "search":
            command.extend(
                [
                    f"--jackhmmer_n_cpu={config.cpu_threads}",
                    f"--nhmmer_n_cpu={config.cpu_threads}",
                ]
            )
        return command
    rosetta = config.rosetta
    assert rosetta is not None
    af3_result = receipt(job, candidate, "af3")
    if af3_result is None:
        raise ValueError("AF3 must complete before Rosetta scoring")
    model = checked_path(job, af3_result["artifacts"]["af3_structure"])
    return [
        str(rosetta.python),
        str(Path(__file__).with_name("rosetta_worker.py")),
        "--input-cif",
        str(model),
        "--protocol-xml",
        str(job / "ppi.xml"),
        "--output",
        str(attempt / "metrics.json"),
        "--seed",
        str(payload["protocol"]["seed"]),
    ]


def run_assessment(job_dir: str | Path, config: EvaluationRuntime) -> dict:
    job = Path(job_dir).resolve()
    with job_lock(job):
        payload = load_job(job)
        for candidate in payload["candidates"]:
            for stage in ("af3", "rosetta"):
                if receipt(job, candidate, stage) is not None:
                    continue
                remaining = remaining_budget(job, payload)
                if remaining <= 0:
                    result = {"status": "budget_exhausted", "remaining_local_seconds": 0}
                    write_json(job / "status.json", result)
                    return result
                check = inspect_stage(stage, config, payload["protocol"]["msa_mode"])
                if check["issues"]:
                    result = {"status": "blocked", "candidate_id": candidate["id"], **check}
                    write_json(job / "status.json", result)
                    return result
                attempt = job / "executions" / candidate["key"] / stage / timestamp_id()
                attempt.mkdir(parents=True)
                write_json(attempt / "environment.json", check)
                if stage == "af3":
                    # Record actual parameter bytes for the process that will use them.
                    weights = [
                        {"path": str(p), "sha256": sha256_file(p), "size": p.stat().st_size}
                        for p in sorted(config.af3.model_dir.glob("*.bin*"))
                        if p.is_file()
                    ]
                    write_json(attempt / "parameters.json", {"files": weights})
                command = stage_command(stage, job, candidate, attempt, payload, config)
                write_json(
                    job / "status.json",
                    {
                        "status": f"{stage}_running",
                        "candidate_id": candidate["id"],
                        "attempt": str(attempt.relative_to(job)),
                    },
                )
                try:
                    run_logged(
                        command,
                        attempt,
                        metadata={
                            "stage": stage,
                            "candidate_id": candidate["id"],
                            "job_manifest_sha256": sha256_file(job / "manifest.json"),
                            "environment_sha256": sha256_file(attempt / "environment.json"),
                            "parameters_sha256": sha256_file(attempt / "parameters.json")
                            if stage == "af3"
                            else None,
                            "config": config.model_dump(mode="json"),
                        },
                        timeout_seconds=min(config.stage_timeout_seconds, remaining),
                        cwd=config.af3.repo if stage == "af3" else attempt,
                        env=_env(config),
                        label=stage,
                        log_prefix=stage,
                    )
                    if stage == "af3":
                        accept_af3(
                            job,
                            candidate,
                            attempt / "output",
                            mode="local-execution",
                            execution=attempt / "execution.json",
                        )
                    else:
                        accept_rosetta(
                            job,
                            candidate,
                            attempt / "metrics.json",
                            mode="local-execution",
                            execution=attempt / "execution.json",
                        )
                except BaseException as error:
                    failure = {
                        "status": "cancelled" if isinstance(error, KeyboardInterrupt) else "failed",
                        "stage": stage,
                        "candidate_id": candidate["id"],
                        "error_type": type(error).__name__,
                        "message": str(error),
                        "attempt": str(attempt.relative_to(job)),
                    }
                    write_json(attempt / "failure.json", failure)
                    write_json(job / "status.json", failure)
                    if not isinstance(error, Exception):
                        raise
                    raise ValueError(
                        f"assessment failed; retained at {attempt}: {error}"
                    ) from error
        result = {
            "status": "ready_to_report",
            "remaining_local_seconds": remaining_budget(job, payload),
        }
        write_json(job / "status.json", result)
        return result

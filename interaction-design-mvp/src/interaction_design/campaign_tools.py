"""Local model tools and explicitly labelled saved-observation replay."""

from __future__ import annotations

import math
import os
import time
from pathlib import Path

from interaction_design.assets import default_asset_lock, load_odesign_revision, sha256_file
from interaction_design.decisions import objective_vector
from interaction_design.evaluation.complex import (
    default_policy,
    prepare_complex_batch,
    run_complex_batch,
)
from interaction_design.evaluation.feedback import write_interface_feedback
from interaction_design.evaluation.jobs import checked_path, read_json
from interaction_design.generator import ODesignGenerator
from interaction_design.persistence import write_json
from interaction_design.runtime import LocalODesignExecutor
from interaction_design.specs import InteractionDesignSpec
from interaction_design.workflow import DesignWorkflow


def load_feedback(path: Path) -> dict:
    root = path.resolve()
    manifest = read_json(root / "manifest.json")
    records = manifest["artifacts"]
    names = [r["path"] for r in records]
    if len(names) != len(set(names)) or not {"observations.json", "status.json"} <= set(names):
        raise ValueError("feedback manifest is incomplete or duplicated")
    for record in records:
        checked_path(root, record)
    if read_json(root / "status.json")["status"] != "completed":
        raise ValueError("feedback must be completed")
    payload = read_json(root / "observations.json")
    rows = payload["observations"]
    if (
        not rows
        or payload["candidate_count"] != len(rows)
        or len({x["candidate_id"] for x in rows}) != len(rows)
        or len({x["generation_seed"] for x in rows}) != len(rows)
    ):
        raise ValueError("feedback requires distinct candidates and one candidate per seed")
    for row in rows:
        objective_vector(row)
        if row["acceptance"] is not None or row["binding_validated"] is not False:
            raise ValueError("this controller requires diagnostic observations without acceptance")
        if row["model_policy"] != payload["model_policy"]:
            raise ValueError("mixed model protocols in feedback")
    return payload


class ReplayTools:
    mode = "saved_observation_replay"

    def __init__(self, payload: dict):
        self.payload = payload
        self.by_seed = {x["generation_seed"]: x for x in payload["observations"]}

    def sample(self, seeds: list[int], directory: Path, remaining: float | None) -> dict:
        if remaining is not None:
            raise ValueError("replay does not simulate live tool wall-time budgets")
        if set(seeds) - self.by_seed.keys():
            raise ValueError("requested seeds are absent from the saved replay")
        return {
            "observations": [self.by_seed[seed] for seed in seeds],
            "model_policy": self.payload["model_policy"],
            "geometry_policy": self.payload["policy"],
            "mode": self.mode,
            "new_model_inference": False,
        }


class LocalCampaignTools:
    mode = "local_models"

    def __init__(self, task: dict, runtime: dict):
        self.task = task
        self.runtime = runtime
        if set(runtime) != {"generation", "complex"}:
            raise ValueError("campaign runtime requires generation and complex objects")
        required = {"odesign_repo", "data_root", "checkpoint_root", "python_executable"}
        allowed = required | {"cuda_visible_devices", "timeout_seconds"}
        if not required <= runtime["generation"].keys() or (runtime["generation"].keys() - allowed):
            raise ValueError("invalid campaign generation runtime")
        complex_runtime = runtime["complex"]
        if not {"python", "model_dir", "esmc_dir", "ccd"} <= complex_runtime.keys():
            raise ValueError("incomplete ESMFold2 runtime; generation has not started")
        for options, executable in [
            (runtime["generation"], "python_executable"),
            (complex_runtime, "python"),
        ]:
            timeout = options.get(
                "timeout_seconds", 900 if executable == "python_executable" else 1800
            )
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout)
                or timeout <= 0
            ):
                raise ValueError("runtime timeouts must be finite and positive")
            python = Path(options[executable])
            if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
                raise ValueError("runtime requires an available absolute Python executable")
        for name in ["odesign_repo", "data_root", "checkpoint_root"]:
            path = Path(runtime["generation"][name])
            if not path.is_absolute() or not path.is_dir():
                raise ValueError(f"generation {name} must be an available absolute directory")
        pinned = default_policy()
        for name, revision in [("model_dir", "model_revision"), ("esmc_dir", "esmc_revision")]:
            path = Path(complex_runtime[name])
            if (
                not path.is_absolute()
                or path.name != pinned[revision]
                or not (path / "config.json").is_file()
            ):
                raise ValueError("ESMFold2 snapshots differ from the pinned protocol")
        if (
            not Path(complex_runtime["ccd"]).is_absolute()
            or not Path(complex_runtime["ccd"]).is_file()
        ):
            raise ValueError("ESMFold2 CCD is unavailable")

    def sample(self, seeds: list[int], directory: Path, remaining: float | None) -> dict:
        started = time.monotonic()
        task = InteractionDesignSpec.model_validate(self.task)
        task.generation.seeds = seeds
        options = dict(self.runtime["generation"])
        for name in ["odesign_repo", "data_root", "checkpoint_root"]:
            options[name] = Path(options[name])
        timeout = options.get("timeout_seconds", 900)
        options["timeout_seconds"] = min(timeout, remaining) if remaining is not None else timeout
        options["expected_revision"] = load_odesign_revision(default_asset_lock())
        generation = DesignWorkflow(
            ODesignGenerator(LocalODesignExecutor(**options), directory / "generation")
        ).run(task, evaluate=False)
        left = None if remaining is None else remaining - (time.monotonic() - started)
        if left is not None and left <= 0:
            raise TimeoutError("tool wall budget exhausted after generation; generation preserved")
        runtime = dict(self.runtime["complex"])
        timeout = runtime.get("timeout_seconds", 1800)
        runtime["timeout_seconds"] = min(timeout, left) if left is not None else timeout
        config = write_json(directory / "complex-runtime.json", runtime)
        job = prepare_complex_batch(
            generation.run_dir,
            artifacts=directory / "complex",
            budget_seconds=runtime["timeout_seconds"],
        )
        run_complex_batch(job, config=config)
        feedback = write_interface_feedback([job], artifacts=directory / "feedback")
        payload = load_feedback(feedback)
        return {
            "observations": payload["observations"],
            "model_policy": payload["model_policy"],
            "geometry_policy": payload["policy"],
            "mode": self.mode,
            "new_model_inference": True,
            "feedback": {
                "path": str(feedback),
                "observations_sha256": sha256_file(feedback / "observations.json"),
            },
        }

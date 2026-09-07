"""Local-process and OCI-container ODesign executors."""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from interaction_design.assets import sha256_file, verify_task_checkpoints
from interaction_design.persistence import write_json
from interaction_design.runtime.base import (
    ExecutionResult,
    ODesignExecutionError,
    ODesignRunRequest,
)
from interaction_design.runtime.process import run_logged


def _data_records(data_root: Path) -> list[dict[str, object]]:
    return [
        {"path": str(path.resolve()), "sha256": sha256_file(path), "size": path.stat().st_size}
        for path in (
            data_root / "components.v20240608.cif",
            data_root / "components.v20240608.cif.rdkit_mol.pkl",
        )
    ]


def _verify_git_revision(repo: Path, expected: str | None) -> str | None:
    if expected is None:
        return None
    try:
        actual = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ODesignExecutionError(f"cannot inspect ODesign Git revision: {error}") from error
    if actual != expected:
        raise ODesignExecutionError(
            f"ODesign revision mismatch: expected {expected}, found {actual}"
        )
    return actual


def _hydra_overrides(
    request: ODesignRunRequest,
    *,
    input_json: str,
    output_dir: str,
    data_root: str,
    checkpoint_root: str,
) -> list[str]:
    spec = request.spec
    generation = spec.generation
    return [
        f"exp=train_{spec.model}",
        f"data_root_dir={data_root}",
        f"ckpt_root_dir={checkpoint_root}",
        f"exp.infer_model_name={spec.model}",
        f"exp.design_modality={spec.design_modality}",
        f"exp.input_json_path={input_json}",
        f"exp.exp_name={spec.name}",
        f"exp.seeds={generation.seeds}",
        f"exp.model.sample_diffusion.N_sample={request.samples_per_seed}",
        f"exp.invfold_topk={generation.inverse_fold_topk}",
        f"exp.invfold_temp={request.temperature}",
        f"exp.invfold_use_beam={str(generation.inverse_fold_beam).lower()}",
        f"exp.use_msa={str(generation.use_msa).lower()}",
        f"exp.num_workers={generation.num_workers}",
        "exp.model.inference_noise_schedulers.coordinate.partial_diffusion.enable="
        f"{str(bool(spec.partial_diffusion)).lower()}",
        "exp.model.inference_noise_schedulers.coordinate.partial_diffusion.snr="
        f"{generation.partial_diffusion_snr}",
        f"hydra.run.dir={output_dir}",
        "hydra.job.chdir=false",
    ]


@dataclass
class LocalODesignExecutor:
    odesign_repo: Path
    data_root: Path
    checkpoint_root: Path
    python_executable: str = "python"
    cuda_visible_devices: str = "0"
    timeout_seconds: float | None = None
    expected_revision: str | None = None
    name: str = "local"

    def execute(self, request: ODesignRunRequest) -> ExecutionResult:
        repo = self.odesign_repo.resolve()
        entrypoint = repo / "scripts" / "inference.py"
        if not entrypoint.is_file():
            raise ODesignExecutionError(f"ODesign entrypoint not found: {entrypoint}")
        revision = _verify_git_revision(repo, self.expected_revision)
        if (
            request.spec.reference_structure
            and not Path(request.spec.reference_structure).is_file()
        ):
            raise ODesignExecutionError(
                f"reference structure not found: {request.spec.reference_structure}"
            )
        for label, path in (
            ("data root", self.data_root),
            ("checkpoint root", self.checkpoint_root),
        ):
            if not path.is_dir():
                raise ODesignExecutionError(f"ODesign {label} not found: {path}")
        required_files = [
            self.data_root / "components.v20240608.cif",
            self.data_root / "components.v20240608.cif.rdkit_mol.pkl",
            self.checkpoint_root / f"{request.spec.model}.pt",
            self.checkpoint_root / f"oinvfold_{request.spec.design_modality}.ckpt",
        ]
        missing = [str(path) for path in required_files if not path.is_file()]
        if missing:
            raise ODesignExecutionError(
                "required ODesign assets are missing:\n" + "\n".join(missing)
            )

        command = [
            self.python_executable,
            str(entrypoint),
            *_hydra_overrides(
                request,
                input_json=str(request.input_json.resolve()),
                output_dir=str(request.output_dir.resolve()),
                data_root=str(self.data_root.resolve()),
                checkpoint_root=str(self.checkpoint_root.resolve()),
            ),
        ]
        env = os.environ.copy()
        env["CUDA_VISIBLE_DEVICES"] = self.cuda_visible_devices
        prior_pythonpath = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{repo}{os.pathsep}{prior_pythonpath}" if prior_pythonpath else str(repo)
        )

        env["PYTHONUNBUFFERED"] = "1"
        environment = subprocess.run(
            [
                self.python_executable,
                "-c",
                "import sys,json,importlib.metadata as m; "
                "print(json.dumps({'executable':sys.executable,'python':sys.version,"
                "'packages':{d.metadata['Name']:d.version for d in m.distributions()}}))",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        write_json(request.run_dir / "runtime-environment.json", json.loads(environment.stdout))
        metadata = {
            "odesign_repo": str(repo),
            "odesign_revision": revision,
            "cuda_visible_devices": self.cuda_visible_devices,
            "chemical_components": _data_records(self.data_root),
            "checkpoints": verify_task_checkpoints(
                self.checkpoint_root, request.spec.model, request.spec.design_modality
            ),
        }
        stdout_path, stderr_path = run_logged(
            command,
            request.run_dir,
            cwd=repo,
            env=env,
            metadata=metadata,
            timeout_seconds=self.timeout_seconds,
        )
        return ExecutionResult(
            output_dir=request.output_dir,
            command=tuple(command),
            executor=self.name,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            metadata=metadata,
        )


@dataclass
class ContainerODesignExecutor:
    """Run ODesign in an already-built CUDA-enabled OCI image."""

    image: str
    odesign_repo: Path
    data_root: Path
    checkpoint_root: Path
    runtime: str = "docker"
    gpu_devices: str = "all"
    timeout_seconds: float | None = None
    expected_revision: str | None = None
    name: str = "container"

    def execute(self, request: ODesignRunRequest) -> ExecutionResult:
        repo = self.odesign_repo.resolve()
        data_root = self.data_root.resolve()
        checkpoint_root = self.checkpoint_root.resolve()
        for label, path in (
            ("ODesign repository", repo),
            ("data root", data_root),
            ("checkpoint root", checkpoint_root),
        ):
            if not path.is_dir():
                raise ODesignExecutionError(f"{label} not found: {path}")
        revision = _verify_git_revision(repo, self.expected_revision)
        required_files = [
            data_root / "components.v20240608.cif",
            data_root / "components.v20240608.cif.rdkit_mol.pkl",
            checkpoint_root / f"{request.spec.model}.pt",
            checkpoint_root / f"oinvfold_{request.spec.design_modality}.ckpt",
        ]
        missing = [str(path) for path in required_files if not path.is_file()]
        if missing:
            raise ODesignExecutionError(
                "required ODesign assets are missing:\n" + "\n".join(missing)
            )
        reference_mount: list[str] = []
        if request.spec.reference_structure:
            reference = Path(request.spec.reference_structure).resolve()
            if not reference.is_file():
                raise ODesignExecutionError(f"reference structure not found: {reference}")
            reference_mount = ["-v", f"{reference}:{reference}:ro"]

        container_input = "/work/odesign_input.json"
        container_output = "/work/output"
        inner = [
            "python",
            "/opt/odesign/scripts/inference.py",
            *_hydra_overrides(
                request,
                input_json=container_input,
                output_dir=container_output,
                data_root="/opt/assets/data",
                checkpoint_root="/opt/assets/checkpoints",
            ),
        ]
        container_name = f"molclaw-{uuid.uuid4().hex}"
        command = [
            self.runtime,
            "run",
            "--rm",
            "--name",
            container_name,
            "-e",
            "PYTHONUNBUFFERED=1",
            "--gpus",
            self.gpu_devices,
            "-v",
            f"{repo}:/opt/odesign:ro",
            "-v",
            f"{request.run_dir.resolve()}:/work",
            "-v",
            f"{data_root}:/opt/assets/data:ro",
            "-v",
            f"{checkpoint_root}:/opt/assets/checkpoints:ro",
            *reference_mount,
            "-w",
            "/opt/odesign",
            self.image,
            *inner,
        ]
        metadata = {
            "image": self.image,
            "runtime": self.runtime,
            "container_name": container_name,
            "odesign_repo": str(repo),
            "odesign_revision": revision,
            "chemical_components": _data_records(data_root),
            "checkpoints": verify_task_checkpoints(
                checkpoint_root, request.spec.model, request.spec.design_modality
            ),
        }
        try:
            stdout_path, stderr_path = run_logged(
                command,
                request.run_dir,
                metadata=metadata,
                timeout_seconds=self.timeout_seconds,
            )
        except BaseException:
            # Killing the Docker client does not stop the container on its own.
            try:
                subprocess.run(
                    [self.runtime, "rm", "--force", container_name],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            raise
        return ExecutionResult(
            output_dir=request.output_dir,
            command=tuple(command),
            executor=self.name,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            metadata=metadata,
        )

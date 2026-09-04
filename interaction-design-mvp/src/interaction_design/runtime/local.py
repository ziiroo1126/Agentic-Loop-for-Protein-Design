"""Local-process and OCI-container ODesign executors."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from interaction_design.runtime.base import (
    ExecutionResult,
    ODesignExecutionError,
    ODesignRunRequest,
)


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
    timeout_seconds: int | None = None
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

        stdout_path = request.run_dir / "odesign.stdout.log"
        stderr_path = request.run_dir / "odesign.stderr.log"
        try:
            completed = subprocess.run(
                command,
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ODesignExecutionError(f"failed to launch ODesign: {error}") from error

        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            tail = completed.stderr[-4000:]
            raise ODesignExecutionError(
                f"ODesign exited with code {completed.returncode}; stderr tail:\n{tail}"
            )
        return ExecutionResult(
            output_dir=request.output_dir,
            command=tuple(command),
            executor=self.name,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            metadata={"odesign_repo": str(repo), "odesign_revision": revision},
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
    timeout_seconds: int | None = None
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
        command = [
            self.runtime,
            "run",
            "--rm",
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
        stdout_path = request.run_dir / "odesign.stdout.log"
        stderr_path = request.run_dir / "odesign.stderr.log"
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ODesignExecutionError(f"failed to launch container: {error}") from error

        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")
        if completed.returncode != 0:
            raise ODesignExecutionError(
                f"ODesign container exited with code {completed.returncode}; "
                f"stderr tail:\n{completed.stderr[-4000:]}"
            )
        return ExecutionResult(
            output_dir=request.output_dir,
            command=tuple(command),
            executor=self.name,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            metadata={
                "image": self.image,
                "runtime": self.runtime,
                "odesign_repo": str(repo),
                "odesign_revision": revision,
            },
        )

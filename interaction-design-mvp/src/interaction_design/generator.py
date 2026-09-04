"""evedesign Generator adapter for ODesign."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from evedesign.model import BaseModel, Generator
from evedesign.system import System, SystemInstance
from evedesign.types import EntityPosList, StatusCallback

from interaction_design.conversion import system_to_odesign_input
from interaction_design.outputs import parse_odesign_outputs
from interaction_design.runtime.base import (
    ExecutionResult,
    ODesignExecutionError,
    ODesignExecutor,
    ODesignRunRequest,
)
from interaction_design.specs import InteractionDesignSpec


class ODesignGenerator(BaseModel, Generator):
    """Run ODesign behind evedesign's generation contract.

    The executor is injected, so scientific translation, process isolation,
    containers, and deterministic mock execution remain independently testable.
    """

    available = True
    name = "ODesign"
    citations = ["arXiv:2510.22304"]

    requires_target = False
    requires_fixed_length = False
    handles_deletions = False
    handles_insertions = True
    requires_gpu = True
    supports_gpu = True
    supports_gpu_parallel = True
    supports_cpu_parallel = False

    required_entity_attributes = None
    optional_entity_attributes = [
        "structures",
        "interactions",
        "cyclic",
        "min_length",
        "max_length",
        "ligand_rep_type",
    ]

    def __init__(self, executor: ODesignExecutor, artifact_root: str | Path):
        self.executor = executor
        self.artifact_root = Path(artifact_root)
        self._system: System | None = None
        self._spec: InteractionDesignSpec | None = None
        self.last_run_dir: Path | None = None
        self.last_execution: ExecutionResult | None = None

    @property
    def ready(self) -> bool:
        return self._system is not None and self._spec is not None

    @property
    def system(self) -> System | None:
        return self._system

    @classmethod
    def can_model(
        cls,
        system: System,
        data: Any = None,
    ) -> tuple[bool, str]:
        try:
            spec = (
                data
                if isinstance(data, InteractionDesignSpec)
                else InteractionDesignSpec.model_validate(data)
            )
        except Exception as error:
            return False, f"data must be a valid InteractionDesignSpec: {error}"

        if len(system) != len(spec.molecules):
            return False, "system and InteractionDesignSpec molecule counts differ"
        for index, (entity, molecule) in enumerate(zip(system, spec.molecules, strict=True)):
            if entity.id != molecule.id or entity.type != molecule.type:
                return False, (
                    f"entity {index} ({entity.id!r}, {entity.type!r}) does not match "
                    f"molecule ({molecule.id!r}, {molecule.type!r})"
                )
            if entity.deletions:
                return False, f"entity {index} enables deletions, unsupported by ODesign"
        return True, ""

    def build(
        self,
        system: System,
        data: Any,
        status_callback: StatusCallback | None = None,
    ) -> Self:
        self.can_model_or_raise(system, data)
        self._system = system
        self._spec = (
            data
            if isinstance(data, InteractionDesignSpec)
            else InteractionDesignSpec.model_validate(data)
        )
        return self

    def generate(
        self,
        num_designs: int,
        entities: Sequence[int] | None = None,
        fixed_pos: EntityPosList | None = None,
        temperature: float = 1.0,
        status_callback: StatusCallback | None = None,
    ) -> list[SystemInstance]:
        self.ready_or_raise()
        if num_designs < 1:
            raise ValueError("num_designs must be at least 1")
        if temperature <= 0:
            raise ValueError("temperature must be positive")
        if fixed_pos:
            raise ValueError(
                "ODesign fixed regions must be declared as fixed segments or "
                "partial_diffusion regions in InteractionDesignSpec"
            )
        assert self._system is not None and self._spec is not None

        if status_callback:
            status_callback("running", 0.0, "materializing ODesign input")
        native_input = system_to_odesign_input(self._system, self._spec, entities)
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        run_dir = self.artifact_root / f"{timestamp}-{uuid.uuid4().hex[:8]}"
        output_dir = run_dir / "output"
        output_dir.mkdir(parents=True, exist_ok=False)
        input_path = run_dir / "odesign_input.json"
        input_path.write_text(
            json.dumps(native_input, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        capacity_per_backbone = (
            len(self._spec.generation.seeds) * self._spec.generation.inverse_fold_topk
        )
        samples_per_seed = max(
            self._spec.generation.samples_per_seed,
            math.ceil(num_designs / capacity_per_backbone),
        )
        request = ODesignRunRequest(
            spec=self._spec,
            system=self._system,
            input_json=input_path,
            run_dir=run_dir,
            output_dir=output_dir,
            num_designs=num_designs,
            samples_per_seed=samples_per_seed,
            temperature=temperature,
        )
        try:
            if status_callback:
                status_callback("running", 0.1, f"running {self.executor.name} executor")
            execution = self.executor.execute(request)
            if status_callback:
                status_callback("running", 0.9, "parsing ODesign outputs")
            instances = parse_odesign_outputs(execution.output_dir, self._system, self._spec)
        except Exception as error:
            (run_dir / "failure.json").write_text(
                json.dumps(
                    {
                        "error_type": type(error).__name__,
                        "message": str(error),
                        "task": self._spec.model_dump(mode="json"),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if status_callback:
                status_callback("failed", None, str(error))
            if isinstance(error, ODesignExecutionError):
                raise
            raise ODesignExecutionError(f"ODesign generation failed: {error}") from error

        if len(instances) < num_designs:
            raise ODesignExecutionError(
                f"ODesign returned {len(instances)} parseable designs; "
                f"at least {num_designs} were requested"
            )
        self._validate_instances(instances, raise_invalid=True)
        self.last_run_dir = run_dir
        self.last_execution = execution
        if status_callback:
            status_callback("done", 1.0, f"parsed {len(instances)} designs")
        return instances

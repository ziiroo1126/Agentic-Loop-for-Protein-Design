"""Shared implementation for external-artifact evedesign scorers."""

from __future__ import annotations

import copy
import json
import math
from abc import abstractmethod
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Self

from evedesign.model import BaseModel, Scorer
from evedesign.system import System, SystemInstance
from evedesign.types import StatusCallback


class ArtifactScorer(BaseModel, Scorer):
    requires_target = False
    requires_fixed_length = False
    handles_deletions = False
    handles_insertions = True
    requires_gpu = False
    supports_gpu = False
    supports_gpu_parallel = False
    supports_cpu_parallel = True
    required_entity_attributes = None
    optional_entity_attributes = ["structures"]

    artifact_key: str
    evaluation_key: str

    def __init__(self) -> None:
        self._system: System | None = None

    @property
    def ready(self) -> bool:
        return self._system is not None

    @property
    def system(self) -> System | None:
        return self._system

    @classmethod
    def can_model(cls, system: System, data: Any = None) -> tuple[bool, str]:
        if data is not None:
            return False, "artifact scorers do not accept build data"
        if len(system) == 0:
            return False, "system has no entities"
        return True, ""

    def build(
        self,
        system: System,
        data: None = None,
        status_callback: StatusCallback | None = None,
    ) -> Self:
        self.can_model_or_raise(system, data)
        self._system = system
        return self

    @abstractmethod
    def parse_metrics(self, payload: dict[str, Any]) -> dict[str, float]:
        pass

    @abstractmethod
    def primary_score(self, metrics: dict[str, float]) -> float:
        pass

    def score(
        self,
        instances: Sequence[SystemInstance],
        status_callback: StatusCallback | None = None,
    ) -> list[SystemInstance]:
        self.ready_or_raise()
        self._validate_instances(instances, raise_invalid=True)
        scored: list[SystemInstance] = []
        total = len(instances)
        for index, instance in enumerate(instances):
            metadata = instance.metadata or {}
            artifact_path = metadata.get("artifacts", {}).get(self.artifact_key)
            if not artifact_path:
                raise ValueError(f"instance {instance.id!r} has no {self.artifact_key!r} artifact")
            try:
                payload = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ValueError(
                    f"cannot read {self.artifact_key} for instance {instance.id!r}: {error}"
                ) from error
            if not isinstance(payload, dict):
                raise ValueError(f"{artifact_path} must contain a JSON object")
            metrics = self.parse_metrics(payload)
            if any(not math.isfinite(value) for value in metrics.values()):
                raise ValueError(f"non-finite {self.evaluation_key} metric for {instance.id}")

            updated = instance.copy()
            updated.metadata = copy.deepcopy(metadata)
            evaluations = updated.metadata.setdefault("evaluations", {})
            evaluations[self.evaluation_key] = metrics
            updated.score = self.primary_score(metrics)
            scored.append(updated)
            if status_callback:
                progress = (index + 1) / total if total else 1.0
                status_callback("running", progress, f"scored {instance.id}")
        if status_callback:
            status_callback("done", 1.0, f"scored {total} instances")
        return scored

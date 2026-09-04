"""Runtime-neutral ODesign execution contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from evedesign.system import System

from interaction_design.specs import InteractionDesignSpec


class ODesignExecutionError(RuntimeError):
    """Raised when an ODesign backend fails or produces unusable output."""


@dataclass(frozen=True)
class ODesignRunRequest:
    spec: InteractionDesignSpec
    system: System
    input_json: Path
    run_dir: Path
    output_dir: Path
    num_designs: int
    samples_per_seed: int
    temperature: float


@dataclass(frozen=True)
class ExecutionResult:
    output_dir: Path
    command: tuple[str, ...]
    executor: str
    stdout_path: Path | None = None
    stderr_path: Path | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class ODesignExecutor(Protocol):
    name: str

    def execute(self, request: ODesignRunRequest) -> ExecutionResult:
        """Execute one fully materialized request."""

"""Collect incomplete design intent before compiling a checked pipeline task.

Names, sequences and prose are retained as context, not interpreted as coordinates
or executable constraints. This module never downloads assets or runs a model.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from interaction_design.pipeline_config import validate_pipeline_task
from interaction_design.specs import (
    FixedSegment,
    InteractionDesignSpec,
    ResidueRef,
    StrictModel,
)

PositiveLength = Annotated[int, Field(strict=True, ge=1)]
Seed = Annotated[int, Field(strict=True, ge=0, le=2**31 - 1)]


class LengthPreference(StrictModel):
    min: PositiveLength
    max: PositiveLength

    @model_validator(mode="after")
    def ordered(self) -> Self:
        if self.max < self.min:
            raise ValueError("length max must be greater than or equal to min")
        return self


class TargetBrief(StrictModel):
    identifier: str | None = None
    sequence_file: str | None = None
    reference_structure: str | None = None
    segments: list[FixedSegment] = Field(default_factory=list)
    hotspots: list[ResidueRef] | None = None


class BinderBrief(StrictModel):
    length: LengthPreference | None = None
    selected_length: PositiveLength | None = None
    cyclic: bool = False

    @model_validator(mode="after")
    def compatible_length(self) -> Self:
        if self.selected_length is not None and self.length is not None:
            if not self.length.min <= self.selected_length <= self.length.max:
                raise ValueError("selected_length must lie within the requested length range")
        return self


class DesignBrief(StrictModel):
    kind: Literal["protein_binder_brief"] = "protein_binder_brief"
    schema_version: Literal["0.1"] = "0.1"
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    goal: str | None = None
    target: TargetBrief = Field(default_factory=TargetBrief)
    binder: BinderBrief = Field(default_factory=BinderBrief)
    unresolved_requirements: list[str] = Field(default_factory=list)
    model: Literal["odesign_base_prot_flex", "odesign_base_prot_rigid"] = "odesign_base_prot_flex"
    seeds: list[Seed] = Field(default_factory=lambda: [42], min_length=1)

    @model_validator(mode="after")
    def unique_seeds(self) -> Self:
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("seeds must be distinct")
        return self


def load_brief(path: Path) -> DesignBrief:
    brief = DesignBrief.model_validate_json(path.read_text(encoding="utf-8"))
    for field in ("reference_structure", "sequence_file"):
        value = getattr(brief.target, field)
        if value and "://" not in value:
            local = Path(value).expanduser()
            if not local.is_absolute():
                local = path.resolve().parent / local
            setattr(brief.target, field, str(local.resolve()))
    return brief


def review_brief(brief: DesignBrief) -> dict:
    """Report missing choices; check actual residue mapping once choices are complete."""
    issues = []

    def issue(field: str, category: str, message: str) -> None:
        issues.append({"field": field, "category": category, "message": message})

    reference = brief.target.reference_structure
    if not reference:
        issue(
            "target.reference_structure",
            "missing_input",
            "Provide a local target PDB/mmCIF and establish its construct and numbering. "
            "An identifier or sequence alone does not select a unique structure.",
        )
    elif "://" in reference or not Path(reference).is_file():
        issue(
            "target.reference_structure",
            "invalid_input",
            "The reference must be an available local file; no structure is fetched automatically.",
        )
    elif Path(reference).suffix.lower() not in {".pdb", ".cif", ".mmcif"}:
        issue("target.reference_structure", "invalid_input", "Use a PDB or mmCIF file.")

    if not brief.target.segments:
        issue(
            "target.segments",
            "missing_input",
            "Specify target chain and residue intervals using the reference's author numbering.",
        )
    if not brief.target.hotspots:
        issue(
            "target.hotspots",
            "backend_requirement",
            "The current ODesign pipeline requires explicit target hotspots. "
            "Unspecified hotspots may remain in a brief, but automatic site selection "
            "is not implemented in this pipeline.",
        )

    length = brief.binder.selected_length
    preference = brief.binder.length
    if length is None and preference is not None and preference.min == preference.max:
        length = preference.min
    if length is None:
        if preference is None:
            issue("binder.length", "missing_input", "Provide a binder length or length preference.")
        else:
            issue(
                "binder.selected_length",
                "backend_requirement",
                "The length range is preserved. Select a fixed length within it before building "
                "one task; range sampling is not implemented in the complete pipeline.",
            )
    if brief.binder.cyclic:
        issue(
            "binder.cyclic",
            "unsupported_requirement",
            "The complete pipeline currently supports linear protein binders only.",
        )
    for index, requirement in enumerate(brief.unresolved_requirements):
        issue(
            f"unresolved_requirements.{index}",
            "unresolved_requirement",
            f"Resolve this requirement before compilation: {requirement}",
        )

    task = None
    mapping_checked = False
    if not issues:
        spec = InteractionDesignSpec.model_validate(
            {
                "name": brief.name,
                "description": brief.goal,
                "model": brief.model,
                "design_modality": "protein",
                "reference_structure": reference,
                "molecules": [
                    {
                        "id": "binder",
                        "type": "protein",
                        "role": "design",
                        "segments": [
                            {"kind": "generated", "min_length": length, "max_length": length}
                        ],
                    },
                    {
                        "id": "target",
                        "type": "protein",
                        "role": "context",
                        "segments": [segment.model_dump() for segment in brief.target.segments],
                    },
                ],
                "hotspots": [hotspot.model_dump() for hotspot in brief.target.hotspots],
                "center_method": "hotspot_center",
                "generation": {"seeds": brief.seeds},
                "evaluation": {
                    "binder_molecule": "binder",
                    "metric_rules": [],
                    "require_all_metrics": False,
                },
            }
        )
        try:
            validate_pipeline_task(spec)
        except ValueError as error:
            issue("target", "invalid_mapping", str(error))
        else:
            task = spec.model_dump(mode="json")
            mapping_checked = True

    return {
        "status": "needs_input" if issues else "ready_for_preflight",
        "issues": issues,
        "brief": brief.model_dump(mode="json"),
        "task": task,
        "execution_settings": {
            "model": brief.model,
            "seeds": brief.seeds,
            "candidate_count": len(brief.seeds),
            "selected_length": length,
        },
        "checks": {"target_mapping": mapping_checked, "runtime": False},
        "inference": "not_run",
    }


def write_new_json(path: Path, payload: dict) -> None:
    """Create a new user-authored artifact without replacing an existing file."""
    content = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)


def build_task(brief_path: Path, output: Path) -> dict:
    report = review_brief(load_brief(brief_path))
    if report["task"] is not None:
        write_new_json(output, report["task"])
        report["task_path"] = str(output.resolve())
    return report

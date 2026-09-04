"""Validated domain specification for an interaction-design experiment.

The public schema models scientific intent.  ODesign's compact chain-string
syntax is deliberately kept in the adapter layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

MoleculeType = Literal["protein", "ligand", "rna", "dna"]
MoleculeRole = Literal["context", "design"]
ODesignModel = Literal[
    "odesign_base_prot_flex",
    "odesign_base_prot_rigid",
    "odesign_base_ligand_rigid",
    "odesign_base_na_rigid",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneratedSegment(StrictModel):
    """A variable or fixed-length region to generate."""

    kind: Literal["generated"] = "generated"
    min_length: int = Field(ge=1)
    max_length: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if self.max_length < self.min_length:
            raise ValueError("max_length must be greater than or equal to min_length")
        return self


class FixedSegment(StrictModel):
    """A residue interval copied from the reference structure."""

    kind: Literal["fixed"] = "fixed"
    chain: str = Field(min_length=1, max_length=8)
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


ChainSegment = Annotated[
    GeneratedSegment | FixedSegment,
    Field(discriminator="kind"),
]


class MoleculeSpec(StrictModel):
    """One molecule in the designed complex, in output-chain order."""

    id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]*$")
    type: MoleculeType
    role: MoleculeRole
    segments: list[ChainSegment] = Field(default_factory=list)
    smiles: str | None = None
    total_length: int | None = Field(default=None, ge=1)
    cyclic: bool = False
    msa: str | None = None

    @model_validator(mode="after")
    def valid_representation(self) -> Self:
        representations = bool(self.segments) + bool(self.smiles)
        if representations != 1:
            raise ValueError("exactly one of segments or smiles must be specified")
        if self.smiles is not None and self.type != "ligand":
            raise ValueError("smiles is only valid for ligand molecules")
        if self.smiles is not None and self.role != "context":
            raise ValueError("SMILES ligands are context; designed ligands use generated segments")
        if self.cyclic and self.type != "protein":
            raise ValueError("ODesign cyclic generation currently supports proteins only")
        if self.role == "context" and any(
            isinstance(segment, GeneratedSegment) for segment in self.segments
        ):
            raise ValueError("context molecules cannot contain generated segments")
        if self.total_length is not None and not self.segments:
            raise ValueError("total_length is only used with segment-based molecules")
        if self.total_length is not None and not (
            self.min_length <= self.total_length <= self.max_length
        ):
            raise ValueError("total_length must be achievable from the segment length bounds")
        return self

    @property
    def min_length(self) -> int | None:
        if not self.segments:
            return None
        return sum(
            segment.min_length
            if isinstance(segment, GeneratedSegment)
            else segment.end - segment.start + 1
            for segment in self.segments
        )

    @property
    def max_length(self) -> int | None:
        if not self.segments:
            return None
        return sum(
            segment.max_length
            if isinstance(segment, GeneratedSegment)
            else segment.end - segment.start + 1
            for segment in self.segments
        )


class ResidueRef(StrictModel):
    chain: str = Field(min_length=1, max_length=8)
    residue: int = Field(ge=1)


class AtomConstraint(StrictModel):
    residue: ResidueRef
    atoms: list[str] = Field(min_length=1)


class PartialDiffusionRegion(StrictModel):
    chain: str = Field(min_length=1, max_length=8)
    start: int = Field(ge=1)
    end: int = Field(ge=1)

    @model_validator(mode="after")
    def ordered_bounds(self) -> Self:
        if self.end < self.start:
            raise ValueError("end must be greater than or equal to start")
        return self


class GenerationSpec(StrictModel):
    seeds: list[int] = Field(default_factory=lambda: [42], min_length=1)
    samples_per_seed: int = Field(default=1, ge=1)
    inverse_fold_topk: int = Field(default=1, ge=1)
    inverse_fold_temperature: float = Field(default=1.0, gt=0)
    inverse_fold_beam: bool = True
    use_msa: bool = False
    num_workers: int = Field(default=1, ge=0)
    partial_diffusion_snr: float = Field(default=0.1, gt=0)


class MetricRule(StrictModel):
    source: Literal["af3", "rosetta"]
    metric: str
    higher_is_better: bool
    weight: float = Field(gt=0)
    threshold: float | None = None


def _default_metric_rules() -> list[MetricRule]:
    return [
        MetricRule(
            source="af3",
            metric="iptm",
            higher_is_better=True,
            weight=0.35,
            threshold=0.60,
        ),
        MetricRule(
            source="af3",
            metric="binder_ptm",
            higher_is_better=True,
            weight=0.15,
            threshold=0.50,
        ),
        MetricRule(
            source="af3",
            metric="ipae_avg",
            higher_is_better=False,
            weight=0.15,
            threshold=12.0,
        ),
        MetricRule(
            source="rosetta",
            metric="ddg",
            higher_is_better=False,
            weight=0.20,
            threshold=-44.0,
        ),
        MetricRule(
            source="rosetta",
            metric="sap_score",
            higher_is_better=False,
            weight=0.05,
            threshold=40.0,
        ),
        MetricRule(
            source="rosetta",
            metric="contact_molecular_surface",
            higher_is_better=True,
            weight=0.10,
            threshold=400.0,
        ),
    ]


class EvaluationSpec(StrictModel):
    binder_molecule: str
    metric_rules: list[MetricRule] = Field(default_factory=_default_metric_rules)
    require_all_metrics: bool = True


class InteractionDesignSpec(StrictModel):
    """One declarative generation, evaluation, and ranking task."""

    schema_version: Literal["0.1"] = "0.1"
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    description: str | None = None
    model: ODesignModel
    design_modality: MoleculeType
    reference_structure: str | None = None
    molecules: list[MoleculeSpec] = Field(min_length=1)
    hotspots: list[ResidueRef] = Field(default_factory=list)
    atom_constraints: list[AtomConstraint] = Field(default_factory=list)
    partial_diffusion: list[PartialDiffusionRegion] = Field(default_factory=list)
    motif_scaffolding: bool = False
    center_method: Literal["none", "hotspot_center", "global_center", "usr_provide_center"] = "none"
    user_center: tuple[float, float, float] | None = None
    generation: GenerationSpec = Field(default_factory=GenerationSpec)
    evaluation: EvaluationSpec

    @model_validator(mode="after")
    def coherent_task(self) -> Self:
        ids = [molecule.id for molecule in self.molecules]
        if len(ids) != len(set(ids)):
            raise ValueError("molecule ids must be unique")
        if self.evaluation.binder_molecule not in ids:
            raise ValueError("evaluation.binder_molecule must name a molecule")

        designed = [molecule for molecule in self.molecules if molecule.role == "design"]
        if not designed:
            raise ValueError("at least one molecule must have role='design'")
        if any(molecule.type != self.design_modality for molecule in designed):
            raise ValueError("all design molecules must match design_modality")
        has_generated_region = any(
            isinstance(segment, GeneratedSegment)
            for molecule in designed
            for segment in molecule.segments
        )
        partial_design_chains = {
            segment.chain
            for molecule in designed
            for segment in molecule.segments
            if isinstance(segment, FixedSegment)
        }
        if not has_generated_region and not any(
            region.chain in partial_design_chains for region in self.partial_diffusion
        ):
            raise ValueError(
                "design molecules need a generated segment or a partial-diffusion region"
            )

        expected_model = {
            "protein": {"odesign_base_prot_flex", "odesign_base_prot_rigid"},
            "ligand": {"odesign_base_ligand_rigid"},
            "rna": {"odesign_base_na_rigid"},
            "dna": {"odesign_base_na_rigid"},
        }[self.design_modality]
        if self.model not in expected_model:
            raise ValueError(
                f"model {self.model!r} does not support {self.design_modality!r} design"
            )

        needs_reference = bool(
            self.hotspots or self.atom_constraints or self.partial_diffusion
        ) or any(
            isinstance(segment, FixedSegment)
            for molecule in self.molecules
            for segment in molecule.segments
        )
        if needs_reference and not self.reference_structure:
            raise ValueError("fixed structure constraints require reference_structure")
        if self.center_method == "hotspot_center" and not self.hotspots:
            raise ValueError("hotspot_center requires at least one hotspot")
        if self.center_method == "usr_provide_center" and self.user_center is None:
            raise ValueError("usr_provide_center requires user_center")
        if self.user_center is not None and self.center_method != "usr_provide_center":
            raise ValueError("user_center is only valid with usr_provide_center")
        constrained_residues = [
            (constraint.residue.chain, constraint.residue.residue)
            for constraint in self.atom_constraints
        ]
        if len(constrained_residues) != len(set(constrained_residues)):
            raise ValueError("atom_constraints may contain each residue only once")
        if self.motif_scaffolding:
            motif_ready = any(
                any(isinstance(segment, FixedSegment) for segment in molecule.segments)
                and any(isinstance(segment, GeneratedSegment) for segment in molecule.segments)
                for molecule in designed
            )
            if not motif_ready:
                raise ValueError(
                    "motif_scaffolding requires a design molecule with fixed and generated segments"
                )
        return self

    @property
    def binder_index(self) -> int:
        return next(
            index
            for index, molecule in enumerate(self.molecules)
            if molecule.id == self.evaluation.binder_molecule
        )


def load_spec(path: str | Path) -> InteractionDesignSpec:
    """Load a JSON task and resolve its reference path relative to the task."""

    spec_path = Path(path).resolve()
    payload = json.loads(spec_path.read_text(encoding="utf-8"))
    reference = payload.get("reference_structure")
    if reference and not Path(reference).is_absolute():
        payload["reference_structure"] = str((spec_path.parent / reference).resolve())
    return InteractionDesignSpec.model_validate(payload)

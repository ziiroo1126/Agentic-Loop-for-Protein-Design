"""Conversions between the domain task, evedesign, and ODesign."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from evedesign.system import DNA, RNA, Ligand, Protein, System

from interaction_design.specs import (
    FixedSegment,
    GeneratedSegment,
    InteractionDesignSpec,
    MoleculeSpec,
)

CHAIN_TYPES = {
    "protein": "proteinChain",
    "ligand": "ligand",
    "rna": "rnaChain",
    "dna": "dnaChain",
}


def spec_to_system(spec: InteractionDesignSpec) -> System:
    """Create the evedesign domain System represented by a task."""

    entities = []
    for molecule in spec.molecules:
        if molecule.type == "ligand":
            entities.append(
                Ligand(
                    id=molecule.id,
                    rep=molecule.smiles,
                    ligand_rep_type="smiles" if molecule.smiles else "user_ccd",
                )
            )
            continue

        entity_class = {"protein": Protein, "rna": RNA, "dna": DNA}[molecule.type]
        entities.append(
            entity_class(
                id=molecule.id,
                rep=None,
                min_length=molecule.min_length,
                max_length=molecule.max_length,
                cyclic=molecule.cyclic,
            )
        )
    return System(entities)


def _segment_to_native(segment: GeneratedSegment | FixedSegment) -> str:
    if isinstance(segment, GeneratedSegment):
        return f"{segment.min_length}-{segment.max_length}"
    return f"{segment.chain}/{segment.start}-{segment.end}"


def _molecule_to_native(molecule: MoleculeSpec) -> dict:
    chain: dict[str, object] = {"chain_type": CHAIN_TYPES[molecule.type]}
    if molecule.smiles is not None:
        chain["smiles"] = molecule.smiles
    else:
        chain["sequence"] = ",".join(_segment_to_native(segment) for segment in molecule.segments)
    if molecule.total_length is not None:
        chain["length"] = molecule.total_length
    if molecule.cyclic:
        chain["if_cyc"] = "true"
    if molecule.msa is not None:
        chain["msa"] = molecule.msa
    return chain


def system_to_odesign_input(
    system: System,
    spec: InteractionDesignSpec,
    entities: Sequence[int] | None = None,
) -> list[dict]:
    """Lower an evedesign System plus task constraints to ODesign JSON.

    ODesign represents designed-vs-context regions inside each chain expression.
    ``entities`` is therefore validated against the task rather than used to
    silently rewrite scientific intent.
    """

    if len(system) != len(spec.molecules):
        raise ValueError("system and task contain different numbers of molecules")
    for index, (entity, molecule) in enumerate(zip(system, spec.molecules, strict=True)):
        if entity.id != molecule.id or entity.type != molecule.type:
            raise ValueError(f"system entity {index} does not match task molecule {molecule.id!r}")

    declared_design = {
        index for index, molecule in enumerate(spec.molecules) if molecule.role == "design"
    }
    if entities is not None and set(entities) != declared_design:
        raise ValueError(
            "ODesign entity selection must exactly match role='design' in the task; "
            f"expected {sorted(declared_design)}, got {sorted(set(entities))}"
        )

    native: dict[str, object] = {
        "name": spec.name,
        "ref_file": (
            str(Path(spec.reference_structure).resolve()) if spec.reference_structure else ""
        ),
        "chains": [_molecule_to_native(molecule) for molecule in spec.molecules],
    }
    if spec.motif_scaffolding:
        native["motif_scaffolding"] = True
    if spec.hotspots:
        native["hotspot"] = ",".join(
            f"{residue.chain}/{residue.residue}" for residue in spec.hotspots
        )
    if spec.atom_constraints:
        native["condition_atom"] = {
            f"{constraint.residue.chain}/{constraint.residue.residue}": constraint.atoms
            for constraint in spec.atom_constraints
        }
    if spec.partial_diffusion:
        native["partial_diff"] = ",".join(
            f"{region.chain}/{region.start}-{region.end}" for region in spec.partial_diffusion
        )
    if spec.center_method != "none":
        native["center_method"] = spec.center_method
    if spec.user_center is not None:
        native["usr_provide_center"] = list(spec.user_center)
    return [native]

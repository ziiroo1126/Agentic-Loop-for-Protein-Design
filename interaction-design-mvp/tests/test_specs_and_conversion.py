from __future__ import annotations

import pytest
from pydantic import ValidationError

from interaction_design.conversion import spec_to_system, system_to_odesign_input
from interaction_design.specs import InteractionDesignSpec


def constrained_payload() -> dict:
    return {
        "name": "motif_and_partial_design",
        "model": "odesign_base_prot_rigid",
        "design_modality": "protein",
        "reference_structure": "reference.cif",
        "molecules": [
            {
                "id": "target",
                "type": "protein",
                "role": "context",
                "segments": [{"kind": "fixed", "chain": "B", "start": 20, "end": 133}],
            },
            {
                "id": "binder",
                "type": "protein",
                "role": "design",
                "segments": [
                    {"kind": "generated", "min_length": 8, "max_length": 20},
                    {"kind": "fixed", "chain": "A", "start": 3, "end": 7},
                    {"kind": "generated", "min_length": 6, "max_length": 10},
                ],
                "total_length": 30,
            },
        ],
        "hotspots": [{"chain": "B", "residue": 64}],
        "atom_constraints": [
            {
                "residue": {"chain": "A", "residue": 3},
                "atoms": ["NE2", "CD2"],
            }
        ],
        "partial_diffusion": [{"chain": "A", "start": 8, "end": 15}],
        "motif_scaffolding": True,
        "center_method": "hotspot_center",
        "evaluation": {"binder_molecule": "binder"},
    }


def test_domain_spec_lowers_to_native_odesign_without_leaking_native_syntax():
    spec = InteractionDesignSpec.model_validate(constrained_payload())
    system = spec_to_system(spec)
    native = system_to_odesign_input(system, spec, entities=[1])[0]

    assert [entity.id for entity in system] == ["target", "binder"]
    assert native["chains"][0]["sequence"] == "B/20-133"
    assert native["chains"][1]["sequence"] == "8-20,A/3-7,6-10"
    assert native["chains"][1]["length"] == 30
    assert native["hotspot"] == "B/64"
    assert native["condition_atom"] == {"A/3": ["NE2", "CD2"]}
    assert native["partial_diff"] == "A/8-15"


def test_entity_selection_cannot_silently_change_declared_scientific_intent():
    spec = InteractionDesignSpec.model_validate(constrained_payload())
    with pytest.raises(ValueError, match="exactly match"):
        system_to_odesign_input(spec_to_system(spec), spec, entities=[0])


def test_modality_and_model_must_agree():
    payload = constrained_payload()
    payload["model"] = "odesign_base_na_rigid"
    with pytest.raises(ValidationError, match="does not support"):
        InteractionDesignSpec.model_validate(payload)


@pytest.mark.parametrize(
    ("modality", "chain_type"),
    [("rna", "rnaChain"), ("dna", "dnaChain")],
)
def test_nucleic_acid_tasks_are_first_class(modality, chain_type):
    spec = InteractionDesignSpec.model_validate(
        {
            "name": f"free_{modality}",
            "model": "odesign_base_na_rigid",
            "design_modality": modality,
            "molecules": [
                {
                    "id": "aptamer",
                    "type": modality,
                    "role": "design",
                    "segments": [{"kind": "generated", "min_length": 30, "max_length": 50}],
                }
            ],
            "evaluation": {"binder_molecule": "aptamer"},
        }
    )
    system = spec_to_system(spec)
    native = system_to_odesign_input(system, spec)[0]
    assert system[0].type == modality
    assert native["chains"] == [{"chain_type": chain_type, "sequence": "30-50"}]


def test_ligand_design_and_partial_redesign_have_explicit_domain_forms():
    ligand_spec = InteractionDesignSpec.model_validate(
        {
            "name": "design_ligand",
            "model": "odesign_base_ligand_rigid",
            "design_modality": "ligand",
            "reference_structure": "target.cif",
            "molecules": [
                {
                    "id": "target",
                    "type": "protein",
                    "role": "context",
                    "segments": [{"kind": "fixed", "chain": "A", "start": 1, "end": 80}],
                },
                {
                    "id": "ligand",
                    "type": "ligand",
                    "role": "design",
                    "segments": [{"kind": "generated", "min_length": 12, "max_length": 18}],
                },
            ],
            "evaluation": {"binder_molecule": "ligand"},
        }
    )
    ligand_native = system_to_odesign_input(spec_to_system(ligand_spec), ligand_spec)[0]
    assert ligand_native["chains"][1] == {
        "chain_type": "ligand",
        "sequence": "12-18",
    }

    partial_payload = constrained_payload()
    partial_payload["molecules"][1]["segments"] = [
        {"kind": "fixed", "chain": "A", "start": 1, "end": 30}
    ]
    partial_payload["molecules"][1].pop("total_length")
    partial_payload["motif_scaffolding"] = False
    partial_spec = InteractionDesignSpec.model_validate(partial_payload)
    assert (
        system_to_odesign_input(spec_to_system(partial_spec), partial_spec)[0]["partial_diff"]
        == "A/8-15"
    )

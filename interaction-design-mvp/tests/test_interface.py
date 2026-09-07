from __future__ import annotations

import numpy as np
import pytest
from biotite.structure import AtomArray

from interaction_design.evaluation.feedback import map_target_residues
from interaction_design.evaluation.interface import (
    geometry_flags,
    interface_geometry,
    protein_chain,
)
from interaction_design.specs import InteractionDesignSpec


def make_atoms(rows):
    atoms = AtomArray(len(rows))
    atoms.chain_id = np.array([x[0] for x in rows])
    atoms.res_id = np.array([x[1] for x in rows])
    atoms.res_name = np.array([x[2] for x in rows])
    atoms.atom_name = np.array([x[3] for x in rows])
    atoms.element = np.array([x[4] for x in rows])
    atoms.coord = np.array([x[5] for x in rows], dtype=float)
    return atoms


def geometry_example(distance=4.0):
    return make_atoms(
        [
            ("A", 7, "ALA", "CA", "C", [0, 0, 0]),
            ("A", 7, "ALA", "N", "N", [0, 1, 0]),
            ("B", 101, "ALA", "CA", "C", [distance, 0, 0]),
            ("B", 101, "ALA", "N", "N", [distance, 1, 0]),
            ("B", 102, "GLY", "CA", "C", [20, 0, 0]),
        ]
    )


def test_contacts_count_residue_pairs_not_atom_pairs():
    result = interface_geometry(geometry_example(), "A", "B", hotspot_positions=[1, 2])
    assert result["contact_residue_pair_count"] == 1
    assert result["binder_contact_positions"] == [1]
    assert result["target_contact_positions"] == [1]
    assert result["hotspot_coverage"] == 0.5
    assert result["clash_atom_pair_count"] == 0
    assert result["minimum_interchain_distance_angstrom"] == 4
    assert geometry_flags(result) == ["PARTIAL_REQUESTED_HOTSPOT_CONTACT"]


@pytest.mark.parametrize(
    "distance,contacts,clashes", [(5.0, 0, 0), (4.999, 1, 0), (2.0, 1, 0), (1.99, 1, 2)]
)
def test_strict_distance_boundaries(distance, contacts, clashes):
    result = interface_geometry(geometry_example(distance), "A", "B")
    assert result["contact_residue_pair_count"] == contacts
    assert result["clash_atom_pair_count"] == clashes
    assert result["clash_residue_pair_count"] == int(clashes > 0)
    assert result["hotspot_coverage"] is None


def test_hydrogens_and_hetero_atoms_are_excluded():
    original = geometry_example()
    extra = make_atoms(
        [
            ("B", 101, "ALA", "H", "H", [0.1, 0, 0]),
            ("B", 101, "ALA", "D", "D", [0.1, 0, 0]),
            ("B", 200, "HOH", "O", "O", [0.1, 0, 0]),
        ]
    )
    extra.hetero[-1] = True
    assert interface_geometry(original + extra, "A", "B") == interface_geometry(original, "A", "B")


def test_geometry_is_rigid_transform_invariant_and_detects_separation():
    atoms = geometry_example(1.99)
    original = interface_geometry(atoms, "A", "B", hotspot_positions=[1])
    moved = atoms.copy()
    moved.coord = moved.coord @ np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]]) + [10, -7, 4]
    result = interface_geometry(moved, "A", "B", hotspot_positions=[1])
    for key in ["contact_residue_pair_count", "clash_atom_pair_count", "hotspots_contacted"]:
        assert result[key] == original[key]
    moved.coord[moved.chain_id == "A"] += [1000, 0, 0]
    separate = interface_geometry(moved, "A", "B", hotspot_positions=[1])
    assert geometry_flags(separate) == ["NO_TARGET_CONTACT", "NO_REQUESTED_HOTSPOT_CONTACT"]


@pytest.mark.parametrize("mutation", ["nan", "duplicate", "missing_ca", "unknown_element"])
def test_ambiguous_or_invalid_atoms_are_rejected(mutation):
    atoms = geometry_example()
    if mutation == "nan":
        atoms.coord[0, 0] = np.nan
    elif mutation == "duplicate":
        atoms = atoms + atoms[:1]
    elif mutation == "missing_ca":
        atoms.atom_name[0] = "CB"
    else:
        atoms.element[0] = ""
    with pytest.raises(ValueError):
        interface_geometry(atoms, "A", "B")


def reference_example():
    atoms = make_atoms(
        [
            ("X", 20, "ALA", "CA", "C", [0, 0, 0]),
            ("X", 21, "CYS", "CA", "C", [1, 0, 0]),
            ("X", 22, "ASP", "CA", "C", [2, 0, 0]),
        ]
    )
    spec = InteractionDesignSpec.model_validate(
        {
            "name": "mapping",
            "model": "odesign_base_prot_flex",
            "design_modality": "protein",
            "reference_structure": "unused.cif",
            "molecules": [
                {
                    "id": "target",
                    "role": "context",
                    "type": "protein",
                    "segments": [{"kind": "fixed", "chain": "X", "start": 20, "end": 22}],
                },
                {
                    "id": "binder",
                    "role": "design",
                    "type": "protein",
                    "segments": [{"kind": "generated", "min_length": 3, "max_length": 3}],
                },
            ],
            "evaluation": {"binder_molecule": "binder"},
            "hotspots": [{"chain": "X", "residue": 21}],
        }
    )
    return atoms, spec


def test_hotspot_mapping_uses_reference_identities_and_segment_order():
    atoms, spec = reference_example()
    result = map_target_residues(spec, atoms, "ACD")
    assert result["hotspots"][0] == {
        "target_position": 2,
        "source_chain": "X",
        "source_residue": 21,
        "res_name": "CYS",
    }
    raw = spec.model_dump(mode="json")
    raw["molecules"][0]["segments"] = [
        {"kind": "fixed", "chain": "X", "start": 22, "end": 22},
        {"kind": "fixed", "chain": "X", "start": 20, "end": 21},
    ]
    result = map_target_residues(InteractionDesignSpec.model_validate(raw), atoms, "DAC")
    assert result["hotspots"][0]["target_position"] == 3


@pytest.mark.parametrize("mutation", ["missing_residue", "insertion", "sequence", "hotspot"])
def test_hotspot_mapping_rejects_ambiguous_correspondence(mutation):
    atoms, spec = reference_example()
    sequence = "ACD"
    if mutation == "missing_residue":
        atoms = atoms[[0, 2]]
    elif mutation == "insertion":
        atoms.ins_code[1] = "A"
    elif mutation == "sequence":
        sequence = "AAA"
    else:
        # The task schema permits this hotspot; the evaluated target must also contain it.
        raw = spec.model_dump(mode="json")
        raw["hotspots"] = [{"chain": "X", "residue": 30}]
        spec = InteractionDesignSpec.model_validate(raw)
    with pytest.raises(ValueError):
        map_target_residues(spec, atoms, sequence)


def test_clash_details_are_bounded_but_counts_are_complete():
    rows = [
        (chain, i + 1, "ALA", "CA", "C", [i * 0.001, 0, 0])
        for chain in ["A", "B"]
        for i in range(140)
    ]
    result = interface_geometry(make_atoms(rows), "A", "B")
    assert result["clash_atom_pair_count"] == 140**2
    assert result["clash_residue_pair_count"] == 140**2
    assert len(result["closest_clashes"]) == 20
    assert result["closest_clashes_truncated"]
    _, residues, indices = protein_chain(make_atoms(rows), "A")
    assert len(residues) == 140 and indices.max() == 139

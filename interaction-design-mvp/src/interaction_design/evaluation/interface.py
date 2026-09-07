"""Protein interface geometry with explicit atom and residue counting semantics."""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from biotite.structure.io.pdb import PDBFile
from biotite.structure.io.pdbx import CIFFile, get_structure

from interaction_design.outputs import PROTEIN_3_TO_1


def read_atoms(path: Path, *, author: bool = False):
    if path.suffix.lower() == ".pdb":
        return PDBFile.read(path).get_structure(model=1, altloc="first")
    if path.suffix.lower() in {".cif", ".mmcif"}:
        return get_structure(CIFFile.read(path), model=1, use_author_fields=author, altloc="first")
    raise ValueError("geometry requires a PDB or mmCIF structure")


def protein_chain(atoms, chain: str):
    selected = atoms[(atoms.chain_id == chain) & ~atoms.hetero]
    selected = selected[~np.isin(np.char.upper(selected.element), ["H", "D"])]
    if not len(selected) or not np.isfinite(selected.coord).all():
        raise ValueError(f"chain {chain} is empty or has non-finite coordinates")
    if set(np.char.upper(selected.element)) - {"C", "N", "O", "S"}:
        raise ValueError(f"chain {chain} contains unsupported or missing protein elements")
    residues, indices, seen_atoms = {}, [], set()
    for atom in selected:
        key = (int(atom.res_id), str(atom.ins_code))
        name = str(atom.res_name)
        atom_key = (*key, str(atom.atom_name))
        if name not in PROTEIN_3_TO_1 or atom_key in seen_atoms:
            raise ValueError(f"chain {chain} contains unsupported residues or duplicate atoms")
        seen_atoms.add(atom_key)
        if key not in residues:
            residues[key] = {
                "position": len(residues) + 1,
                "residue": key[0],
                "insertion_code": key[1],
                "res_name": name,
                "ca_count": 0,
            }
        residue = residues[key]
        if residue["res_name"] != name:
            raise ValueError("inconsistent residue names")
        residue["ca_count"] += int(atom.atom_name == "CA")
        indices.append(residue["position"] - 1)
    if any(x["ca_count"] != 1 for x in residues.values()):
        raise ValueError("each protein residue must contain exactly one C-alpha atom")
    table = [{k: v for k, v in row.items() if k != "ca_count"} for row in residues.values()]
    return selected, table, np.asarray(indices, dtype=np.int32)


def residue_sequence(table: list[dict]) -> str:
    return "".join(PROTEIN_3_TO_1[x["res_name"]] for x in table)


def interface_geometry(
    atoms,
    binder_chain: str,
    target_chain: str,
    *,
    hotspot_positions: list[int] | None = None,
    contact_distance: float = 5.0,
    clash_distance: float = 2.0,
) -> dict:
    """Count strict distance contacts; all positions in output are one-based sequence indices."""
    if binder_chain == target_chain:
        raise ValueError("binder and target chains must differ")
    if not (
        math.isfinite(contact_distance)
        and math.isfinite(clash_distance)
        and 0 < clash_distance < contact_distance
    ):
        raise ValueError("distance cutoffs must be finite, positive and ordered")
    a, a_res, a_index = protein_chain(atoms, binder_chain)
    b, b_res, b_index = protein_chain(atoms, target_chain)
    positions = [] if hotspot_positions is None else hotspot_positions
    if len(set(positions)) != len(positions) or any(
        type(p) is not int or not 1 <= p <= len(b_res) for p in positions
    ):
        raise ValueError("hotspot positions must be unique valid target sequence indices")
    minimum_squared = np.full((len(a_res), len(b_res)), np.inf)
    atom_clashes, closest = 0, []
    b_coord = b.coord.astype(np.float64)
    for start in range(0, len(a), 128):
        block = a.coord[start : start + 128].astype(np.float64)
        delta = block[:, None, :] - b_coord[None, :, :]
        distances = np.einsum("ijk,ijk->ij", delta, delta)
        np.minimum.at(
            minimum_squared,
            (a_index[start : start + len(block), None], b_index[None, :]),
            distances,
        )
        ii, jj = np.where(distances < clash_distance**2)
        atom_clashes += len(ii)
        # Retain the 20 closest clashes, while counting every pair.
        order = np.argsort(distances[ii, jj], kind="stable")[:20]
        for k in order:
            i, j = int(start + ii[k]), int(jj[k])
            closest.append(
                {
                    "binder_position": int(a_index[i] + 1),
                    "binder_atom": str(a.atom_name[i]),
                    "target_position": int(b_index[j] + 1),
                    "target_atom": str(b.atom_name[j]),
                    "distance_angstrom": float(np.sqrt(distances[ii[k], j])),
                }
            )
        closest.sort(
            key=lambda x: (
                x["distance_angstrom"],
                x["binder_position"],
                x["target_position"],
                x["binder_atom"],
                x["target_atom"],
            )
        )
        closest = closest[:20]
    contact_mask = minimum_squared < contact_distance**2
    clash_mask = minimum_squared < clash_distance**2
    contacts = [
        {
            "binder_position": int(i + 1),
            "target_position": int(j + 1),
            "minimum_distance_angstrom": float(np.sqrt(minimum_squared[i, j])),
        }
        for i, j in zip(*np.where(contact_mask), strict=True)
    ]
    target_minimum = np.sqrt(minimum_squared.min(axis=0))
    hotspot_rows = [
        {
            "target_position": p,
            "minimum_distance_angstrom": float(target_minimum[p - 1]),
            "contact": bool(target_minimum[p - 1] < contact_distance),
            "clashing": bool(target_minimum[p - 1] < clash_distance),
        }
        for p in positions
    ]
    covered = sum(x["contact"] for x in hotspot_rows)
    return {
        "binder_chain": binder_chain,
        "target_chain": target_chain,
        "binder_residue_count": len(a_res),
        "target_residue_count": len(b_res),
        "binder_heavy_atom_count": len(a),
        "target_heavy_atom_count": len(b),
        "contact_residue_pair_count": len(contacts),
        "binder_contact_positions": (np.where(contact_mask.any(axis=1))[0] + 1).tolist(),
        "target_contact_positions": (np.where(contact_mask.any(axis=0))[0] + 1).tolist(),
        "minimum_interchain_distance_angstrom": float(np.sqrt(minimum_squared.min())),
        "clash_atom_pair_count": atom_clashes,
        "clash_residue_pair_count": int(clash_mask.sum()),
        "closest_clashes": closest,
        "closest_clashes_truncated": atom_clashes > len(closest),
        "hotspot_count": len(positions),
        "hotspots_contacted": covered,
        "hotspot_coverage": covered / len(positions) if positions else None,
        "hotspots": hotspot_rows,
        "contacts": contacts,
    }


def geometry_flags(geometry: dict) -> list[str]:
    flags = []
    if geometry["contact_residue_pair_count"] == 0:
        flags.append("NO_TARGET_CONTACT")
    if geometry["hotspot_count"]:
        if geometry["hotspots_contacted"] == 0:
            flags.append("NO_REQUESTED_HOTSPOT_CONTACT")
        elif geometry["hotspots_contacted"] < geometry["hotspot_count"]:
            flags.append("PARTIAL_REQUESTED_HOTSPOT_CONTACT")
    if geometry["clash_atom_pair_count"]:
        flags.append("INTERCHAIN_HEAVY_ATOM_CLASH")
    return flags

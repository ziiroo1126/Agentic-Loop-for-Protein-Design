"""Native AF3 inputs and identity checks for the two-protein binder protocol."""

from __future__ import annotations

import copy
import math
from pathlib import Path

import numpy as np
from biotite.structure import get_residues
from biotite.structure.io.pdbx import CIFFile, get_structure

from interaction_design.outputs import PROTEIN_3_TO_1
from interaction_design.scoring.af3 import extract_af3_metrics


def _msa_rows(msa: str, sequence: str) -> list[str]:
    """Compare alignment rows, allowing AF3 to materialize a query-only empty MSA."""
    if not msa:
        return [sequence]
    rows = []
    current = []
    for line in msa.splitlines():
        if line.startswith(">"):
            if current:
                rows.append("".join(current))
                current = []
        else:
            current.append(line.strip())
    if current:
        rows.append("".join(current))
    return rows


def _msa_query(msa: str, sequence: str) -> None:
    if not msa:
        return
    rows = msa.splitlines()
    if not rows or not rows[0].startswith(">"):
        raise ValueError("target MSA must be A3M with a query header")
    query = []
    for row in rows[1:]:
        if row.startswith(">"):
            break
        query.append(row.strip())
    if "".join(query) != sequence:
        raise ValueError("target MSA query does not match target sequence")


def target_features(data: dict, sequence: str) -> dict:
    matching = [
        item["protein"]
        for item in data.get("sequences", [])
        if "protein" in item and item["protein"].get("sequence") == sequence
    ]
    if len(matching) != 1:
        raise ValueError("target data must contain exactly one matching protein sequence")
    protein = matching[0]
    features = {}
    for name in ("unpairedMsa", "pairedMsa"):
        if not isinstance(protein.get(name), str):
            raise ValueError(f"target data requires inline {name}, as emitted by AF3 _data.json")
        _msa_query(protein[name], sequence)
        features[name] = protein[name]
    templates = protein.get("templates")
    if not isinstance(templates, list):
        raise ValueError("target data requires a templates list")
    for template in templates:
        if not isinstance(template.get("mmcif"), str) or not template["mmcif"]:
            raise ValueError("target templates must contain inline mmcif, not external file paths")
        query = template.get("queryIndices", [])
        indices = template.get("templateIndices", [])
        if (
            len(query) != len(indices)
            or any(not isinstance(i, int) or not 0 <= i < len(sequence) for i in query)
            or any(not isinstance(i, int) or i < 0 for i in indices)
        ):
            raise ValueError("invalid target template residue mapping")
    features["templates"] = copy.deepcopy(templates)
    return features


def make_input(
    name: str,
    binder_sequence: str,
    target_sequence: str,
    *,
    seed: int,
    msa_mode: str,
    target_data: dict | None = None,
) -> dict:
    if msa_mode not in {"search", "provided", "none"}:
        raise ValueError("MSA mode must be search, provided, or none")
    empty = {"unpairedMsa": "", "pairedMsa": "", "templates": []}
    binder = {"id": "A", "sequence": binder_sequence, **empty}
    target = {"id": "B", "sequence": target_sequence}
    if msa_mode == "none":
        target.update(empty)
    elif msa_mode == "provided":
        target.update(target_features(target_data or {}, target_sequence))
    elif target_data is not None:
        raise ValueError("target_data requires MSA mode provided")
    return {
        "name": name,
        "modelSeeds": [seed],
        "sequences": [
            {"protein": binder},
            {"protein": target},
        ],
        "dialect": "alphafold3",
        "version": 1,
    }


def validate_refold(model: Path, data: dict, summary: dict, request: dict) -> None:
    if data.get("name") != request["name"] or data.get("modelSeeds") != request["modelSeeds"]:
        raise ValueError("AF3 output job name/seeds differ from the prepared request")
    expected = [item["protein"] for item in request["sequences"]]
    actual = [item.get("protein", {}) for item in data.get("sequences", [])]
    if [(x.get("id"), x.get("sequence")) for x in actual] != [
        (x["id"], x["sequence"]) for x in expected
    ]:
        raise ValueError("AF3 output chain IDs/sequences differ from the prepared request")
    for original, processed in zip(expected, actual, strict=True):
        for field in ("unpairedMsa", "pairedMsa", "templates"):
            if field not in original:
                continue
            wanted, found = original[field], processed.get(field)
            if field.endswith("Msa") and isinstance(found, str):
                wanted = _msa_rows(wanted, original["sequence"])
                found = _msa_rows(found, original["sequence"])
            if wanted != found:
                raise ValueError(f"AF3 output changed explicit {field} for chain {original['id']}")
    atoms = get_structure(CIFFile.read(model), model=1, use_author_fields=False)
    if list(dict.fromkeys(atoms.chain_id)) != ["A", "B"]:
        raise ValueError("AF3 model must have binder A followed by target B")
    if not np.isfinite(atoms.coord).all():
        raise ValueError("AF3 model contains non-finite coordinates")
    for protein in expected:
        _, residues = get_residues(atoms[atoms.chain_id == protein["id"]])
        sequence = "".join(PROTEIN_3_TO_1.get(str(residue), "X") for residue in residues)
        if sequence != protein["sequence"]:
            raise ValueError(f"AF3 model sequence mismatch in chain {protein['id']}")
    if summary.get("chain_ids", ["A", "B"]) != ["A", "B"]:
        raise ValueError("AF3 summary chain order differs from binder A / target B")
    if len(summary.get("chain_ptm", [])) != 2:
        raise ValueError("AF3 summary must contain exactly two protein chains")
    metrics = extract_af3_metrics(summary, binder_index=0)
    if any(not math.isfinite(value) or value < 0 for value in metrics.values()):
        raise ValueError("AF3 summary metrics must be finite and nonnegative")
    if any(value > 1 for key, value in metrics.items() if "ptm" in key):
        raise ValueError("AF3 pTM/ipTM metrics must be within [0, 1]")

"""Deterministic CPU-only executor used for examples and contract tests."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from biotite.structure import AtomArray
from biotite.structure.io import pdbx

from interaction_design.runtime.base import ExecutionResult, ODesignRunRequest

AA_1_TO_3 = {
    "A": "ALA",
    "C": "CYS",
    "D": "ASP",
    "E": "GLU",
    "F": "PHE",
    "G": "GLY",
    "H": "HIS",
    "I": "ILE",
    "K": "LYS",
    "L": "LEU",
    "M": "MET",
    "N": "ASN",
    "P": "PRO",
    "Q": "GLN",
    "R": "ARG",
    "S": "SER",
    "T": "THR",
    "V": "VAL",
    "W": "TRP",
    "Y": "TYR",
}


def _chain_id(index: int) -> str:
    """Match ODesign's A..Z, AA.. chain assignment for practical MVP sizes."""

    value = index + 1
    chars: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def _sequence_for(molecule, length: int, rng: random.Random) -> str:
    if molecule.type == "protein":
        alphabet = "ACDEFGHIKLMNPQRSTVWY"
    elif molecule.type == "dna":
        alphabet = "ACGT"
    elif molecule.type == "rna":
        alphabet = "ACGU"
    else:
        return ""
    return "".join(rng.choice(alphabet) for _ in range(length))


def _residue_name(molecule_type: str, symbol: str) -> str:
    if molecule_type == "protein":
        return AA_1_TO_3[symbol]
    if molecule_type == "dna":
        return f"D{symbol}"
    if molecule_type == "rna":
        return symbol
    return "LIG"


def _write_mock_cif(request: ODesignRunRequest, path: Path, seed: int) -> None:
    rng = random.Random(seed)
    rows: list[tuple[str, int, str, str, str, bool, tuple[float, float, float]]] = []
    x = 0.0
    for index, molecule in enumerate(request.spec.molecules):
        chain = _chain_id(index)
        if molecule.type == "ligand":
            rows.append((chain, 1, "LIG", "C1", "C", True, (x, 0.0, 0.0)))
            x += 3.0
            continue
        min_length = molecule.min_length or 1
        max_length = molecule.max_length or min_length
        length = rng.randint(min_length, max_length)
        sequence = _sequence_for(molecule, length, rng)
        atom_name = "CA" if molecule.type == "protein" else "P"
        element = "C" if molecule.type == "protein" else "P"
        for residue, symbol in enumerate(sequence, start=1):
            rows.append(
                (
                    chain,
                    residue,
                    _residue_name(molecule.type, symbol),
                    atom_name,
                    element,
                    False,
                    (x, float(index) * 4.0, 0.0),
                )
            )
            x += 1.5

    atoms = AtomArray(len(rows))
    atoms.chain_id = np.array([row[0] for row in rows])
    atoms.res_id = np.array([row[1] for row in rows])
    atoms.res_name = np.array([row[2] for row in rows])
    atoms.atom_name = np.array([row[3] for row in rows])
    atoms.element = np.array([row[4] for row in rows])
    atoms.hetero = np.array([row[5] for row in rows])
    atoms.coord = np.array([row[6] for row in rows], dtype=float)
    atoms.set_annotation("b_factor", np.zeros(len(rows), dtype=float))
    atoms.set_annotation("occupancy", np.ones(len(rows), dtype=float))
    atoms.set_annotation("charge", np.zeros(len(rows), dtype=int))
    cif_file = pdbx.CIFFile()
    pdbx.set_structure(cif_file, atoms)
    cif_file.write(str(path))


@dataclass
class MockODesignExecutor:
    """Create structurally valid, deterministic ODesign-shaped artifacts."""

    name: str = "mock"

    def execute(self, request: ODesignRunRequest) -> ExecutionResult:
        predictions = request.output_dir / request.spec.name / "mock" / "predictions"
        predictions.mkdir(parents=True, exist_ok=True)
        seeds = request.spec.generation.seeds
        for index in range(request.num_designs):
            seed = seeds[index % len(seeds)]
            digest = hashlib.sha256(f"{request.spec.name}:{seed}:{index}".encode()).digest()
            quality = int.from_bytes(digest[:4], "big") / (2**32 - 1)
            candidate = f"{request.spec.name}_seed_{seed}_bb_{index}_seq_0"
            cif_path = predictions / f"{candidate}.cif"
            _write_mock_cif(request, cif_path, seed * 100_000 + index)

            chain_count = len(request.spec.molecules)
            binder = request.spec.binder_index
            ipae = round(4.0 + (1.0 - quality) * 12.0, 4)
            pair_pae = [
                [0.0 if i == j else ipae for j in range(chain_count)] for i in range(chain_count)
            ]
            iptm = round(0.55 + quality * 0.40, 5)
            binder_ptm = round(0.48 + quality * 0.45, 5)
            af3 = {
                "iptm": iptm,
                "ptm": round(0.50 + quality * 0.40, 5),
                "chain_iptm": [iptm] * chain_count,
                "chain_ptm": [
                    binder_ptm if i == binder else round(0.62 + quality * 0.25, 5)
                    for i in range(chain_count)
                ],
                "chain_pair_pae_min": pair_pae,
            }
            rosetta = {
                "ddg": round(-20.0 - quality * 40.0, 5),
                "sap_score": round(50.0 - quality * 25.0, 5),
                "contact_molecular_surface": round(280.0 + quality * 520.0, 5),
            }
            cif_path.with_suffix(".af3.json").write_text(
                json.dumps(af3, indent=2) + "\n", encoding="utf-8"
            )
            cif_path.with_suffix(".rosetta.json").write_text(
                json.dumps(rosetta, indent=2) + "\n", encoding="utf-8"
            )

        return ExecutionResult(
            output_dir=request.output_dir,
            command=("mock-odesign", "--num-designs", str(request.num_designs)),
            executor=self.name,
            metadata={"deterministic": True, "schema": "odesign-output-v1"},
        )

"""Single-candidate worker for a separately installed PyRosetta environment.

Uses ODesign-pipeline's unchanged PPI RosettaScripts XML and JD2 scoring path.
This file can run under Python 3.10 without importing the Python >=3.12 app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shlex
from pathlib import Path


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-cif", type=Path, required=True)
    parser.add_argument("--protocol-xml", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    args = parser.parse_args()
    import pyrosetta
    from biotite.structure.io.pdb import PDBFile
    from biotite.structure.io.pdbx import CIFFile, get_structure

    root = args.output.resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    atoms = get_structure(CIFFile.read(args.input_cif), model=1, use_author_fields=False)
    if list(dict.fromkeys(atoms.chain_id)) != ["A", "B"]:
        raise ValueError("PPI protocol requires binder A followed by target B")
    pdb = PDBFile()
    pdb.set_structure(atoms)
    pdb_path = root / "refold.pdb"
    pdb.write(pdb_path)
    input_list = root / "inputs.txt"
    input_list.write_text(str(pdb_path) + "\n")
    raw_score = root / "scores.jsonl"
    flags = shlex.join(
        [
            "-beta_nov16",
            "-in:file:l",
            str(input_list),
            "-out:file:score_only",
            str(raw_score),
            "-out:file:scorefile_format",
            "json",
            "-mute",
            "all",
            "-constant_seed",
            "-jran",
            str(args.seed),
        ]
    )
    pyrosetta.init(flags)
    xml = args.protocol_xml.read_text()
    objects = pyrosetta.rosetta.protocols.rosetta_scripts.XmlObjects.create_from_string(xml)
    protocol = objects.get_mover("ParsedProtocol")
    pyrosetta.rosetta.protocols.jd2.JobDistributor.get_instance().go(protocol)
    rows = [json.loads(line) for line in raw_score.read_text().splitlines() if line.strip()]
    if len(rows) != 1:
        raise ValueError(f"expected one Rosetta score row, got {len(rows)}")
    metrics = {
        key: float(rows[0][key]) for key in ("ddg", "sap_score", "contact_molecular_surface")
    }
    if any(not math.isfinite(value) for value in metrics.values()):
        raise ValueError("non-finite Rosetta metric")
    metrics["provenance"] = {
        "structure_sha256": checksum(args.input_cif),
        "protocol_sha256": checksum(args.protocol_xml),
        "binder_chain": "A",
        "target_chain": "B",
        "seed": args.seed,
        "pyrosetta_version": str(pyrosetta.version()),
        "raw_score_sha256": checksum(raw_score),
    }
    args.output.write_text(json.dumps(metrics, indent=2, allow_nan=False) + "\n")


if __name__ == "__main__":
    main()

"""Parse ODesign CIF artifacts into evedesign SystemInstance objects."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
from evedesign.structure import StructureFile
from evedesign.system import EntityInstance, System, SystemInstance

from interaction_design.runtime.base import ODesignExecutionError
from interaction_design.specs import InteractionDesignSpec

PROTEIN_3_TO_1 = {
    "ALA": "A",
    "CYS": "C",
    "ASP": "D",
    "GLU": "E",
    "PHE": "F",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LYS": "K",
    "LEU": "L",
    "MET": "M",
    "ASN": "N",
    "PRO": "P",
    "GLN": "Q",
    "ARG": "R",
    "SER": "S",
    "THR": "T",
    "VAL": "V",
    "TRP": "W",
    "TYR": "Y",
}
OUTPUT_PATTERN = re.compile(r"_seed_(-?\d+)_bb_(\d+)_seq_(\d+)$")


def _chain_id(index: int) -> str:
    value = index + 1
    chars: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        chars.append(chr(65 + remainder))
    return "".join(reversed(chars))


def _chain_sequence(chain, molecule_type: str) -> str:
    residue_frame = chain.res_df()
    residue_names = [str(value).upper() for value in residue_frame["res_name"]]
    if molecule_type == "protein":
        try:
            return "".join(PROTEIN_3_TO_1[name] for name in residue_names)
        except KeyError as error:
            raise ODesignExecutionError(
                f"unsupported protein residue in ODesign output: {error.args[0]}"
            ) from error
    if molecule_type == "dna":
        sequence = "".join(name[1:] if name.startswith("D") else name for name in residue_names)
        if not set(sequence).issubset(set("ACGT")):
            raise ODesignExecutionError(
                f"unsupported DNA residue(s) in ODesign output: {residue_names}"
            )
        return sequence
    if molecule_type == "rna":
        sequence = "".join(residue_names)
        if not set(sequence).issubset(set("ACGU")):
            raise ODesignExecutionError(
                f"unsupported RNA residue(s) in ODesign output: {residue_names}"
            )
        return sequence
    raise ValueError(f"cannot extract a polymer sequence for {molecule_type}")


def parse_odesign_outputs(
    output_dir: str | Path,
    system: System,
    spec: InteractionDesignSpec,
) -> list[SystemInstance]:
    """Recursively parse ODesign prediction CIFs in stable path order."""

    root = Path(output_dir)
    cif_paths = sorted(path for path in root.rglob("*.cif") if "predictions" in path.parts)
    if not cif_paths:
        raise ODesignExecutionError(
            f"ODesign completed but no prediction CIF files were found under {root}"
        )

    instances: list[SystemInstance] = []
    failures: list[str] = []
    for cif_path in cif_paths:
        try:
            structure = StructureFile(str(cif_path), format="cif").get_model()
            available = set(structure.chains())
            entity_instances: list[EntityInstance] = []
            for index, (entity, molecule) in enumerate(zip(system, spec.molecules, strict=True)):
                chain_name = _chain_id(index)
                if chain_name not in available:
                    raise ODesignExecutionError(
                        f"expected output chain {chain_name!r}, found {sorted(available)}"
                    )
                chain = structure.get_chain(chain_name)
                if molecule.type == "ligand":
                    rep = entity.rep.copy() if entity.rep is not None else None
                else:
                    rep = np.array(list(_chain_sequence(chain, molecule.type)), dtype="U1")
                entity_instances.append(EntityInstance(rep=rep, models={"odesign": chain}))

            artifacts: dict[str, str] = {"structure": str(cif_path.resolve())}
            af3_path = cif_path.with_suffix(".af3.json")
            rosetta_path = cif_path.with_suffix(".rosetta.json")
            if af3_path.is_file():
                artifacts["af3_summary"] = str(af3_path.resolve())
            if rosetta_path.is_file():
                artifacts["rosetta_metrics"] = str(rosetta_path.resolve())

            match = OUTPUT_PATTERN.search(cif_path.stem)
            odesign_meta: dict[str, int] = {}
            if match:
                odesign_meta = {
                    "seed": int(match.group(1)),
                    "backbone_index": int(match.group(2)),
                    "sequence_index": int(match.group(3)),
                }
            instances.append(
                SystemInstance(
                    entity_instances,
                    id=cif_path.stem,
                    metadata={
                        "artifacts": artifacts,
                        "odesign": odesign_meta,
                    },
                )
            )
        except Exception as error:  # retain all parse failures for one actionable error
            failures.append(f"{cif_path}: {error}")

    if failures:
        sample = "\n".join(failures[:10])
        raise ODesignExecutionError(f"failed to parse {len(failures)} ODesign output(s):\n{sample}")
    return instances

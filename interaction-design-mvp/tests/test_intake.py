"""Task preparation uses synthetic coordinates and never performs design inference."""

from __future__ import annotations

import json
from copy import deepcopy

import numpy as np
import pytest
from biotite.structure import AtomArray
from biotite.structure.io.pdb import PDBFile
from biotite.structure.io.pdbx import CIFFile, set_structure
from pydantic import ValidationError

from interaction_design.cli import main
from interaction_design.intake import DesignBrief, build_task, load_brief, review_brief
from interaction_design.pipeline_config import validate_pipeline_task
from interaction_design.specs import load_spec


@pytest.fixture(params=[".pdb", ".cif", ".mmcif"])
def complete_brief(tmp_path, request):
    atoms = AtomArray(3)
    atoms.chain_id = np.array(["X", "X", "X"])
    atoms.res_id = np.array([20, 21, 22])
    atoms.res_name = np.array(["ALA", "CYS", "ASP"])
    atoms.atom_name = np.array(["CA"] * 3)
    atoms.element = np.array(["C"] * 3)
    atoms.coord = np.array([[0, 0, 0], [1, 0, 0], [2, 0, 0]], dtype=float)
    reference = tmp_path / f"synthetic{request.param}"
    if request.param == ".pdb":
        structure = PDBFile()
        structure.set_structure(atoms)
    else:
        structure = CIFFile()
        set_structure(structure, atoms)
    structure.write(reference)
    payload = {
        "name": "synthetic_intake",
        "goal": "Synthetic software validation only.",
        "target": {
            "identifier": "synthetic reference",
            "reference_structure": reference.name,
            "segments": [{"chain": "X", "start": 20, "end": 22}],
            "hotspots": [{"chain": "X", "residue": 21}],
        },
        "binder": {"length": {"min": 5, "max": 7}, "selected_length": 6},
        "seeds": [0, 2**31 - 1],
    }
    path = tmp_path / "brief.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload, reference


def test_init_and_incomplete_review_preserve_unknowns(tmp_path, capsys):
    path = tmp_path / "brief.json"
    assert main(["task", "init", "--name", "new_design", "--output", str(path)]) == 0
    capsys.readouterr()
    initial = path.read_bytes()
    assert main(["task", "review", str(path)]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "needs_input"
    assert result["task"] is None
    assert result["brief"]["target"]["hotspots"] is None
    assert result["execution_settings"]["selected_length"] is None
    assert {issue["field"] for issue in result["issues"]} == {
        "target.reference_structure",
        "target.segments",
        "target.hotspots",
        "binder.length",
    }
    assert result["checks"] == {"target_mapping": False, "runtime": False}
    assert path.read_bytes() == initial


def test_identifier_and_sequence_do_not_trigger_structure_selection(tmp_path):
    path = tmp_path / "brief.json"
    path.write_text(
        json.dumps(
            {
                "name": "incomplete",
                "target": {"identifier": "user target", "sequence_file": "target.fa"},
                "binder": {"length": {"min": 5, "max": 7}},
            }
        )
    )
    output = tmp_path / "new" / "task.json"
    result = build_task(path, output)
    assert result["task"] is None
    assert result["brief"]["target"]["sequence_file"] == str(tmp_path / "target.fa")
    assert result["execution_settings"]["selected_length"] is None
    assert not output.parent.exists()


def test_build_preserves_scientific_choices_and_relative_path(
    complete_brief, tmp_path, monkeypatch
):
    source, payload, reference = complete_brief
    original = source.read_bytes()
    monkeypatch.chdir(tmp_path.parent)
    output = tmp_path / "elsewhere" / "task.json"
    report = build_task(source, output)
    assert report["status"] == "ready_for_preflight"
    assert report["checks"] == {"target_mapping": True, "runtime": False}
    spec = load_spec(output)
    validate_pipeline_task(spec)
    assert spec.reference_structure == str(reference)
    assert spec.generation.seeds == payload["seeds"]
    assert spec.molecules[0].min_length == spec.molecules[0].max_length == 6
    assert spec.molecules[1].segments[0].start == 20
    assert spec.hotspots[0].residue == 21
    assert spec.description == payload["goal"]
    assert spec.evaluation.metric_rules == []
    assert source.read_bytes() == original


@pytest.mark.parametrize("change", ["range", "hotspots", "cyclic", "unresolved"])
def test_unresolved_or_unsupported_choices_prevent_build(complete_brief, tmp_path, change):
    source, payload, _ = complete_brief
    if change == "range":
        del payload["binder"]["selected_length"]
    elif change == "hotspots":
        payload["target"]["hotspots"] = None
    elif change == "cyclic":
        payload["binder"]["cyclic"] = True
    else:
        payload["unresolved_requirements"] = ["User constraint not yet represented in task fields"]
    source.write_text(json.dumps(payload))
    output = tmp_path / "task.json"
    report = build_task(source, output)
    assert report["status"] == "needs_input"
    assert report["task"] is None
    assert not output.exists()


@pytest.mark.parametrize("change", ["chain", "hotspot", "gap", "duplicate", "overlap", "malformed"])
def test_reference_mapping_errors_prevent_build(complete_brief, tmp_path, change):
    source, payload, reference = complete_brief
    if change == "chain":
        payload["target"]["segments"][0]["chain"] = "Y"
    elif change == "hotspot":
        payload["target"]["hotspots"][0]["residue"] = 19
    elif change == "gap":
        payload["target"]["segments"][0]["start"] = 19
    elif change == "duplicate":
        payload["target"]["hotspots"] *= 2
    elif change == "overlap":
        payload["target"]["segments"] *= 2
    else:
        reference.write_text("not a structure")
    source.write_text(json.dumps(payload))
    output = tmp_path / "task.json"
    report = build_task(source, output)
    assert report["status"] == "needs_input"
    assert report["issues"][0]["category"] == "invalid_mapping"
    assert not output.exists()


def test_fixed_length_needs_no_separate_selection(complete_brief):
    source, _, _ = complete_brief
    brief = load_brief(source)
    brief.binder.length.min = brief.binder.length.max = 5
    brief.binder.selected_length = None
    assert review_brief(brief)["task"]["molecules"][0]["segments"][0]["min_length"] == 5


@pytest.mark.parametrize(
    "extra",
    [
        {"seeds": [True]},
        {"seeds": [1.5]},
        {"seeds": ["42"]},
        {"seeds": [2**31]},
        {"seeds": [1, 1]},
        {"seeds": []},
        {"unknown_field": True},
        {"binder": {"length": {"min": 7, "max": 5}}},
        {"binder": {"length": {"min": 5, "max": 7}, "selected_length": 8}},
    ],
)
def test_rejects_invalid_schema(extra):
    with pytest.raises(ValidationError):
        DesignBrief.model_validate({"name": "invalid", **deepcopy(extra)})


def test_build_refuses_to_replace_existing_file(complete_brief, tmp_path):
    source, _, _ = complete_brief
    output = tmp_path / "task.json"
    output.write_text("existing user task")
    with pytest.raises(FileExistsError):
        build_task(source, output)
    assert output.read_text() == "existing user task"
    before = source.read_bytes()
    with pytest.raises(FileExistsError):
        build_task(source, source)
    assert source.read_bytes() == before


def test_cli_build_ready_and_task_validation(complete_brief, tmp_path, capsys):
    source, _, _ = complete_brief
    output = tmp_path / "task.json"
    assert main(["task", "build", str(source), "--output", str(output)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["task_path"] == str(output)
    assert report["inference"] == "not_run"
    assert main(["validate", str(output)]) == 0

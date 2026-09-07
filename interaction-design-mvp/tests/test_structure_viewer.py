"""Offline viewer content, provenance and source-bundle boundary checks."""

import hashlib
import json
import re
from pathlib import Path

import pytest

from interaction_design.cli import main
from interaction_design.structure_viewer import export_structure_viewer, render_structure_viewer


def write_json(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


def seal(root):
    write_json(
        root / "manifest.json",
        {
            "stage": "screening_export",
            "status": "completed",
            "artifacts": [
                {"path": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in sorted(root.iterdir())
                if p.name != "manifest.json"
            ],
        },
    )


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "reference.pdb").write_text(
        "ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 50.00           C\n"
        "ATOM      2  CA  ALA A   2       3.800   0.000   0.000  1.00 50.00           C\n"
        "END\n"
    )
    (root / "complex.mmcif").write_text(
        "data_synthetic\nloop_\n_atom_site.group_PDB\n_atom_site.id\n"
        "_atom_site.type_symbol\n_atom_site.label_atom_id\n_atom_site.label_comp_id\n"
        "_atom_site.label_asym_id\n_atom_site.label_seq_id\n_atom_site.Cartn_x\n"
        "_atom_site.Cartn_y\n_atom_site.Cartn_z\nATOM 1 C CA ALA A 1 0 0 0\n#\n"
    )
    write_json(root / "task.json", {"name": "synthetic", "reference_structure": "reference.pdb"})
    row = {
        "candidate_id": "candidate-0",
        "seed": 42,
        "length": 2,
        "sequence": "AA",
        "pre": {"monomer_plddt": 50},
        "post": None,
        "complex_evaluation_status": "unevaluated",
        "artifacts": {
            "generated_structure": "complex.mmcif",
            "monomer_structure": "reference.pdb",
            "predicted_complex": None,
        },
    }
    evaluated = {
        **row,
        "candidate_id": "candidate-1",
        "complex_evaluation_status": "evaluated",
        "post": {"iptm": 0.5},
        "artifacts": {**row["artifacts"], "predicted_complex": "complex.mmcif"},
    }
    write_json(root / "candidates.json", {"candidates": [row, evaluated]})
    seal(root)
    return root


def payload(page):
    return json.loads(re.search(r'<script id="structure-data"[^>]*>(.*?)</script>', page, re.S)[1])


def test_offline_page_survives_relocation_and_preserves_original_bytes(bundle, tmp_path):
    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    destination = export_structure_viewer(bundle, tmp_path / "viewer.html")
    page = destination.read_text()
    data = payload(page)
    assert len(data["candidates"]) == 2
    assert len(data["structures"]) == 2  # shared reference is embedded only once
    for name, structure in data["structures"].items():
        assert structure["text"].encode() == before[name]
        assert data["sources"][name] == hashlib.sha256(before[name]).hexdigest()
    assert data["structures"]["complex.mmcif"]["format"] == "cif"
    assert data["candidates"][0]["structures"]["predicted_complex"] is None
    assert data["candidates"][0]["post"] is None
    assert data["candidates"][1]["post"] == {"iptm": 0.5}
    assert data["acceptance"] is None and data["binding_validated"] is False
    assert not re.search(r"<script[^>]+src\s*=|<link[^>]+href\s*=", page, re.I)
    assert "connect-src 'none'" in page
    assert "//# sourceMappingURL=" not in page
    assert "BSD-3-Clause" in page and "3dmol v2.5.5" in page
    assert str(bundle) not in page
    assert {p.name: p.read_bytes() for p in bundle.iterdir()} == before
    moved = tmp_path / "moved.html"
    destination.rename(moved)
    assert payload(moved.read_text()) == data


def test_untrusted_names_and_structure_comments_cannot_escape_html(bundle):
    attack = '</script><script>alert("x")</script>&@@LIBRARY@@'
    write_json(bundle / "task.json", {"name": attack, "reference_structure": "reference.pdb"})
    with (bundle / "reference.pdb").open("a") as handle:
        handle.write("REMARK " + attack)
    page = render_structure_viewer(bundle)
    assert attack not in page
    assert payload(page)["name"] == attack
    assert payload(page)["structures"]["reference.pdb"]["text"].endswith(attack)


@pytest.mark.parametrize("field", ["post", "predicted_complex"])
def test_unevaluated_candidate_cannot_expose_complex_result(bundle, field):
    data = json.loads((bundle / "candidates.json").read_text())
    row = data["candidates"][0]
    if field == "post":
        row["post"] = {"iptm": 0.99}
    else:
        row["artifacts"][field] = "complex.mmcif"
    write_json(bundle / "candidates.json", data)
    with pytest.raises(ValueError, match="unevaluated"):
        render_structure_viewer(bundle)


@pytest.mark.parametrize("name", ["reference.pdb", "task.json", "candidates.json"])
def test_existing_bundle_tamper_or_missing_file_does_not_publish(bundle, tmp_path, name):
    (bundle / name).write_text("tampered")
    destination = tmp_path / "bad.html"
    with pytest.raises(ValueError, match="checksum"):
        export_structure_viewer(bundle, destination)
    assert not destination.exists()
    (bundle / name).unlink()
    with pytest.raises(ValueError, match="checksum"):
        export_structure_viewer(bundle, destination)


@pytest.mark.parametrize("escape", ["../outside.pdb", "absolute", "symlink"])
def test_references_cannot_embed_files_outside_bundle(bundle, tmp_path, escape):
    outside = tmp_path / "outside.pdb"
    outside.write_text("not part of this export")
    if escape == "absolute":
        escape = str(outside)
    elif escape == "symlink":
        (bundle / "linked.pdb").symlink_to(outside)
        escape = "linked.pdb"
    write_json(bundle / "task.json", {"reference_structure": escape})
    with pytest.raises(ValueError, match="relative|escapes"):
        render_structure_viewer(bundle)


def test_refuse_overwrite_or_mutating_original_bundle(bundle, tmp_path):
    destination = tmp_path / "existing.html"
    destination.write_text("keep")
    with pytest.raises(FileExistsError):
        export_structure_viewer(bundle, destination)
    assert destination.read_text() == "keep"
    with pytest.raises(ValueError, match="outside"):
        export_structure_viewer(bundle, bundle / "index.html")


def test_reject_unrecorded_structure(bundle, tmp_path):
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["artifacts"] = [p for p in manifest["artifacts"] if p["path"] != "complex.mmcif"]
    write_json(bundle / "manifest.json", manifest)
    with pytest.raises(ValueError, match="checksum"):
        export_structure_viewer(bundle, tmp_path / "bad.html")


def test_cli_generates_standalone_file_and_returns_its_path(bundle, tmp_path, capsys):
    destination = tmp_path / "cli.html"
    assert main(["viewer", "export", str(bundle), "--output", str(destination)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert Path(result["page"]) == destination
    assert result["inference"] == "not_run"
    assert payload(destination.read_text())["name"] == "synthetic"

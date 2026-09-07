"""Synthetic, dependency-free checks for private sequential development import."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_adaptive_development.py"
SPEC = importlib.util.spec_from_file_location("import_adaptive_development", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)


def summary(uuid="synthetic", target="PD-L1", label=False, **extras):
    return {"uuid": uuid, "target": target, "binder_final": label, **extras}


def predictions(uuid="synthetic", target="PD-L1"):
    return [
        {
            "uuid": uuid,
            "target": target,
            "cofolding_model": "ef2full",
            "stoichiometry": "1to1",
            "target_form": "",
            "seed": str(seed),
            "iptm_pae": value,
            "ipsae_min": 1 - value,
            "sc_dockq": value / 2,
        }
        for seed, value in enumerate([0.1, 0.8, 0.2, 0.9, 0.3])
    ]


def candidate(result):
    return result["pools"][0]["candidates"][0]


def test_individual_seeds_are_preserved_without_aggregation_or_sequence_columns():
    result = importer.normalize(
        [summary(sequence="never emit sequence", full_name="hidden")], predictions()
    )
    assert result["schema_version"] == "molclaw-adaptive-input-v1"
    assert result["purpose"] == "development"
    assert result["pools"][0]["pool_id"] == "p05"
    row = candidate(result)
    assert set(row) == {"candidate_id", "source_id", "label", "predictions"}
    assert row["candidate_id"] == "c_" + hashlib.sha256(b"synthetic").hexdigest()[:16]
    assert row["source_id"] == "synthetic"
    assert row["label"] is False
    assert row["predictions"] == [
        {"seed": seed, "iptm": value, "ipsae": 1 - value, "sc_dockq": value / 2}
        for seed, value in enumerate([0.1, 0.8, 0.2, 0.9, 0.3])
    ]
    assert "never emit sequence" not in json.dumps(result)
    assert "full_name" not in json.dumps(result)


@pytest.mark.parametrize("label", [None, 0, 1, "False", float("nan"), [], {}])
def test_unknown_or_nonboolean_label_is_rejected(label):
    with pytest.raises(ValueError, match="known boolean"):
        importer.normalize([summary(label=label)], predictions())


@pytest.mark.parametrize("label", [False, True])
def test_labels_are_exact_source_composite_booleans(label):
    result = importer.normalize(
        [summary(label=label, adaptyv_binding="not_measured")], predictions()
    )
    assert candidate(result)["label"] is label


def test_missing_label_is_rejected():
    row = summary()
    del row["binder_final"]
    with pytest.raises(ValueError, match="known boolean"):
        importer.normalize([row], predictions())


@pytest.mark.parametrize("missing", [None, float("nan")])
@pytest.mark.parametrize(("source", "field"), importer.SCORE_FIELDS.items())
def test_missing_cell_only_affects_one_seed_and_one_field(missing, source, field):
    rows = predictions()
    rows[2][field] = missing
    result = candidate(importer.normalize([summary()], rows))
    assert result["predictions"][2][source] is None
    assert sum(value is None for row in result["predictions"] for value in row.values()) == 1
    json.dumps(result, allow_nan=False)


def test_absent_score_cell_is_null():
    rows = predictions()
    del rows[4]["sc_dockq"]
    result = candidate(importer.normalize([summary()], rows))
    assert result["predictions"][4]["sc_dockq"] is None


@pytest.mark.parametrize(
    "value", [True, False, "0.5", [], {}, -0.01, 1.01, 10**1000, float("inf"), -float("inf")]
)
def test_invalid_scores_are_rejected(value):
    rows = predictions()
    rows[0]["iptm_pae"] = value
    with pytest.raises(ValueError, match="finite numbers"):
        importer.normalize([summary()], rows)


@pytest.mark.parametrize("remove_count", [1, 5])
def test_missing_seed_rows_fail_even_if_missing_cells_are_allowed(remove_count):
    with pytest.raises(ValueError, match="exactly seeds"):
        importer.normalize([summary()], predictions()[remove_count:])


@pytest.mark.parametrize("seed", [True, False, 0.0, -1, 5, "00", "5", "0.0", None])
def test_invalid_seeds_fail(seed):
    rows = predictions()
    rows[0]["seed"] = seed
    with pytest.raises(ValueError, match="seed must be"):
        importer.normalize([summary()], rows)


def test_canonical_integer_seeds_and_duplicate_normalized_seed():
    rows = predictions()
    rows[0]["seed"] = 0
    assert candidate(importer.normalize([summary()], rows))["predictions"][0]["seed"] == 0
    with pytest.raises(ValueError, match="duplicate prediction"):
        importer.normalize([summary()], rows + [{**rows[0], "seed": "0"}])


def test_duplicate_summary_across_targets_fails():
    with pytest.raises(ValueError, match="duplicate summary"):
        importer.normalize([summary(), summary(target="EGFR")], predictions())


def test_prediction_without_summary_fails():
    with pytest.raises(ValueError, match="no included summary"):
        importer.normalize([summary()], predictions() + predictions("orphan"))


def test_target_mismatch_fails():
    with pytest.raises(ValueError, match="does not match"):
        importer.normalize([summary()], predictions(target="EGFR"))


def test_candidate_hash_collision_fails(monkeypatch):
    monkeypatch.setattr(importer, "_candidate_id", lambda source: "c_collision")
    with pytest.raises(ValueError, match="hash collision"):
        importer.normalize([summary(), summary("other")], [])


@pytest.mark.parametrize("row", [None, [], 1, {"target": None}, {"target": ""}])
def test_invalid_rows_and_targets_fail(row):
    with pytest.raises(ValueError):
        importer.normalize([row], [])


def test_out_of_scope_targets_are_skipped_without_other_field_access():
    class TargetOnly(dict):
        def get(self, key, default=None):
            assert key == "target"
            return super().get(key, default)

    excluded = TargetOnly(target="not-development", sequence="must not inspect")
    result = importer.normalize([summary(), excluded], predictions() + [excluded])
    assert len(result["pools"]) == 1


def test_other_models_stoichiometries_and_forms_are_not_included():
    excluded = [
        {"target": "PD-L1", "cofolding_model": "ef2fast"},
        {**predictions()[0], "stoichiometry": "2to1", "seed": "invalid"},
        {**predictions()[0], "target_form": "alternate", "seed": "invalid"},
    ]
    assert importer.normalize([summary()], predictions() + excluded) == importer.normalize(
        [summary()], predictions()
    )


def test_output_is_deterministic_and_input_is_unchanged():
    summaries = [summary("a"), summary("b", target="EGFR"), summary("c", target="EGFR")]
    rows = [row for item in summaries for row in predictions(item["uuid"], item["target"])]
    original = json.dumps([summaries, rows], sort_keys=True)
    result = importer.normalize(summaries, rows)
    assert importer.normalize(list(reversed(summaries)), list(reversed(rows))) == result
    assert [pool["target"] for pool in result["pools"]] == ["EGFR", "PD-L1"]
    assert [pool["pool_id"] for pool in result["pools"]] == ["p02", "p05"]
    assert json.dumps([summaries, rows], sort_keys=True) == original


def test_snapshot_checks_revision_before_optional_dependency_import(tmp_path):
    with pytest.raises(ValueError, match="pinned revision"):
        importer.import_snapshot(tmp_path)


def fake_snapshot(tmp_path, monkeypatch, *, complete=True, missing_column=False):
    snapshot = tmp_path / importer.REVISION
    hashes = {}
    for path in importer.PINNED_HASHES:
        file_path = snapshot / path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(path.encode())
        hashes[path] = hashlib.sha256(path.encode()).hexdigest()
    monkeypatch.setattr(importer, "PINNED_HASHES", hashes)
    summaries = [
        summary(f"{target}-{index}", target=target, label=bool(index % 2))
        for target in importer.TARGETS
        for index in range(90 if complete else 1)
    ]
    rows = [row for item in summaries for row in predictions(item["uuid"], item["target"])]
    reads = []

    def read_schema(path):
        columns = (
            importer.SUMMARY_COLUMNS
            if path.name == "design_summary.parquet"
            else importer.PREDICTION_COLUMNS
        )
        return SimpleNamespace(names=list(columns[1:] if missing_column else columns))

    def read_table(path, *, columns, filters):
        reads.append({"name": path.name, "columns": columns, "filters": filters})
        data = summaries if path.name == "design_summary.parquet" else rows
        return SimpleNamespace(to_pylist=lambda: data)

    pyarrow = ModuleType("pyarrow")
    pyarrow.__version__ = "synthetic-test"
    parquet = ModuleType("pyarrow.parquet")
    parquet.read_schema = read_schema
    parquet.read_table = read_table
    pyarrow.parquet = parquet
    monkeypatch.setitem(sys.modules, "pyarrow", pyarrow)
    monkeypatch.setitem(sys.modules, "pyarrow.parquet", parquet)
    return snapshot, reads


def test_parquet_projection_and_filters_do_not_load_other_targets_or_sequences(
    tmp_path, monkeypatch
):
    snapshot, reads = fake_snapshot(tmp_path, monkeypatch)
    result = importer.import_snapshot(snapshot)
    assert len(reads) == 2
    assert reads[0]["columns"] == ["uuid", "target", "binder_final"]
    assert reads[0]["filters"] == [("target", "in", list(importer.TARGETS))]
    assert reads[1]["columns"] == list(importer.PREDICTION_COLUMNS)
    assert reads[1]["filters"] == reads[0]["filters"] + [
        ("cofolding_model", "=", "ef2full"),
        ("stoichiometry", "=", "1to1"),
        ("target_form", "=", ""),
    ]
    assert len(result["pools"]) == 7
    assert all(len(pool["candidates"]) == 90 for pool in result["pools"])
    assert result["source"]["dataset"] == importer.REPO
    assert result["source"]["revision"] == importer.REVISION
    assert result["source"]["license"] == "CC-BY-4.0"
    assert len(result["source"]["files"]) == 2


def test_pinned_source_hash_mismatch_fails_before_reading_rows(tmp_path, monkeypatch):
    snapshot, reads = fake_snapshot(tmp_path, monkeypatch)
    (snapshot / importer.SUMMARY_PATH).write_bytes(b"changed")
    with pytest.raises(ValueError, match="pinned source hash mismatch"):
        importer.import_snapshot(snapshot)
    assert reads == []


def test_real_snapshot_contract_requires_630_candidates(tmp_path, monkeypatch):
    snapshot, _ = fake_snapshot(tmp_path, monkeypatch, complete=False)
    with pytest.raises(ValueError, match="7 targets with 90 candidates"):
        importer.import_snapshot(snapshot)


def test_required_parquet_columns_are_checked_before_read(tmp_path, monkeypatch):
    snapshot, reads = fake_snapshot(tmp_path, monkeypatch, missing_column=True)
    with pytest.raises(ValueError, match="required columns"):
        importer.import_snapshot(snapshot)
    assert reads == []


def test_audit_includes_only_structure_missingness_and_hashes():
    rows = predictions()
    rows[2]["sc_dockq"] = None
    rows[3]["sc_dockq"] = None
    document = importer.normalize([summary(label=True)], rows)
    document["source"] = {"files": [{"path": "source", "sha256": "synthetic", "size_bytes": 1}]}
    result = importer.audit(document, output_record={"path": "private.json", "sha256": "synthetic"})
    assert result["candidate_count"] == 1
    assert result["prediction_count"] == 5
    assert result["missing_score_cells"] == {"iptm": 0, "ipsae": 0, "sc_dockq": 2}
    assert result["candidates_with_missing_score"] == {"iptm": 0, "ipsae": 0, "sc_dockq": 1}
    assert result["seed_counts"] == {str(seed): 1 for seed in range(5)}
    serialized = json.dumps(result)
    assert "label" not in serialized
    assert "positive" not in serialized
    assert "binder_final" not in serialized
    assert "source_id" not in serialized


def test_cli_writes_private_input_and_label_free_audit_once(tmp_path, monkeypatch, capsys):
    snapshot, _ = fake_snapshot(tmp_path, monkeypatch)
    output, audit = tmp_path / "new" / "private.json", tmp_path / "new" / "audit.json"
    args = ["--snapshot", str(snapshot), "--output", str(output), "--audit", str(audit)]
    assert importer.main(args) == 0
    data = json.loads(output.read_text())
    audit_data = json.loads(audit.read_text())
    assert data["schema_version"] == importer.SCHEMA_VERSION
    assert audit_data["candidate_count"] == 630
    assert audit_data["output"]["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert (
        capsys.readouterr().out == "Imported 7 development pools, 630 candidates; audit created.\n"
    )
    original = (output.read_bytes(), audit.read_bytes())
    with pytest.raises(SystemExit) as exc:
        importer.main(args)
    assert exc.value.code == 2
    assert (output.read_bytes(), audit.read_bytes()) == original
    assert "already exists" in capsys.readouterr().err


@pytest.mark.parametrize("existing", ["output", "audit"])
def test_existing_output_or_dangling_symlink_is_not_overwritten(tmp_path, monkeypatch, existing):
    output, audit = tmp_path / "private.json", tmp_path / "audit.json"
    selected = output if existing == "output" else audit
    selected.symlink_to(tmp_path / "missing")

    def unexpected_read(snapshot):
        pytest.fail("existing output must be checked before reading snapshot")

    monkeypatch.setattr(importer, "import_snapshot", unexpected_read)
    with pytest.raises(SystemExit) as exc:
        importer.main(["--snapshot", str(tmp_path), "--output", str(output), "--audit", str(audit)])
    assert exc.value.code == 2
    assert selected.is_symlink()
    assert not (tmp_path / "missing").exists()


def test_same_output_and_audit_path_is_rejected(tmp_path):
    output = tmp_path / "result.json"
    with pytest.raises(SystemExit) as exc:
        importer.main(
            ["--snapshot", str(tmp_path), "--output", str(output), "--audit", str(output)]
        )
    assert exc.value.code == 2
    assert not output.exists()

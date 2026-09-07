"""Synthetic checks for the standalone, standard-library benchmark normalizer."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_anthropic_benchmark.py"
SPEC = importlib.util.spec_from_file_location("import_anthropic_benchmark", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)


def summary(uuid="synthetic", target="PD-L1", label=False, **overrides):
    return {
        "uuid": uuid,
        "target": target,
        "binder_final": label,
        "adaptyv_binding": "non_binder",
        "twist_binding": "not_tested",
        **overrides,
    }


def predictions(uuid="synthetic", target="PD-L1"):
    values = {
        "ef2full": [0.1, 0.8, 0.2, 0.9, 0.3],
        "ef2fast": [0.9, 0.7, 0.6, 0.8, 1.0],
        "ptxv2": [0.4, 0.6, 0.5, 0.3, 0.7],
    }
    return [
        {
            "uuid": uuid,
            "target": target,
            "cofolding_model": model,
            "stoichiometry": "1to1",
            "target_form": "",
            "seed": str(seed),
            "iptm_pae": 1 - value,
            "ipsae_min": value,
            "sc_dockq": value / 2,
        }
        for model, scores in values.items()
        for seed, value in enumerate(scores)
    ]


def test_complete_seed_medians_and_exact_projection():
    raw = summary(sequence="must not escape", full_name="hidden", rank=1)
    result = importer.normalize([raw], predictions())
    row = result["rows"][0]
    assert set(row) == {"uuid", "target", "label", "adaptyv_binding", "twist_binding", "scores"}
    assert row["scores"] == pytest.approx(
        {"ef2_iptm": 0.7, "ef2_ipsae": 0.3, "ef2_sc_dockq": 0.15, "ensemble_ipsae": 0.5}
    )
    assert set(row["scores"]) == set(importer.SCORE_KEYS)
    assert "must not escape" not in json.dumps(result)
    assert result["qc"]["included_prediction_rows"] == 15
    assert result["qc"]["target_counts"] == {"PD-L1": 1}
    assert "positive" not in json.dumps(result["qc"])


def test_preserves_negative_labels_and_unknown_labels_without_vendor_recode():
    source = [
        summary("negative", label=False, adaptyv_binding="not_tested"),
        summary("positive", label=True),
        summary("unknown", label=None, adaptyv_binding=None, twist_binding="not_tested"),
    ]
    result = importer.normalize(source, sum((predictions(row["uuid"]) for row in source), []))
    rows = {row["uuid"]: row for row in result["rows"]}
    assert rows["negative"]["label"] is False
    assert rows["positive"]["label"] is True
    assert rows["unknown"]["label"] is None
    assert rows["unknown"]["adaptyv_binding"] is None
    assert rows["unknown"]["twist_binding"] == "not_tested"
    assert result["qc"]["missing_label_rows"] == 1


@pytest.mark.parametrize("label", [0, 1, "False", "True", float("nan"), [], {}])
def test_nonboolean_labels_are_rejected(label):
    with pytest.raises(ValueError, match="boolean or null"):
        importer.normalize([summary(label=label)], predictions())


@pytest.mark.parametrize("missing", [None, float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize(
    ("field", "affected"),
    [
        ("iptm_pae", {"ef2_iptm"}),
        ("ipsae_min", {"ef2_ipsae", "ensemble_ipsae"}),
        ("sc_dockq", {"ef2_sc_dockq"}),
    ],
)
def test_one_missing_seed_score_invalidates_only_its_complete_medians(missing, field, affected):
    source = predictions()
    source[0][field] = missing
    result = importer.normalize([summary()], source)
    scores = result["rows"][0]["scores"]
    assert {name for name, value in scores.items() if value is None} == affected
    assert {name for name, value in result["qc"]["missing_score_rows"].items() if value} == affected
    json.dumps(result, allow_nan=False)


def test_absent_score_cell_and_other_model_missing_score_remain_unknown():
    source = predictions()
    del source[5]["ipsae_min"]
    scores = importer.normalize([summary()], source)["rows"][0]["scores"]
    assert scores["ensemble_ipsae"] is None
    assert scores["ef2_ipsae"] == pytest.approx(0.3)


@pytest.mark.parametrize("value", [True, False, "0.5", [], {}, -0.01, 1.01, 10**1000])
def test_malformed_or_out_of_range_scores_are_rejected(value):
    source = predictions()
    source[0]["sc_dockq"] = value
    with pytest.raises(ValueError, match="excluding booleans"):
        importer.normalize([summary()], source)


@pytest.mark.parametrize("remove_count", [1, 5, 15])
def test_missing_seed_or_whole_model_is_rejected(remove_count):
    with pytest.raises(ValueError, match="exactly seeds"):
        importer.normalize([summary()], predictions()[remove_count:])


@pytest.mark.parametrize("seed", [True, False, 0.0, -1, 5, "00", "5", "0.0", None])
def test_noncanonical_or_unexpected_seed_is_rejected(seed):
    source = predictions()
    source[0]["seed"] = seed
    with pytest.raises(ValueError, match="seed must be"):
        importer.normalize([summary()], source)


def test_integer_seeds_are_supported_and_duplicate_string_integer_seed_is_rejected():
    source = predictions()
    source[0]["seed"] = 0
    assert len(importer.normalize([summary()], source)["rows"]) == 1
    with pytest.raises(ValueError, match="duplicate prediction"):
        importer.normalize([summary()], source + [{**source[0], "seed": "0"}])


def test_duplicate_summary_is_rejected_across_targets():
    with pytest.raises(ValueError, match="duplicate summary uuid"):
        importer.normalize([summary(), summary(target="EGFR")], predictions())


def test_prediction_without_summary_is_rejected():
    with pytest.raises(ValueError, match="no included summary"):
        importer.normalize([summary()], predictions() + predictions(uuid="orphan"))


def test_cross_target_join_is_rejected():
    with pytest.raises(ValueError, match="does not match"):
        importer.normalize([summary()], predictions(target="EGFR"))


@pytest.mark.parametrize("target", [None, "", [], 7])
def test_invalid_target_is_rejected(target):
    with pytest.raises(ValueError, match="target must be"):
        importer.normalize([summary(target=target)], [])


def test_out_of_scope_rows_are_skipped_before_other_field_access():
    class TargetOnly(dict):
        def get(self, key, default=None):
            assert key == "target", "out-of-scope fields must not be inspected"
            return super().get(key, default)

    out_of_scope = TargetOnly(target="out-of-scope", sequence=object())
    result = importer.normalize([summary(), out_of_scope], predictions() + [out_of_scope])
    assert len(result["rows"]) == 1
    assert result["qc"]["excluded_summary_rows"] == 1
    assert result["qc"]["excluded_prediction_rows"] == 1


def test_prediction_filters_exclude_other_models_stoichiometries_and_target_forms():
    excluded = [
        {"target": "PD-L1", "cofolding_model": "other"},
        {**predictions()[0], "stoichiometry": "2to1", "seed": "invalid"},
        {**predictions()[0], "target_form": "other", "seed": "invalid"},
    ]
    result = importer.normalize([summary()], predictions() + excluded)
    assert result["qc"]["excluded_prediction_rows"] == 3


def test_normalization_is_deterministic_and_does_not_mutate_inputs():
    summaries = [summary("b"), summary("a", target="EGFR")]
    source = predictions("b") + predictions("a", target="EGFR")
    before = json.dumps([summaries, source], sort_keys=True)
    expected = importer.normalize(summaries, source)
    assert importer.normalize(list(reversed(summaries)), list(reversed(source))) == expected
    assert [row["uuid"] for row in expected["rows"]] == ["a", "b"]
    assert json.dumps([summaries, source], sort_keys=True) == before


def test_snapshot_requires_exact_revision_before_importing_pyarrow(tmp_path):
    with pytest.raises(ValueError, match="pinned revision"):
        importer.import_snapshot(tmp_path)


def test_cli_creates_fresh_strict_json_and_does_not_print_labels(tmp_path, monkeypatch, capsys):
    document = {"version": importer.VERSION, **importer.normalize([summary()], predictions())}
    monkeypatch.setattr(importer, "import_snapshot", lambda snapshot: document)
    output = tmp_path / "new" / "import.json"
    args = ["--snapshot", str(tmp_path), "--output", str(output)]
    assert importer.main(args) == 0
    assert json.loads(output.read_text()) == document
    assert capsys.readouterr().out == "Imported 1 rows; output created successfully.\n"
    original = output.read_bytes()
    with pytest.raises(SystemExit) as exc:
        importer.main(args)
    assert exc.value.code == 2
    assert output.read_bytes() == original
    assert "output already exists" in capsys.readouterr().err


def test_file_metadata_has_exact_sha256_and_size(tmp_path):
    import hashlib

    (tmp_path / "fixture.parquet").write_bytes(b"synthetic parquet fixture")
    result = importer._file_metadata(tmp_path, "fixture.parquet")
    assert result == {
        "path": "fixture.parquet",
        "sha256": hashlib.sha256(b"synthetic parquet fixture").hexdigest(),
        "size_bytes": len(b"synthetic parquet fixture"),
    }

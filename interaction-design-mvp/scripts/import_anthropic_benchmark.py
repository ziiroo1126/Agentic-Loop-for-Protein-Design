#!/usr/bin/env python3
"""Import a pinned local benchmark snapshot without loading sequence columns.

Python 3.10+; only the command-line import requires pyarrow. The pure
``normalize`` function uses the standard library. This script has no network
operations and refuses to overwrite its output file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

VERSION = "anthropic-binder-import-v1"
REPO = "Anthropic/claude-protein-binder-design"
REVISION = "9e1b81696da46835e9e9cde9a3da976e0abc92ab"
TARGETS = frozenset({"PD-L1", "EGFR", "IL-7Ra", "TREM2", "TrkA", "BBF-14", "MBP"})
MODELS = ("ef2full", "ef2fast", "ptxv2")
SEEDS = frozenset(range(5))
SCORE_KEYS = ("ef2_iptm", "ef2_ipsae", "ef2_sc_dockq", "ensemble_ipsae")
SUMMARY_PATH = "data/tables/design_summary.parquet"
PREDICTIONS_PATH = "data/tables/insilico/cofold_predictions.parquet"
SUMMARY_COLUMNS = ("uuid", "target", "binder_final", "adaptyv_binding", "twist_binding")
PREDICTION_COLUMNS = (
    "uuid",
    "target",
    "cofolding_model",
    "stoichiometry",
    "target_form",
    "seed",
    "iptm_pae",
    "ipsae_min",
    "sc_dockq",
)


def _string(row: dict, key: str, *, allow_empty: bool = False) -> str:
    value = row.get(key)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError(f"{key} must be a {'possibly empty ' if allow_empty else ''}string")
    return value


def _target(row: dict) -> str:
    if not isinstance(row, dict):
        raise ValueError("each input row must be a dictionary")
    return _string(row, "target")


def _score(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("scores must be numeric values in [0, 1], excluding booleans")
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if not 0 <= value <= 1:
        raise ValueError("scores must be numeric values in [0, 1], excluding booleans")
    return float(value)


def _seed(value: Any) -> int:
    # The pinned parquet schema stores seeds as strings. Accept integers for
    # callers of the pure API, but never coerce booleans, floats, or "00".
    if isinstance(value, str) and value in {"0", "1", "2", "3", "4"}:
        return int(value)
    if type(value) is int and value in SEEDS:
        return value
    raise ValueError("seed must be an integer 0..4 or its canonical string")


def _complete_median(values: list[float | None]) -> float | None:
    if any(value is None for value in values):
        return None
    return float(median(values))


def normalize(summaries: list[dict], predictions: list[dict]) -> dict:
    """Validate joins and seed coverage, then return sequence-free rows and QC.

    Only allowlisted targets, the three specified models, 1:1 stoichiometry,
    and the empty target form are included. Every included summary requires
    all three models and all five seeds. Null/nonfinite score cells propagate
    to null medians; malformed numeric values and incomplete records fail.
    The source composite label is preserved exactly, including False and None.
    Vendor outcomes are retained verbatim and never used to recode that label.
    """
    by_uuid: dict[str, dict] = {}
    for summary in summaries:
        target = _target(summary)
        if target not in TARGETS:
            continue
        uuid = _string(summary, "uuid")
        if uuid in by_uuid:
            raise ValueError("duplicate summary uuid")
        if "binder_final" not in summary:
            raise ValueError("summary is missing binder_final")
        label = summary["binder_final"]
        if label is not None and type(label) is not bool:
            raise ValueError("binder_final must be a boolean or null")
        vendor_fields = {}
        for name in ("adaptyv_binding", "twist_binding"):
            if name not in summary:
                raise ValueError(f"summary is missing {name}")
            value = summary[name]
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or null")
            vendor_fields[name] = value
        by_uuid[uuid] = {"uuid": uuid, "target": target, "label": label, **vendor_fields}

    groups: dict[tuple[str, str], dict[int, dict]] = {}
    included_predictions = 0
    for prediction in predictions:
        target = _target(prediction)
        if target not in TARGETS:
            continue
        model = _string(prediction, "cofolding_model")
        if model not in MODELS:
            continue
        stoichiometry = _string(prediction, "stoichiometry")
        target_form = _string(prediction, "target_form", allow_empty=True)
        if stoichiometry != "1to1" or target_form != "":
            continue
        uuid = _string(prediction, "uuid")
        if uuid not in by_uuid:
            raise ValueError("prediction uuid has no included summary")
        if by_uuid[uuid]["target"] != target:
            raise ValueError("prediction target does not match its summary target")
        seed = _seed(prediction.get("seed"))
        group = groups.setdefault((uuid, model), {})
        if seed in group:
            raise ValueError("duplicate prediction (uuid, model, seed)")
        group[seed] = {
            name: _score(prediction.get(name)) for name in ("iptm_pae", "ipsae_min", "sc_dockq")
        }
        included_predictions += 1

    rows = []
    for uuid, summary in sorted(by_uuid.items()):
        model_medians = {}
        for model in MODELS:
            group = groups.get((uuid, model), {})
            if set(group) != SEEDS:
                raise ValueError("every included model requires exactly seeds {0, 1, 2, 3, 4}")
            model_medians[model] = {
                name: _complete_median([group[seed][name] for seed in sorted(SEEDS)])
                for name in ("iptm_pae", "ipsae_min", "sc_dockq")
            }
        ef2 = model_medians["ef2full"]
        scores = {
            "ef2_iptm": ef2["iptm_pae"],
            "ef2_ipsae": ef2["ipsae_min"],
            "ef2_sc_dockq": ef2["sc_dockq"],
            "ensemble_ipsae": _complete_median(
                [model_medians[model]["ipsae_min"] for model in MODELS]
            ),
        }
        rows.append({**summary, "scores": scores})

    if included_predictions != len(rows) * len(MODELS) * len(SEEDS):
        raise ValueError("included prediction count does not match complete model/seed coverage")
    qc = {
        "input_summary_rows": len(summaries),
        "input_prediction_rows": len(predictions),
        "included_summary_rows": len(rows),
        "included_prediction_rows": included_predictions,
        "excluded_summary_rows": len(summaries) - len(rows),
        "excluded_prediction_rows": len(predictions) - included_predictions,
        "target_counts": dict(sorted(Counter(row["target"] for row in rows).items())),
        "prediction_model_counts": {model: len(rows) * len(SEEDS) for model in MODELS},
        "missing_label_rows": sum(row["label"] is None for row in rows),
        "missing_score_rows": {
            name: sum(row["scores"][name] is None for row in rows) for name in SCORE_KEYS
        },
    }
    return {"rows": rows, "qc": qc}


def _file_metadata(snapshot: Path, relative_path: str) -> dict:
    path = snapshot / relative_path
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": relative_path, "sha256": digest.hexdigest(), "size_bytes": size}


def import_snapshot(snapshot: Path) -> dict:
    """Read only the permitted columns and targets from a pinned cache directory."""
    snapshot = Path(snapshot).resolve()
    if snapshot.name != REVISION:
        raise ValueError(f"snapshot directory must be named for pinned revision {REVISION}")
    # Keep the normalizer importable in the main project environment without
    # installing or changing any dependencies there.
    import pyarrow
    import pyarrow.parquet as pq

    for relative_path, columns in (
        (SUMMARY_PATH, SUMMARY_COLUMNS),
        (PREDICTIONS_PATH, PREDICTION_COLUMNS),
    ):
        schema = pq.read_schema(snapshot / relative_path)
        if not set(columns).issubset(schema.names):
            raise ValueError(f"required columns are missing from {relative_path}")

    target_filter = [("target", "in", sorted(TARGETS))]
    summaries = pq.read_table(
        snapshot / SUMMARY_PATH, columns=list(SUMMARY_COLUMNS), filters=target_filter
    ).to_pylist()
    predictions = pq.read_table(
        snapshot / PREDICTIONS_PATH,
        columns=list(PREDICTION_COLUMNS),
        filters=target_filter
        + [
            ("cofolding_model", "in", list(MODELS)),
            ("stoichiometry", "=", "1to1"),
            ("target_form", "=", ""),
        ],
    ).to_pylist()
    normalized = normalize(summaries, predictions)
    normalized["qc"].update(
        source_summary_rows=pq.ParquetFile(snapshot / SUMMARY_PATH).metadata.num_rows,
        source_prediction_rows=pq.ParquetFile(snapshot / PREDICTIONS_PATH).metadata.num_rows,
    )
    return {
        "version": VERSION,
        "source": {
            "repo": REPO,
            "revision": REVISION,
            "pyarrow_version": pyarrow.__version__,
            "files": [_file_metadata(snapshot, path) for path in (SUMMARY_PATH, PREDICTIONS_PATH)],
        },
        **normalized,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot", required=True, type=Path, help="Pinned local snapshot directory"
    )
    parser.add_argument("--output", required=True, type=Path, help="New JSON output file")
    args = parser.parse_args(argv)
    try:
        if args.output.exists() or args.output.is_symlink():
            raise FileExistsError("output already exists; choose a fresh file")
        document = import_snapshot(args.snapshot)
        serialized = json.dumps(document, allow_nan=False, sort_keys=True, indent=2) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8") as handle:
            handle.write(serialized)
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(2, f"Import failed: {exc}\n")
    # Only structural counts may be printed: never labels, scores, or sequences.
    print(f"Imported {len(document['rows'])} rows; output created successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

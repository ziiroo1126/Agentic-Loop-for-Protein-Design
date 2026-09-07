#!/usr/bin/env python3
"""Import cached, already exposed development data for sequential EF2 replay.

This script reads no sequence columns, makes no network calls, and never runs
structure prediction. Its output is private executor input: it contains future
seed scores and known experimental labels and must not be exposed to a policy.
Python 3.10+; pyarrow is required only for the local snapshot import.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "molclaw-adaptive-input-v1"
REPO = "Anthropic/claude-protein-binder-design"
REVISION = "9e1b81696da46835e9e9cde9a3da976e0abc92ab"
TARGETS = ("BBF-14", "EGFR", "IL-7Ra", "MBP", "PD-L1", "TREM2", "TrkA")
SEEDS = frozenset(range(5))
SUMMARY_PATH = "data/tables/design_summary.parquet"
PREDICTIONS_PATH = "data/tables/insilico/cofold_predictions.parquet"
PINNED_HASHES = {
    SUMMARY_PATH: "3e796869b8dc0c90d7ab0e12daf25308c0cf272d04c6a5e27281f903ecd18744",
    PREDICTIONS_PATH: "ba48010610daa9b9d184aa1ddb419beebdf728ccf745f5a8f3b48e4041e1a87d",
}
SUMMARY_COLUMNS = ("uuid", "target", "binder_final")
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
SCORE_FIELDS = {"iptm": "iptm_pae", "ipsae": "ipsae_min", "sc_dockq": "sc_dockq"}


def _string(row: dict, key: str, *, allow_empty: bool = False) -> str:
    if not isinstance(row, dict):
        raise ValueError("each input row must be a dictionary")
    value = row.get(key)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise ValueError(f"{key} must be a {'possibly empty ' if allow_empty else ''}string")
    return value


def _score(value: Any) -> float | None:
    if value is None or (type(value) is float and math.isnan(value)):
        return None
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ValueError("scores must be finite numbers in [0, 1], excluding booleans")
    return float(value)


def _seed(value: Any) -> int:
    if isinstance(value, str) and value in {"0", "1", "2", "3", "4"}:
        return int(value)
    if type(value) is int and value in SEEDS:
        return value
    raise ValueError("seed must be an integer 0..4 or its canonical string")


def _candidate_id(source_id: str) -> str:
    return "c_" + hashlib.sha256(source_id.encode("utf-8")).hexdigest()[:16]


def normalize(summaries: list[dict], predictions: list[dict]) -> dict:
    """Normalize only allowlisted data, requiring every included candidate's seeds.

    Input subsets are supported for synthetic validation. The snapshot importer
    additionally requires exactly the previously exposed 90 candidates per target.
    None, NaN, and absent score cells become null; malformed values are rejected.
    Labels preserve the source composite boolean and are never recoded.
    """
    by_source: dict[str, dict] = {}
    candidate_ids: set[str] = set()
    for row in summaries:
        target = _string(row, "target")
        if target not in TARGETS:
            continue
        source_id = _string(row, "uuid")
        if source_id in by_source:
            raise ValueError("duplicate summary uuid")
        if type(row.get("binder_final")) is not bool:
            raise ValueError("binder_final must be a known boolean")
        candidate_id = _candidate_id(source_id)
        if candidate_id in candidate_ids:
            raise ValueError("candidate_id hash collision")
        candidate_ids.add(candidate_id)
        by_source[source_id] = {
            "target": target,
            "source_id": source_id,
            "candidate_id": candidate_id,
            "label": row["binder_final"],
        }

    groups: dict[str, dict[int, dict]] = {}
    for row in predictions:
        target = _string(row, "target")
        if target not in TARGETS:
            continue
        if _string(row, "cofolding_model") != "ef2full":
            continue
        stoichiometry = _string(row, "stoichiometry")
        target_form = _string(row, "target_form", allow_empty=True)
        if stoichiometry != "1to1" or target_form != "":
            continue
        source_id = _string(row, "uuid")
        if source_id not in by_source:
            raise ValueError("prediction uuid has no included summary")
        if by_source[source_id]["target"] != target:
            raise ValueError("prediction target does not match its summary target")
        seed = _seed(row.get("seed"))
        group = groups.setdefault(source_id, {})
        if seed in group:
            raise ValueError("duplicate prediction (uuid, model, seed)")
        group[seed] = {
            "seed": seed,
            **{name: _score(row.get(source_name)) for name, source_name in SCORE_FIELDS.items()},
        }

    by_target: dict[str, list[dict]] = {}
    for source_id, summary in by_source.items():
        group = groups.get(source_id, {})
        if set(group) != SEEDS:
            raise ValueError("every included candidate requires exactly seeds {0, 1, 2, 3, 4}")
        by_target.setdefault(summary["target"], []).append(
            {
                name: value
                for name, value in {
                    **summary,
                    "predictions": [group[seed] for seed in sorted(SEEDS)],
                }.items()
                if name != "target"
            }
        )
    pools = [
        {
            "pool_id": f"p{index:02d}",
            "target": target,
            "candidates": sorted(by_target[target], key=lambda item: item["candidate_id"]),
        }
        for index, target in enumerate(TARGETS, 1)
        if target in by_target
    ]
    return {"schema_version": SCHEMA_VERSION, "purpose": "development", "pools": pools}


def _file_metadata(path: Path, *, name: str | None = None) -> dict:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return {"path": name or path.name, "sha256": digest.hexdigest(), "size_bytes": size}


def import_snapshot(snapshot: Path) -> dict:
    """Read projected columns from the fixed local snapshot after checking hashes."""
    snapshot = Path(snapshot).resolve()
    if snapshot.name != REVISION:
        raise ValueError(f"snapshot directory must be named for pinned revision {REVISION}")
    files = [_file_metadata(snapshot / path, name=path) for path in PINNED_HASHES]
    for record in files:
        if record["sha256"] != PINNED_HASHES[record["path"]]:
            raise ValueError(f"pinned source hash mismatch: {record['path']}")

    import pyarrow
    import pyarrow.parquet as pq

    for path, columns in (
        (SUMMARY_PATH, SUMMARY_COLUMNS),
        (PREDICTIONS_PATH, PREDICTION_COLUMNS),
    ):
        if not set(columns).issubset(pq.read_schema(snapshot / path).names):
            raise ValueError(f"required columns are missing from {path}")
    target_filter = [("target", "in", list(TARGETS))]
    summaries = pq.read_table(
        snapshot / SUMMARY_PATH, columns=list(SUMMARY_COLUMNS), filters=target_filter
    ).to_pylist()
    predictions = pq.read_table(
        snapshot / PREDICTIONS_PATH,
        columns=list(PREDICTION_COLUMNS),
        filters=target_filter
        + [
            ("cofolding_model", "=", "ef2full"),
            ("stoichiometry", "=", "1to1"),
            ("target_form", "=", ""),
        ],
    ).to_pylist()
    document = normalize(summaries, predictions)
    counts = {pool["target"]: len(pool["candidates"]) for pool in document["pools"]}
    if counts != dict.fromkeys(TARGETS, 90):
        raise ValueError("pinned development snapshot requires 7 targets with 90 candidates each")
    document["source"] = {
        "dataset": REPO,
        "revision": REVISION,
        "url": f"https://huggingface.co/datasets/{REPO}/tree/{REVISION}",
        "license": "CC-BY-4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "license_file": "data/LICENSE.md",
        "citation_file": "data/CITATION.cff",
        "files": files,
        "pyarrow_version": pyarrow.__version__,
        "model": "ef2full",
        "stoichiometry": "1to1",
        "target_form": "",
        "label": "binder_final: upstream composite experimental judgment, preserved exactly",
        "prediction_provenance": "Upstream posthoc uniform cofold predictions; no new GPU calls",
        "exposure": "All seven targets and outcomes were exposed in earlier development analyses",
        "projection": {
            SUMMARY_PATH: list(SUMMARY_COLUMNS),
            PREDICTIONS_PATH: list(PREDICTION_COLUMNS),
        },
    }
    return document


def audit(document: dict, *, output_record: dict) -> dict:
    """Return structure, missingness, and hashes without outcomes or score values."""
    pools = document["pools"]
    candidates = [candidate for pool in pools for candidate in pool["candidates"]]
    predictions = [
        prediction for candidate in candidates for prediction in candidate["predictions"]
    ]
    return {
        "schema_version": "molclaw-adaptive-import-audit-v1",
        "input_schema_version": document["schema_version"],
        "purpose": document["purpose"],
        "output": output_record,
        "source_files": document["source"]["files"],
        "pool_count": len(pools),
        "candidate_count": len(candidates),
        "prediction_count": len(predictions),
        "target_counts": {pool["target"]: len(pool["candidates"]) for pool in pools},
        "seed_counts": dict(sorted(Counter(str(row["seed"]) for row in predictions).items())),
        "missing_score_cells": {
            name: sum(row[name] is None for row in predictions) for name in SCORE_FIELDS
        },
        "candidates_with_missing_score": {
            name: sum(any(row[name] is None for row in item["predictions"]) for item in candidates)
            for name in SCORE_FIELDS
        },
    }


def _write_new(path: Path, document: dict) -> None:
    serialized = json.dumps(document, allow_nan=False, sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(serialized)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Fresh private input JSON")
    parser.add_argument("--audit", required=True, type=Path, help="Fresh label-free import audit")
    args = parser.parse_args(argv)
    try:
        if args.output.resolve() == args.audit.resolve():
            raise ValueError("output and audit must be different paths")
        for path in (args.output, args.audit):
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"output already exists: {path}")
        document = import_snapshot(args.snapshot)
        _write_new(args.output, document)
        _write_new(args.audit, audit(document, output_record=_file_metadata(args.output)))
    except (OSError, ValueError, ImportError) as exc:
        parser.exit(2, f"Import failed: {exc}\n")
    count = sum(len(pool["candidates"]) for pool in document["pools"])
    print(
        f"Imported {len(document['pools'])} development pools, {count} candidates; audit created."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

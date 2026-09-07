"""Feature-only, retrospective external candidate evaluation; no model execution.

The public/private boundary is an access protocol, not an OS sandbox. Checksums
detect accidental mutation relative to the local manifest, not a malicious owner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import secrets
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from interaction_design.benchmark_metrics import macro_summary, ranking_metrics, selection_metrics
from interaction_design.evaluation.jobs import checked_path, file_record, job_lock, read_json
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json

FEATURES = ("ef2_iptm", "ef2_ipsae", "ef2_sc_dockq", "ensemble_ipsae")
TARGETS = ("BBF-14", "EGFR", "IL-7Ra", "MBP", "TREM2", "TrkA")
REVISION = "9e1b81696da46835e9e9cde9a3da976e0abc92ab"
PROTOCOL_RULES_SHA256 = "6dc4c5e46e84bee09753d39beccdf501482bf5f90eeca732aaacb0bde3020442"


def _keys(value: object, keys: set[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"invalid {name} fields")


def _score(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 1


def _order(rows: list[dict], field: str) -> list[str]:
    return [
        row["candidate_id"]
        for row in sorted(
            rows,
            key=lambda r: (
                -(r["scores"][field] if r["scores"][field] is not None else -1),
                r["candidate_id"],
            ),
        )
    ]


def baseline_selections(rows: list[dict], quota: int, best_single: str) -> dict:
    """Every policy sees the same rounded, public feature values; null ranks last."""
    n = len(rows)
    ranks = {r["candidate_id"]: [] for r in rows}
    for field in FEATURES:
        values = [r["scores"][field] for r in rows if r["scores"][field] is not None]
        for row in rows:
            value = row["scores"][field]
            percentile = (
                0.0
                if value is None
                else (sum(v < value for v in values) + 0.5 * sum(v == value for v in values)) / n
            )
            ranks[row["candidate_id"]].append(percentile)
    policies = {
        "fixed": sorted(ranks)[:quota],
        "development_best_single": _order(rows, best_single)[:quota],
        "heuristic": sorted(ranks, key=lambda c: (-sum(ranks[c]), c))[:quota],
    }
    for field in ("ef2_iptm", "ef2_ipsae", "ensemble_ipsae"):
        policies[field] = _order(rows, field)[:quota]
    return policies


def prepare_benchmark(imported: Path, protocol_path: Path, output: Path) -> dict:
    protocol = read_json(protocol_path)
    rules = {key: value for key, value in protocol.items() if key != "quota"}
    if canonical_sha256(rules) != PROTOCOL_RULES_SHA256:
        raise ValueError("unsupported benchmark protocol rules")
    if (
        protocol.get("version") != "external-benchmark-v1"
        or protocol.get("source_revision") != REVISION
        or protocol.get("development_targets") != ["PD-L1"]
        or protocol.get("evaluation_targets") != list(TARGETS)
        or protocol.get("features") != list(FEATURES)
        or type(protocol.get("quota")) is not int
        or not 1 <= protocol["quota"] <= 90
    ):
        raise ValueError("unsupported benchmark protocol")
    data = read_json(imported)
    if data.get("version") != "anthropic-binder-import-v1" or (
        data.get("source", {}).get("revision") != REVISION
    ):
        raise ValueError("unsupported imported dataset version")
    if output.exists():
        raise ValueError("benchmark output must be a fresh directory")
    quota = protocol["quota"]
    salt = secrets.token_hex(16)
    groups: dict[str, list[dict]] = {t: [] for t in (*TARGETS, "PD-L1")}
    excluded = Counter()
    uuids = set()
    for row in data["rows"]:
        _keys(row, {"uuid", "target", "label", "adaptyv_binding", "twist_binding", "scores"}, "row")
        if not isinstance(row["uuid"], str) or not row["uuid"] or row["uuid"] in uuids:
            raise ValueError("invalid or duplicate source identity")
        uuids.add(row["uuid"])
        if row["target"] not in groups or (
            row["label"] is not None and type(row["label"]) is not bool
        ):
            raise ValueError("invalid target or composite label")
        _keys(row["scores"], set(FEATURES), "score")
        if any(v is not None and not _score(v) for v in row["scores"].values()):
            raise ValueError("invalid source score")
        if row["label"] is not None and all(v is None for v in row["scores"].values()):
            raise ValueError("every eligible candidate needs at least one finite evidence field")
        for vendor in ("adaptyv", "twist"):
            if row[f"{vendor}_binding"] not in {
                None,
                "binder",
                "non_binder",
                "not_expressed",
                "not_measured",
                "not_tested",
                "inconclusive",
            }:
                raise ValueError("invalid vendor status")
        if row["label"] is None:
            excluded[row["target"]] += 1
            continue
        identity = hashlib.sha256(f"{salt}:{row['uuid']}".encode()).hexdigest()[:16]
        groups[row["target"]].append(
            {
                **row,
                "candidate_id": f"c_{identity}",
                "scores": {
                    k: round(v, 6) if v is not None else None for k, v in row["scores"].items()
                },
            }
        )
    if any(len(rows) < quota for rows in groups.values()):
        raise ValueError("each target must have at least quota labeled candidates")
    dev = groups["PD-L1"]
    dev_metrics = {}
    for field in FEATURES:
        dev_metrics[field] = {
            **ranking_metrics(
                [r["scores"][field] if r["scores"][field] is not None else -1 for r in dev],
                [r["label"] for r in dev],
            ),
            "missing": sum(r["scores"][field] is None for r in dev),
            "missing_order": "tied bottom group",
        }
    best = min(FEATURES, key=lambda f: (-(dev_metrics[f]["average_precision"] or 0), f))
    output.mkdir(parents=True)
    shutil.copyfile(protocol_path, output / "protocol.json")
    shutil.copyfile(imported, output / "imported.json")
    private = {"salt": salt, "source": data["source"], "pools": {}}
    baselines, requests = {}, {}
    for i, target in enumerate(TARGETS, 1):
        pool_id = f"pool_{i:02d}"
        rows = sorted(groups[target], key=lambda r: r["candidate_id"])
        private["pools"][pool_id] = {"target": target, "rows": rows}
        payload = {
            "version": "external-benchmark-request-v1",
            "pool_id": pool_id,
            "quota": quota,
            "objective": (
                "Select the candidates most likely to have positive binding calls. "
                "Use only these computational features. No calibrated thresholds are supplied."
            ),
            "feature_definitions": {
                "ef2_iptm": "ESMFold2 full complex ipTM, median of 5 seeds, higher is better",
                "ef2_ipsae": (
                    "ESMFold2 full minimum directional ipSAE, median of 5 seeds, higher is better"
                ),
                "ef2_sc_dockq": (
                    "ESMFold2 full DockQ against design pose, median of 5 seeds; "
                    "consistency, not experimental accuracy"
                ),
                "ensemble_ipsae": (
                    "Median of ef2full, ef2fast, ptxv2 five-seed ipSAE medians; "
                    "correlated computational evidence"
                ),
            },
            "missing_values": (
                "null is unknown; values are rounded to six decimals for all policies"
            ),
            "candidates": [
                {"candidate_id": r["candidate_id"], "scores": r["scores"]} for r in rows
            ],
        }
        request = {"request_sha256": canonical_sha256(payload), "payload": payload}
        write_json(output / "requests" / f"{pool_id}.json", request)
        requests[pool_id] = f"requests/{pool_id}.json"
        baselines[pool_id] = baseline_selections(payload["candidates"], quota, best)
    write_json(output / "private.json", private)
    write_json(output / "baselines.json", baselines)
    write_json(
        output / "development.json",
        {"target": "PD-L1", "metrics": dev_metrics, "best_single": best},
    )
    write_json(
        output / "cohort.json",
        {
            "included": {t: len(rs) for t, rs in groups.items()},
            "excluded_null_labels": dict(excluded),
            "import_qc": data.get("qc"),
        },
    )
    implementation = output / "implementation"
    implementation.mkdir()
    for name in ("benchmark.py", "benchmark_metrics.py"):
        shutil.copyfile(Path(__file__).with_name(name), implementation / name)
    files = [file_record(p, output) for p in sorted(output.rglob("*")) if p.is_file()]
    write_json(
        output / "manifest.json",
        {
            "version": "external-benchmark-session-v1",
            "created_at": datetime.now(UTC).isoformat(),
            "files": files,
            "requests": requests,
        },
    )
    write_json(output / "commitments.json", {})
    return {"status": "awaiting_selections", "session": str(output), "requests": requests}


def _load(output: Path) -> dict:
    manifest = read_json(output / "manifest.json")
    if manifest.get("version") != "external-benchmark-session-v1":
        raise ValueError("unsupported session version")
    for record in manifest["files"]:
        checked_path(output, record)
    for name in ("benchmark.py", "benchmark_metrics.py"):
        if (output / "implementation" / name).read_bytes() != Path(__file__).with_name(
            name
        ).read_bytes():
            raise ValueError("benchmark implementation changed; use the archived implementation")
    return manifest


def observe_benchmark(output: Path, pool_id: str) -> dict:
    manifest = _load(output)
    if pool_id not in manifest["requests"]:
        raise ValueError("unknown pool")
    return read_json(output / manifest["requests"][pool_id])


def validate_submission(request: dict, submission: dict) -> None:
    _keys(
        submission, {"request_sha256", "candidate_ids", "reason", "evidence", "actor"}, "submission"
    )
    if submission["request_sha256"] != request["request_sha256"]:
        raise ValueError("stale or wrong request hash")
    payload = request["payload"]
    rows = {r["candidate_id"]: r for r in payload["candidates"]}
    ids = submission["candidate_ids"]
    if (
        not isinstance(ids, list)
        or len(ids) != payload["quota"]
        or any(not isinstance(c, str) for c in ids)
        or len(set(ids)) != len(ids)
        or not set(ids) <= rows.keys()
    ):
        raise ValueError("selection must contain exactly quota unique visible candidate IDs")
    if (
        not isinstance(submission["reason"], str)
        or not 1 <= len(submission["reason"].strip()) <= 6000
    ):
        raise ValueError("a concise nonempty selection reason is required")
    actor = submission["actor"]
    _keys(actor, {"harness", "model", "agent_id"}, "actor")
    if not isinstance(actor["harness"], str) or not actor["harness"].strip():
        raise ValueError("harness must be identified")
    if any(v is not None and (not isinstance(v, str) or not v.strip()) for v in actor.values()):
        raise ValueError("actor metadata must be nonempty text or null")
    if not isinstance(submission["evidence"], list):
        raise ValueError("evidence must be a list")
    covered = set()
    for item in submission["evidence"]:
        _keys(item, {"candidate_id", "field", "value"}, "evidence")
        c, field, value = item["candidate_id"], item["field"], item["value"]
        if not isinstance(c, str) or c not in ids or field not in FEATURES:
            raise ValueError(
                "evidence must refer to a selected visible candidate and allowed field"
            )
        if not _score(value) or value != rows[c]["scores"][field]:
            raise ValueError("evidence must exactly match a finite visible value")
        covered.add(c)
    if covered != set(ids):
        raise ValueError("at least one evidence item is required for every selected candidate")


def _receipt(output: Path, pool_id: str, request: dict) -> dict:
    commitments = read_json(output / "commitments.json")
    if pool_id not in commitments:
        raise ValueError("selection receipt is not committed; inspect interrupted write")
    if commitments[pool_id].get("path") != f"submissions/{pool_id}.json":
        raise ValueError("commitment path does not match its pool")
    receipt = read_json(checked_path(output, commitments[pool_id]))
    submission = receipt["submission"]
    if receipt["submission_sha256"] != canonical_sha256(submission):
        raise ValueError("submission checksum mismatch")
    validate_submission(request, submission)
    return receipt


def apply_benchmark(output: Path, pool_id: str, submission: dict) -> dict:
    with job_lock(output):
        request = observe_benchmark(output, pool_id)
        validate_submission(request, submission)
        path = output / "submissions" / f"{pool_id}.json"
        if path.exists():
            receipt = _receipt(output, pool_id, request)
            if receipt["submission"] != submission:
                raise ValueError("selection is already committed; replacement forbidden")
        else:
            if (output / "report.json").exists():
                raise ValueError("evaluation is already revealed")
            commitments = read_json(output / "commitments.json")
            if pool_id in commitments:
                raise ValueError("committed receipt is missing; inspect interrupted write")
            receipt = {"submission": submission, "submission_sha256": canonical_sha256(submission)}
            write_json(path, receipt)
            commitments[pool_id] = file_record(path, output)
            write_json(output / "commitments.json", commitments)
        return {
            "status": "accepted",
            "pool_id": pool_id,
            "submission_sha256": receipt["submission_sha256"],
        }


def report_benchmark(output: Path) -> dict:
    with job_lock(output):
        manifest = _load(output)
        submissions = {}
        for pool_id, request_path in manifest["requests"].items():
            if not (output / "submissions" / f"{pool_id}.json").is_file():
                raise ValueError("reveal requires all host selections to be committed")
            submissions[pool_id] = _receipt(output, pool_id, read_json(output / request_path))
        if set(read_json(output / "commitments.json")) != set(manifest["requests"]):
            raise ValueError("commitment pool set does not match requests")
        private = read_json(output / "private.json")
        baselines = read_json(output / "baselines.json")
        by_target = {}
        for pool_id, pool in private["pools"].items():
            rows = pool["rows"]
            policies = {
                **baselines[pool_id],
                "harness": submissions[pool_id]["submission"]["candidate_ids"],
            }
            scores = {}
            for field in FEATURES:
                available = [r for r in rows if r["scores"][field] is not None]
                scores[field] = {
                    **ranking_metrics(
                        [r["scores"][field] for r in available], [r["label"] for r in available]
                    ),
                    "missing": len(rows) - len(available),
                }
            sensitivity = {}
            for vendor in ("adaptyv", "twist"):
                available = [
                    {**r, "label": r[f"{vendor}_binding"] == "binder"}
                    for r in rows
                    if r[f"{vendor}_binding"] in {"binder", "non_binder"}
                ]
                known_ids = {r["candidate_id"] for r in available}
                sensitivity[vendor] = {
                    policy: {
                        **selection_metrics([c for c in ids if c in known_ids], available),
                        "selected_unknown": len(ids) - len(set(ids) & known_ids),
                    }
                    for policy, ids in policies.items()
                }
            quota = len(policies["harness"])
            by_target[pool["target"]] = {
                "pool_id": pool_id,
                "n": len(rows),
                "positives": sum(r["label"] for r in rows),
                "random_expected_hits": quota * sum(r["label"] for r in rows) / len(rows),
                "policies": {p: selection_metrics(ids, rows) for p, ids in policies.items()},
                "ranking": scores,
                "vendor_sensitivity": sensitivity,
            }
        policy_names = next(iter(by_target.values()))["policies"]
        macro = {
            p: macro_summary([t["policies"][p]["precision"] for t in by_target.values()])
            for p in policy_names
        }
        ranking_macro = {
            f: {
                metric: macro_summary(
                    [
                        t["ranking"][f][metric]
                        for t in by_target.values()
                        if t["ranking"][f][metric] is not None
                    ]
                )
                for metric in ("auroc", "average_precision")
            }
            for f in FEATURES
        }
        differences = {
            baseline: macro_summary(
                [
                    t["policies"]["harness"]["precision"] - t["policies"][baseline]["precision"]
                    for t in by_target.values()
                ]
            )
            for baseline in ("heuristic", "development_best_single")
        }
        protocol = read_json(output / "protocol.json")
        report = {
            "version": "external-benchmark-report-v1",
            "protocol_sha256": canonical_sha256(protocol),
            "primary_endpoint": protocol["primary_endpoint"],
            "source": private["source"],
            "by_target": by_target,
            "macro_precision": macro,
            "macro_ranking": ranking_macro,
            "paired_precision_differences": differences,
            "development": read_json(output / "development.json"),
            "cohort": read_json(output / "cohort.json"),
            "host_submissions": {p: r["submission_sha256"] for p, r in submissions.items()},
            "actor_metadata_verified": False,
            "host_token_usage": None,
            "new_gpu_inference_runs": 0,
            "new_wetlab_experiments": 0,
            "limitations": protocol["limitations"],
        }
        if (output / "report.json").exists() and read_json(output / "report.json") != report:
            raise ValueError("revealed report differs from committed inputs")
        write_json(output / "report.json", report)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--imported", type=Path, required=True)
    prepare.add_argument("--protocol", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    for name in ("observe", "apply", "report"):
        command = commands.add_parser(name)
        command.add_argument("session", type=Path)
        if name != "report":
            command.add_argument("--pool", required=True)
        if name == "apply":
            command.add_argument("--submission", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare_benchmark(args.imported, args.protocol, args.output)
        elif args.action == "observe":
            result = observe_benchmark(args.session, args.pool)
        elif args.action == "apply":
            import sys

            submission = (
                json.load(sys.stdin) if str(args.submission) == "-" else read_json(args.submission)
            )
            result = apply_benchmark(args.session, args.pool, submission)
        else:
            result = report_benchmark(args.session)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())

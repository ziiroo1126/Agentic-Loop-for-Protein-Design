"""Repeat host decisions on frozen public benchmark pools without rerunning models.

The original session remains immutable. Each repetition reuses the v1 validator,
with byte-identical requests, baselines and labels. The coordinator gates reports
until every new decision is committed. This is an access protocol, not OS isolation.
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

from interaction_design.benchmark import (
    _load,
    _receipt,
    apply_benchmark,
    observe_benchmark,
    report_benchmark,
    validate_submission,
)
from interaction_design.benchmark_metrics import macro_summary
from interaction_design.evaluation.jobs import checked_path, file_record, job_lock, read_json
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json
from interaction_design.repeat_metrics import random_hit_distribution, selection_stability

LIMITATIONS = [
    (
        "Exploratory decision repetitions on previously disclosed targets, "
        "not a new holdout or calibration fit."
    ),
    ("Repetitions do not increase the number of independent biological targets or experiments."),
    (
        "Primary summaries use new runs only; "
        "the historical run is a separate descriptive reference."
    ),
    (
        "Candidate IDs, order, visible numbers, quota, instructions and baselines remain fixed; "
        "model sampling settings are unknown."
    ),
    (
        "Feature-only access is a protocol, not OS isolation; "
        "public-data training contamination remains possible."
    ),
    (
        "Random hit distributions condition on the observed labels and are not "
        "prospective success estimates or Agent superiority tests."
    ),
    (
        "No new protein generation, GPU inference or wet-lab experiments; "
        "actor metadata and model stochastic independence are not authenticated."
    ),
]


def _copy_record(source: Path, destination: Path, record: dict) -> None:
    relative = Path(record["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("snapshot record must have a contained relative path")
    path = checked_path(source, record)
    output = destination / relative
    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, output)


def prepare_repetitions(
    source: Path, instructions: Path, output: Path, *, repetitions: int = 2
) -> dict:
    """Fork only immutable v1 inputs, recording the original run separately."""
    if type(repetitions) is not int or not 2 <= repetitions <= 5:
        raise ValueError("repetitions must be an integer from 2 to 5")
    original = _load(source)
    if not (source / "report.json").is_file():
        raise ValueError("source must be a completed benchmark session")
    pools = sorted(original["requests"])
    if set(read_json(source / "commitments.json")) != set(pools):
        raise ValueError("source commitment pool set differs from requests")
    for pool in pools:
        _receipt(source, pool, read_json(source / original["requests"][pool]))
    if not instructions.is_file() or not instructions.read_text().strip():
        raise ValueError("nonempty host instructions are required")
    if output.exists():
        raise ValueError("study output must be a fresh directory")
    output.mkdir(parents=True)
    historical = output / "historical"
    for record in original["files"]:
        _copy_record(source, historical, record)
    for relative in ["manifest.json", "commitments.json", "report.json"] + [
        f"submissions/{pool}.json" for pool in pools
    ]:
        destination = historical / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination)
    # Recompute the copied historical report, leaving the sealed source untouched.
    report_benchmark(historical)
    shutil.copyfile(instructions, output / "host-instructions.md")
    trials = [f"repeat_{i:02d}" for i in range(1, repetitions + 1)]
    immutable = [p for p in historical.rglob("*") if p.is_file() and p.name != ".lock"]
    for trial in trials:
        directory = output / trial
        for record in original["files"]:
            _copy_record(source, directory, record)
        write_json(
            directory / "manifest.json",
            {
                **original,
                "created_at": datetime.now(UTC).isoformat(),
                "repetition_of_source_manifest_sha256": canonical_sha256(original),
            },
        )
        immutable += [directory / record["path"] for record in original["files"]]
        immutable.append(directory / "manifest.json")
        write_json(directory / "commitments.json", {})
    protocol = {
        "version": "external-benchmark-repetitions-v1",
        "new_repetitions": repetitions,
        "trials": trials,
        "pools": pools,
        "source_manifest_sha256": canonical_sha256(original),
        "primary": "New repetitions only, per-target mean precision before target bootstrap",
        "secondary": "Historical plus new-run selection Jaccard, explicitly descriptive",
        "random": "Exact hypergeometric counts per pool, independent selection draws across pools",
        "limitations": LIMITATIONS,
    }
    write_json(output / "protocol.json", protocol)
    immutable += [output / "protocol.json", output / "host-instructions.md"]
    for name in ("benchmark_repeats.py", "repeat_metrics.py"):
        destination = output / "implementation" / name
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(Path(__file__).with_name(name), destination)
        immutable.append(destination)
    write_json(
        output / "manifest.json",
        {
            "version": "benchmark-repetition-study-v1",
            "trials": trials,
            "pools": pools,
            "files": [file_record(p, output) for p in sorted(immutable)],
        },
    )
    return {"status": "awaiting_selections", "study": str(output), "trials": trials, "pools": pools}


def _load_study(study: Path) -> dict:
    manifest = read_json(study / "manifest.json")
    if manifest.get("version") != "benchmark-repetition-study-v1":
        raise ValueError("unsupported repetition study")
    for record in manifest["files"]:
        checked_path(study, record)
    protocol = read_json(study / "protocol.json")
    repetitions = protocol.get("new_repetitions")
    if type(repetitions) is not int or not 2 <= repetitions <= 5:
        raise ValueError("unsupported repetition count")
    trials = [f"repeat_{i:02d}" for i in range(1, repetitions + 1)]
    pools = sorted(read_json(study / "historical" / "manifest.json")["requests"])
    if (
        protocol.get("version") != "external-benchmark-repetitions-v1"
        or protocol.get("trials") != trials
        or manifest.get("trials") != trials
        or protocol.get("pools") != pools
        or manifest.get("pools") != pools
        or any(pool != f"pool_{i:02d}" for i, pool in enumerate(pools, 1))
    ):
        raise ValueError("study routing differs from frozen protocol")
    for name in ("benchmark_repeats.py", "repeat_metrics.py"):
        if (study / "implementation" / name).read_bytes() != Path(__file__).with_name(
            name
        ).read_bytes():
            raise ValueError("repetition implementation changed; use the archived implementation")
    return manifest


def observe_repetition(study: Path, trial: str, pool: str) -> dict:
    manifest = _load_study(study)
    if trial not in manifest["trials"] or pool not in manifest["pools"]:
        raise ValueError("unknown repetition or pool")
    return observe_benchmark(study / trial, pool)


def _committed(study: Path, manifest: dict, *, complete: bool) -> dict:
    receipts = {}
    for trial in ["historical", *manifest["trials"]]:
        directory = study / trial
        commitments = read_json(directory / "commitments.json")
        if not set(commitments) <= set(manifest["pools"]):
            raise ValueError("unexpected committed pool")
        if complete and set(commitments) != set(manifest["pools"]):
            raise ValueError("report requires every repetition and pool to be committed")
        for pool in commitments:
            request = read_json(directory / "requests" / f"{pool}.json")
            receipts[(trial, pool)] = _receipt(directory, pool, request)
    seen_actors = set()
    seen_submissions = set()
    for (trial, _), receipt in receipts.items():
        actor = receipt["submission"]["actor"]["agent_id"]
        digest = receipt["submission_sha256"]
        if trial != "historical" and (
            digest in seen_submissions or (actor is not None and actor in seen_actors)
        ):
            raise ValueError("use a fresh decision context; a prior actor or submission was reused")
        if actor is not None:
            seen_actors.add(actor)
        seen_submissions.add(digest)
    return receipts


def apply_repetition(study: Path, trial: str, pool: str, submission: dict) -> dict:
    with job_lock(study):
        request = observe_repetition(study, trial, pool)
        validate_submission(request, submission)
        manifest = _load_study(study)
        receipts = _committed(study, manifest, complete=False)
        actor = submission["actor"]["agent_id"]
        for key, receipt in receipts.items():
            if key == (trial, pool):
                continue
            previous = receipt["submission"]
            if previous == submission or (
                actor is not None and actor == previous["actor"]["agent_id"]
            ):
                raise ValueError(
                    "use a fresh decision context; a prior actor or submission was reused"
                )
        if (study / "report.json").exists() and (trial, pool) not in receipts:
            raise ValueError("study is already revealed")
        return {"trial": trial, **apply_benchmark(study / trial, pool, submission)}


def _run_summary(report: dict) -> dict:
    by_target = {}
    for target, row in report["by_target"].items():
        by_target[target] = {
            "composite": row["policies"]["harness"],
            **{
                vendor: row["vendor_sensitivity"][vendor]["harness"]
                for vendor in ("adaptyv", "twist")
            },
        }
    return {
        "by_target": by_target,
        "totals": {
            endpoint: {
                "hits": sum(row[endpoint]["hits"] for row in by_target.values()),
                "selected_with_known_endpoint": sum(
                    row[endpoint]["selected"] for row in by_target.values()
                ),
            }
            for endpoint in ("composite", "adaptyv", "twist")
        },
    }


def report_repetitions(study: Path) -> dict:
    with job_lock(study):
        manifest = _load_study(study)
        receipts = _committed(study, manifest, complete=True)
        # Only after the complete-study gate may any new trial outcomes be computed.
        historical = read_json(study / "historical" / "report.json")
        reports = {trial: report_benchmark(study / trial) for trial in manifest["trials"]}
        runs = {
            "historical": _run_summary(historical),
            **{k: _run_summary(v) for k, v in reports.items()},
        }
        targets = sorted(historical["by_target"])
        totals = [runs[trial]["totals"]["composite"]["hits"] for trial in manifest["trials"]]
        paired = {}
        for baseline in ("heuristic", "ef2_ipsae", "development_best_single"):
            differences = []
            for target in targets:
                host_mean = statistics.mean(
                    runs[t]["by_target"][target]["composite"]["precision"]
                    for t in manifest["trials"]
                )
                differences.append(
                    host_mean - historical["by_target"][target]["policies"][baseline]["precision"]
                )
            paired[baseline] = macro_summary(differences)
        stability = {}
        for pool in manifest["pools"]:
            selections = {
                trial: receipts[(trial, pool)]["submission"]["candidate_ids"]
                for trial in ["historical", *manifest["trials"]]
            }
            target = next(k for k, row in historical["by_target"].items() if row["pool_id"] == pool)
            stability[target] = {
                "new_runs_only": selection_stability([selections[t] for t in manifest["trials"]]),
                "historical_plus_new": selection_stability(list(selections.values())),
            }
        quota = next(iter(historical["by_target"].values()))["policies"]["harness"]["selected"]
        result = {
            "version": "benchmark-repetition-report-v1",
            "source": historical["source"],
            "primary_endpoint": historical["primary_endpoint"],
            "study_protocol_sha256": canonical_sha256(read_json(study / "protocol.json")),
            "runs": runs,
            "primary_new_runs": {
                "runs": len(totals),
                "total_hits_per_run": totals,
                "mean_total_hits": statistics.mean(totals),
                "min_total_hits": min(totals),
                "max_total_hits": max(totals),
                "sample_sd_total_hits": statistics.stdev(totals),
                "paired_precision_differences": paired,
                "independent_biological_targets": len(targets),
            },
            "selection_stability": stability,
            "baseline_total_hits": {
                policy: sum(
                    row["policies"][policy]["hits"] for row in historical["by_target"].values()
                )
                for policy in next(iter(historical["by_target"].values()))["policies"]
                if policy != "harness"
            },
            "random_reference": random_hit_distribution(
                [
                    {"n": row["n"], "positives": row["positives"]}
                    for row in historical["by_target"].values()
                ],
                quota,
            ),
            "submissions": {
                f"{trial}/{pool}": receipt["submission_sha256"]
                for (trial, pool), receipt in receipts.items()
            },
            "actor_metadata_verified": False,
            "model_sampling_parameters": None,
            "host_token_usage": None,
            "new_gpu_inference_runs": 0,
            "new_wetlab_experiments": 0,
            "limitations": LIMITATIONS,
        }
        if (study / "report.json").exists() and read_json(study / "report.json") != result:
            raise ValueError("revealed repetition report differs from committed inputs")
        write_json(study / "report.json", result)
        return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-session", type=Path, required=True)
    prepare.add_argument("--instructions", type=Path, required=True)
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--repetitions", type=int, default=2)
    for name in ("observe", "apply", "report"):
        command = commands.add_parser(name)
        command.add_argument("study", type=Path)
        if name != "report":
            command.add_argument("--trial", required=True)
            command.add_argument("--pool", required=True)
        if name == "apply":
            command.add_argument("--submission", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            result = prepare_repetitions(
                args.source_session, args.instructions, args.output, repetitions=args.repetitions
            )
        elif args.action == "observe":
            result = observe_repetition(args.study, args.trial, args.pool)
        elif args.action == "apply":
            submission = (
                json.load(sys.stdin) if str(args.submission) == "-" else read_json(args.submission)
            )
            result = apply_repetition(args.study, args.trial, args.pool, submission)
        else:
            result = report_repetitions(args.study)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
        parser.exit(2, f"error: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())

"""Synthetic-only repetition coordinator integrity and known-outcome checks."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from interaction_design.benchmark import (
    FEATURES,
    REVISION,
    TARGETS,
    apply_benchmark,
    observe_benchmark,
    prepare_benchmark,
    report_benchmark,
)
from interaction_design.benchmark_repeats import (
    apply_repetition,
    main,
    observe_repetition,
    prepare_repetitions,
    report_repetitions,
)
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json


def read(path):
    return json.loads(path.read_text())


def snapshot(directory, *, include_locks=False):
    return {
        str(path.relative_to(directory)): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file() and (include_locks or path.name != ".lock")
    }


def submission_for(request, actor, indices=(0, 2)):
    rows = sorted(request["payload"]["candidates"], key=lambda row: -row["scores"]["ef2_iptm"])
    selected = [rows[index] for index in indices]
    return {
        "request_sha256": request["request_sha256"],
        "candidate_ids": [row["candidate_id"] for row in selected],
        "reason": "Synthetic test selection based on the visible computational scores.",
        "evidence": [
            {
                "candidate_id": row["candidate_id"],
                "field": "ef2_iptm",
                "value": row["scores"]["ef2_iptm"],
            }
            for row in selected
        ],
        "actor": {"harness": "pytest-synthetic", "model": None, "agent_id": actor},
    }


@pytest.fixture(scope="module")
def completed_source(tmp_path_factory):
    base = tmp_path_factory.mktemp("synthetic-completed-benchmark")
    rows = []
    for target in (*TARGETS, "PD-L1"):
        for index in range(4):
            rows.append(
                {
                    "uuid": f"synthetic-{target}-{index}",
                    "target": target,
                    "label": index in (0, 2),
                    "adaptyv_binding": ["binder", "not_expressed", "inconclusive", "not_measured"][
                        index
                    ],
                    "twist_binding": [None, "non_binder", "binder", "not_tested"][index],
                    "scores": dict.fromkeys(FEATURES, [0.9, 0.8, 0.7, 0.6][index]),
                }
            )
    imported = write_json(
        base / "imported.json",
        {
            "version": "anthropic-binder-import-v1",
            "source": {"revision": REVISION, "repository": "synthetic-fixture"},
            "rows": rows,
            "qc": {"origin": "synthetic test data"},
        },
    )
    protocol = read(Path(__file__).parents[1] / "config" / "external-benchmark-v1.json")
    protocol["quota"] = 2
    source = base / "source"
    prepare_benchmark(imported, write_json(base / "protocol.json", protocol), source)
    for pool in read(source / "manifest.json")["requests"]:
        request = observe_benchmark(source, pool)
        apply_benchmark(source, pool, submission_for(request, f"historical-{pool}", (1, 3)))
    report_benchmark(source)
    return source


@pytest.fixture
def study(tmp_path, completed_source):
    instructions = tmp_path / "instructions.md"
    instructions.write_text("Use only observed scores and provide numeric evidence.\n")
    output = tmp_path / "study"
    source_before = snapshot(completed_source, include_locks=True)
    result = prepare_repetitions(completed_source, instructions, output)
    assert result["status"] == "awaiting_selections"
    assert result["trials"] == ["repeat_01", "repeat_02"]
    assert len(result["pools"]) == 6
    assert snapshot(completed_source, include_locks=True) == source_before
    yield output
    assert snapshot(completed_source, include_locks=True) == source_before


def commit_trial(study, trial, indices):
    receipts = {}
    for pool in read(study / "manifest.json")["pools"]:
        request = observe_repetition(study, trial, pool)
        receipts[pool] = apply_repetition(
            study, trial, pool, submission_for(request, f"{trial}-{pool}", indices)
        )
    return receipts


def commit_all(study):
    commit_trial(study, "repeat_01", (0, 2))
    commit_trial(study, "repeat_02", (0, 1))


def test_prepare_preserves_inputs_and_observe_excludes_hidden_metadata(study, completed_source):
    original = read(completed_source / "manifest.json")
    assert read(study / "historical" / "report.json") == read(completed_source / "report.json")
    assert (study / "host-instructions.md").read_bytes() == (
        study.parent / "instructions.md"
    ).read_bytes()
    for trial in ("repeat_01", "repeat_02"):
        assert read(study / trial / "commitments.json") == {}
        assert not (study / trial / "submissions").exists()
        for record in original["files"]:
            assert (study / trial / record["path"]).read_bytes() == (
                completed_source / record["path"]
            ).read_bytes()
        for pool in original["requests"]:
            request = observe_repetition(study, trial, pool)
            assert request == observe_benchmark(completed_source, pool)
            assert set(request) == {"payload", "request_sha256"}
            assert request["request_sha256"] == canonical_sha256(request["payload"])
            assert request["payload"]["quota"] == 2
            for row in request["payload"]["candidates"]:
                assert set(row) == {"candidate_id", "scores"}
                assert set(row["scores"]) == set(FEATURES)
            serialized = json.dumps(request)
            for hidden in ("uuid", "target", "label", "adaptyv_binding", "twist_binding", "source"):
                assert f'"{hidden}"' not in serialized
            for target in (*TARGETS, "PD-L1"):
                assert target not in serialized


def test_complete_first_trial_cannot_reveal_while_second_trial_pending(study):
    commit_trial(study, "repeat_01", (0, 2))
    before = snapshot(study)
    with pytest.raises(ValueError, match="every repetition and pool"):
        report_repetitions(study)
    assert snapshot(study) == before
    assert not (study / "report.json").exists()
    assert not (study / "repeat_01" / "report.json").exists()
    assert not (study / "repeat_02" / "report.json").exists()


def test_all_twelve_decisions_produce_known_primary_baseline_random_and_stability(study):
    commit_all(study)
    report = report_repetitions(study)
    primary = report["primary_new_runs"]
    assert primary["runs"] == 2
    assert primary["total_hits_per_run"] == [12, 6]
    assert primary["mean_total_hits"] == 9
    assert primary["min_total_hits"] == 6
    assert primary["max_total_hits"] == 12
    assert primary["sample_sd_total_hits"] == pytest.approx(math.sqrt(18))
    assert primary["independent_biological_targets"] == 6
    assert report["runs"]["historical"]["totals"]["composite"]["hits"] == 0
    assert len(report["submissions"]) == 18
    assert sum(key.startswith("repeat_") for key in report["submissions"]) == 12
    assert set(report["selection_stability"]) == set(TARGETS)
    assert "PD-L1" not in report["selection_stability"]
    for baseline in ("heuristic", "ef2_ipsae", "development_best_single"):
        assert report["baseline_total_hits"][baseline] == 6
        assert primary["paired_precision_differences"][baseline] == {
            "n": 6,
            "mean": 0.25,
            "ci95": [0.25, 0.25],
        }
    for target in TARGETS:
        new = report["selection_stability"][target]["new_runs_only"]
        assert new == {
            "runs": 2,
            "pairs": 1,
            "mean_pairwise_jaccard": pytest.approx(1 / 3),
            "min_pairwise_jaccard": pytest.approx(1 / 3),
            "max_pairwise_jaccard": pytest.approx(1 / 3),
            "union_size": 3,
            "intersection_size": 1,
        }
        historical = report["selection_stability"][target]["historical_plus_new"]
        assert historical["runs"] == 3
        assert historical["pairs"] == 3
        assert historical["mean_pairwise_jaccard"] == pytest.approx(2 / 9)
        assert historical["min_pairwise_jaccard"] == 0
        assert historical["max_pairwise_jaccard"] == pytest.approx(1 / 3)
        assert historical["union_size"] == 4
        assert historical["intersection_size"] == 0
    random = report["random_reference"]
    assert random["expected_hits"] == 6
    assert random["variance"] == 2
    # Each n=4, K=2, q=2 draw has masses [1, 4, 1]/6; six independent pools.
    counts = [1]
    for _ in range(6):
        combined = [0] * (len(counts) + 2)
        for hits, count in enumerate(counts):
            for added, ways in enumerate([1, 4, 1]):
                combined[hits + added] += count * ways
        counts = combined
    assert random["pmf"] == [
        {"hits": hits, "probability": pytest.approx(count / 6**6)}
        for hits, count in enumerate(counts)
    ]
    assert random["central_95_interval"] == [3, 9]
    assert report["actor_metadata_verified"] is False
    assert report["model_sampling_parameters"] is None
    assert report["host_token_usage"] is None
    assert report["new_gpu_inference_runs"] == report["new_wetlab_experiments"] == 0
    assert read(study / "report.json") == report
    before = snapshot(study)
    assert report_repetitions(study) == report
    assert snapshot(study) == before


def test_unknown_vendor_endpoints_are_excluded_from_known_denominators(study):
    commit_all(study)
    report = report_repetitions(study)
    expected = {
        "historical": {"adaptyv": (0, 0), "twist": (0, 6)},
        "repeat_01": {"adaptyv": (6, 6), "twist": (6, 6)},
        "repeat_02": {"adaptyv": (6, 6), "twist": (0, 6)},
    }
    for trial, endpoints in expected.items():
        for endpoint, (hits, selected) in endpoints.items():
            assert report["runs"][trial]["totals"][endpoint] == {
                "hits": hits,
                "selected_with_known_endpoint": selected,
            }
        for target in TARGETS:
            row = report["runs"][trial]["by_target"][target]
            for endpoint in ("adaptyv", "twist"):
                assert row[endpoint]["selected"] + row[endpoint]["selected_unknown"] == 2
            if trial == "historical":
                assert row["adaptyv"]["precision"] is None
                assert row["adaptyv"]["selected_unknown"] == 2


@pytest.mark.parametrize("prior", ["historical", "same_trial", "other_trial"])
def test_actor_reuse_is_rejected_across_historical_and_new_decisions(study, prior):
    actor = "historical-pool_01"
    if prior != "historical":
        trial = "repeat_01" if prior == "same_trial" else "repeat_02"
        actor = "synthetic-reused-agent"
        request = observe_repetition(study, trial, "pool_02")
        apply_repetition(study, trial, "pool_02", submission_for(request, actor))
    request = observe_repetition(study, "repeat_01", "pool_01")
    before = snapshot(study)
    with pytest.raises(ValueError, match="fresh decision context"):
        apply_repetition(study, "repeat_01", "pool_01", submission_for(request, actor))
    assert snapshot(study) == before


def test_identical_submission_reuse_rejected_even_without_reported_actor_id(study):
    request = observe_repetition(study, "repeat_01", "pool_01")
    submission = submission_for(request, None)
    apply_repetition(study, "repeat_01", "pool_01", submission)
    before = snapshot(study)
    with pytest.raises(ValueError, match="fresh decision context"):
        apply_repetition(study, "repeat_02", "pool_01", deepcopy(submission))
    assert snapshot(study) == before


def test_same_decision_is_idempotent_and_conflicting_replacement_is_rejected(study):
    request = observe_repetition(study, "repeat_01", "pool_01")
    submission = submission_for(request, "fresh-one")
    receipt = apply_repetition(study, "repeat_01", "pool_01", submission)
    before = snapshot(study)
    assert apply_repetition(study, "repeat_01", "pool_01", deepcopy(submission)) == receipt
    assert snapshot(study) == before
    replacement = deepcopy(submission)
    replacement["reason"] = "A conflicting replacement for the already committed decision."
    with pytest.raises(ValueError, match="already committed"):
        apply_repetition(study, "repeat_01", "pool_01", replacement)
    assert snapshot(study) == before


@pytest.mark.parametrize("recompute_self_hash", [False, True])
def test_tampered_receipt_blocks_reveal_without_writing_trial_reports(study, recompute_self_hash):
    commit_all(study)
    path = study / "repeat_02" / "submissions" / "pool_06.json"
    receipt = read(path)
    receipt["submission"]["reason"] = "Mutation after commitment."
    if recompute_self_hash:
        receipt["submission_sha256"] = canonical_sha256(receipt["submission"])
    write_json(path, receipt)
    before = snapshot(study)
    with pytest.raises(ValueError, match="checksum|commit"):
        report_repetitions(study)
    assert snapshot(study) == before
    assert not (study / "repeat_01" / "report.json").exists()


def test_swapped_commitment_records_between_pools_are_rejected(study):
    commit_all(study)
    path = study / "repeat_02" / "commitments.json"
    commitments = read(path)
    commitments["pool_01"], commitments["pool_02"] = (
        commitments["pool_02"],
        commitments["pool_01"],
    )
    write_json(path, commitments)
    before = snapshot(study)
    with pytest.raises(ValueError, match="commitment path does not match its pool"):
        report_repetitions(study)
    assert snapshot(study) == before


def test_reuse_committed_through_original_api_is_rejected_before_reveal(study):
    commit_trial(study, "repeat_01", (0, 2))
    for pool in read(study / "manifest.json")["pools"]:
        request = observe_repetition(study, "repeat_02", pool)
        # The original API does not know about other trials or actor reuse.
        apply_benchmark(
            study / "repeat_02", pool, submission_for(request, f"repeat_01-{pool}", (0, 1))
        )
    before = snapshot(study)
    with pytest.raises(ValueError, match="reus|fresh|actor"):
        report_repetitions(study)
    assert snapshot(study) == before
    assert not (study / "repeat_01" / "report.json").exists()


@pytest.mark.parametrize(
    "relative",
    [
        "repeat_01/requests/pool_01.json",
        "repeat_02/private.json",
        "repeat_01/baselines.json",
        "repeat_02/manifest.json",
        "historical/report.json",
        "host-instructions.md",
        "protocol.json",
        "implementation/repeat_metrics.py",
    ],
)
def test_immutable_input_tamper_blocks_observe_apply_and_report(study, relative):
    request = observe_repetition(study, "repeat_01", "pool_01")
    path = study / relative
    path.write_bytes(path.read_bytes() + b" ")
    before = snapshot(study)
    for operation in (
        lambda: observe_repetition(study, "repeat_01", "pool_01"),
        lambda: apply_repetition(
            study, "repeat_01", "pool_01", submission_for(request, "fresh-one")
        ),
        lambda: report_repetitions(study),
    ):
        with pytest.raises(ValueError, match="checksum"):
            operation()
        assert snapshot(study) == before


@pytest.mark.parametrize(
    ("trial", "pool"),
    [
        ("historical", "pool_01"),
        ("../historical", "pool_01"),
        ("repeat_03", "pool_01"),
        ("repeat_01", "pool_07"),
        ("repeat_01", "../pool_01"),
    ],
)
def test_unknown_or_traversal_paths_are_rejected(study, trial, pool):
    request = observe_repetition(study, "repeat_01", "pool_01")
    before = snapshot(study)
    with pytest.raises(ValueError, match="unknown repetition or pool"):
        observe_repetition(study, trial, pool)
    with pytest.raises(ValueError, match="unknown repetition or pool"):
        apply_repetition(study, trial, pool, submission_for(request, "fresh-one"))
    assert snapshot(study) == before


def _assert_all_operations_reject_modified_routing(study, request):
    before = snapshot(study, include_locks=True)
    for operation in (
        lambda: observe_repetition(study, "repeat_01", "pool_01"),
        lambda: apply_repetition(
            study, "repeat_01", "pool_01", submission_for(request, "routing-test-agent")
        ),
        lambda: report_repetitions(study),
    ):
        with pytest.raises(ValueError, match="routing differs from frozen protocol"):
            operation()
        assert snapshot(study, include_locks=True) == before
    assert not (study / "report.json").exists()
    for trial in ("repeat_01", "repeat_02"):
        assert not (study / trial / "report.json").exists()


@pytest.mark.parametrize("trials", [["repeat_01"], ["repeat_01", "../escape"]])
def test_modified_manifest_trials_cannot_shrink_or_redirect_frozen_study(study, trials):
    # A completed first trial must remain unrevealed when a pending trial is removed.
    commit_trial(study, "repeat_01", (0, 2))
    request = observe_repetition(study, "repeat_01", "pool_01")
    manifest = read(study / "manifest.json")
    manifest["trials"] = trials
    write_json(study / "manifest.json", manifest)
    _assert_all_operations_reject_modified_routing(study, request)


@pytest.mark.parametrize("pools", [["pool_01"], ["pool_01", "../escape"]])
def test_modified_manifest_pools_cannot_shrink_or_redirect_frozen_study(study, pools):
    commit_all(study)
    request = observe_repetition(study, "repeat_01", "pool_01")
    manifest = read(study / "manifest.json")
    manifest["pools"] = pools
    write_json(study / "manifest.json", manifest)
    _assert_all_operations_reject_modified_routing(study, request)


@pytest.mark.parametrize("repetitions", [0, 1, 6, True, False, 2.0, "2", None])
def test_invalid_repetition_counts_rejected_before_creating_output(
    tmp_path, completed_source, repetitions
):
    instructions = tmp_path / "instructions.md"
    instructions.write_text("Synthetic instructions.")
    output = tmp_path / "study"
    with pytest.raises(ValueError, match="repetitions"):
        prepare_repetitions(completed_source, instructions, output, repetitions=repetitions)
    assert not output.exists()


def test_preparation_requires_completed_source_nonempty_instructions_and_fresh_output(
    tmp_path, completed_source
):
    instructions = tmp_path / "instructions.md"
    instructions.write_text("Synthetic instructions.")
    incomplete = tmp_path / "incomplete"
    shutil.copytree(completed_source, incomplete)
    (incomplete / "report.json").unlink()
    output = tmp_path / "study"
    with pytest.raises(ValueError, match="completed benchmark"):
        prepare_repetitions(incomplete, instructions, output)
    assert not output.exists()
    instructions.write_text(" \n ")
    with pytest.raises(ValueError, match="nonempty host instructions"):
        prepare_repetitions(completed_source, instructions, output)
    assert not output.exists()
    instructions.write_text("Synthetic instructions.")
    output.mkdir()
    with pytest.raises(ValueError, match="fresh directory"):
        prepare_repetitions(completed_source, instructions, output)


@pytest.mark.parametrize("absolute", [False, True])
def test_malformed_source_record_cannot_copy_back_over_source(tmp_path, completed_source, absolute):
    source = tmp_path / "source"
    shutil.copytree(completed_source, source)
    manifest = read(source / "manifest.json")
    record = manifest["files"][0]
    relative = record["path"]
    record["path"] = str(source / relative) if absolute else f"../source/{relative}"
    write_json(source / "manifest.json", manifest)
    before = snapshot(source, include_locks=True)
    instructions = tmp_path / "instructions.md"
    instructions.write_text("Synthetic instructions.")
    with pytest.raises(ValueError, match="relative|path|escape"):
        prepare_repetitions(source, instructions, tmp_path / "study")
    assert snapshot(source, include_locks=True) == before


def test_cli_help_exposes_standalone_repetition_commands(capsys):
    with pytest.raises(SystemExit) as stopped:
        main(["--help"])
    assert stopped.value.code == 0
    output = capsys.readouterr()
    assert "{prepare,observe,apply,report}" in output.out
    assert output.err == ""
    completed = subprocess.run(
        [sys.executable, "-m", "interaction_design.benchmark_repeats", "--help"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0
    assert "{prepare,observe,apply,report}" in completed.stdout
    assert completed.stderr == ""

"""Synthetic-only integrity and end-to-end tests of the external benchmark boundary."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from interaction_design.benchmark import (
    FEATURES,
    REVISION,
    TARGETS,
    apply_benchmark,
    baseline_selections,
    observe_benchmark,
    prepare_benchmark,
    report_benchmark,
    validate_submission,
)
from interaction_design.cli import main as cli_main
from interaction_design.manifest import canonical_sha256
from interaction_design.persistence import write_json


def read(path):
    return json.loads(path.read_text())


@pytest.fixture
def protocol():
    path = Path(__file__).parents[1] / "config" / "external-benchmark-v1.json"
    value = read(path)
    value["quota"] = 2
    return value


@pytest.fixture
def synthetic_data():
    scores = [
        [0.912345678, 0.2, 0.4, 0.7],
        [0.5, 0.9, 0.5, 0.8],
        [0.1, 0.6, 0.8, 0.2],
    ]
    rows = []
    for target in (*TARGETS, "PD-L1"):
        for index in range(3):
            rows.append(
                {
                    "uuid": f"synthetic-{target}-{index}",
                    "target": target,
                    "label": index != 1,
                    "adaptyv_binding": ["binder", "not_expressed", "inconclusive"][index],
                    "twist_binding": [None, "non_binder", "not_measured"][index],
                    "scores": dict(zip(FEATURES, scores[index], strict=True)),
                }
            )
    return {
        "version": "anthropic-binder-import-v1",
        "source": {"revision": REVISION, "repository": "synthetic-fixture"},
        "rows": rows,
        "qc": {"origin": "synthetic test data"},
    }


@pytest.fixture
def make_session(tmp_path, monkeypatch, synthetic_data, protocol):
    monkeypatch.setattr("interaction_design.benchmark.secrets.token_hex", lambda _: "ab" * 16)
    count = 0

    def create(data=None, settings=None):
        nonlocal count
        base = tmp_path / f"synthetic_{count}"
        count += 1
        imported = write_json(base / "input.json", synthetic_data if data is None else data)
        protocol_path = write_json(
            base / "protocol.json", protocol if settings is None else settings
        )
        output = base / "session"
        result = prepare_benchmark(imported, protocol_path, output)
        assert result["status"] == "awaiting_selections"
        assert len(result["requests"]) == 6
        return output

    return create


@pytest.fixture
def session(make_session):
    return make_session()


def submission_for(request, ids=None):
    rows = request["payload"]["candidates"]
    if ids is None:
        ordered = sorted(
            rows,
            key=lambda row: -(row["scores"]["ef2_iptm"] or 0),
        )
        ids = [row["candidate_id"] for row in ordered[: request["payload"]["quota"]]]
    by_id = {row["candidate_id"]: row for row in rows}
    evidence = []
    for candidate_id in ids:
        field, value = next(
            (field, value)
            for field, value in by_id[candidate_id]["scores"].items()
            if value is not None
        )
        evidence.append({"candidate_id": candidate_id, "field": field, "value": value})
    return {
        "request_sha256": request["request_sha256"],
        "candidate_ids": list(ids),
        "reason": "Synthetic selection based only on the supplied computational scores.",
        "evidence": evidence,
        "actor": {"harness": "pytest-synthetic", "model": None, "agent_id": "test-agent"},
    }


def commit_all(session):
    receipts = {}
    for index in range(1, 7):
        pool_id = f"pool_{index:02d}"
        request = observe_benchmark(session, pool_id)
        receipts[pool_id] = apply_benchmark(session, pool_id, submission_for(request))
    return receipts


def snapshot(session):
    # The advisory lock file is not workflow state and can appear on failed attempts.
    return {
        str(path.relative_to(session)): path.read_bytes()
        for path in session.rglob("*")
        if path.is_file() and path.name != ".lock"
    }


def test_prepare_and_observe_expose_only_anonymous_rounded_computational_features(
    session, synthetic_data
):
    request = observe_benchmark(session, "pool_01")
    assert set(request) == {"payload", "request_sha256"}
    assert request["request_sha256"] == canonical_sha256(request["payload"])
    payload = request["payload"]
    assert payload["pool_id"] == "pool_01"
    assert payload["quota"] == 2
    assert len(payload["candidates"]) == 3
    assert set(payload["feature_definitions"]) == set(FEATURES)
    assert max(row["scores"]["ef2_iptm"] for row in payload["candidates"]) == 0.912346
    for row in payload["candidates"]:
        assert set(row) == {"candidate_id", "scores"}
        assert row["candidate_id"].startswith("c_")
        assert set(row["scores"]) == set(FEATURES)

    serialized = json.dumps(request)
    for forbidden in ("uuid", "target", "label", "adaptyv_binding", "twist_binding", "source"):
        assert f'"{forbidden}"' not in serialized
    for row in synthetic_data["rows"]:
        assert row["uuid"] not in serialized
        assert row["target"] not in serialized
    assert not (session / "report.json").exists()


def test_evaluation_label_permutations_leave_requests_baselines_and_development_unchanged(
    make_session, synthetic_data
):
    permuted = deepcopy(synthetic_data)
    for target in TARGETS:
        rows = [row for row in permuted["rows"] if row["target"] == target]
        labels = [row["label"] for row in rows]
        for row, label in zip(rows, labels[1:] + labels[:1], strict=True):
            row["label"] = label
    original_session = make_session()
    permuted_session = make_session(permuted)
    for pool_id in read(original_session / "manifest.json")["requests"]:
        assert observe_benchmark(original_session, pool_id) == observe_benchmark(
            permuted_session, pool_id
        )
    assert read(original_session / "baselines.json") == read(permuted_session / "baselines.json")
    development = read(original_session / "development.json")
    assert development == read(permuted_session / "development.json")
    assert development["best_single"] == "ef2_iptm"


def test_null_composite_labels_are_excluded_and_counted(make_session, synthetic_data):
    unknown = deepcopy(synthetic_data["rows"][0])
    unknown.update(uuid="synthetic-unlabeled", label=None)
    synthetic_data["rows"].append(unknown)
    session = make_session(synthetic_data)
    cohort = read(session / "cohort.json")
    assert cohort["included"][TARGETS[0]] == 3
    assert cohort["excluded_null_labels"] == {TARGETS[0]: 1}
    assert len(observe_benchmark(session, "pool_01")["payload"]["candidates"]) == 3


def test_null_scores_rank_last_and_midrank_ties_use_anonymous_id():
    rows = [
        {"candidate_id": "c_z", "scores": dict.fromkeys(FEATURES)},
        {"candidate_id": "c_b", "scores": dict.fromkeys(FEATURES, 0.0)},
        {"candidate_id": "c_a", "scores": dict.fromkeys(FEATURES, 0.0)},
    ]
    expected = {
        name: ["c_a", "c_b"]
        for name in (
            "fixed",
            "development_best_single",
            "heuristic",
            "ef2_iptm",
            "ef2_ipsae",
            "ensemble_ipsae",
        )
    }
    assert baseline_selections(rows, 2, "ef2_sc_dockq") == expected
    assert baseline_selections(list(reversed(rows)), 2, "ef2_sc_dockq") == expected


def test_rank_heuristic_uses_all_four_features_and_fixed_is_independent_of_scores():
    rows = [
        {"candidate_id": "c_a", "scores": dict(zip(FEATURES, [1.0, 0.1, 0.1, 0.1], strict=True))},
        {"candidate_id": "c_b", "scores": dict.fromkeys(FEATURES, 0.8)},
        {"candidate_id": "c_c", "scores": dict.fromkeys(FEATURES, 0.5)},
    ]
    result = baseline_selections(rows, 1, "ef2_iptm")
    assert result["fixed"] == ["c_a"]
    assert result["ef2_iptm"] == ["c_a"]
    assert result["development_best_single"] == ["c_a"]
    assert result["heuristic"] == ["c_b"]


@pytest.mark.parametrize(
    "defect",
    [
        "too_few",
        "too_many",
        "duplicate",
        "unknown",
        "nonstring_id",
        "stale_hash",
        "extra_field",
        "missing_evidence",
        "extra_evidence_field",
        "hidden_evidence_field",
        "unselected_evidence",
        "wrong_evidence_value",
        "nan",
        "infinity",
        "boolean_score",
        "blank_reason",
        "blank_harness",
        "extra_actor_field",
    ],
)
def test_invalid_submissions_are_rejected_without_changing_session_state(session, defect):
    request = observe_benchmark(session, "pool_01")
    submission = submission_for(request)
    unselected = next(
        row["candidate_id"]
        for row in request["payload"]["candidates"]
        if row["candidate_id"] not in submission["candidate_ids"]
    )
    if defect == "too_few":
        submission["candidate_ids"].pop()
    elif defect == "too_many":
        submission["candidate_ids"].append(unselected)
    elif defect == "duplicate":
        submission["candidate_ids"][1] = submission["candidate_ids"][0]
    elif defect == "unknown":
        submission["candidate_ids"][0] = "c_unknown"
    elif defect == "nonstring_id":
        submission["candidate_ids"][0] = []
    elif defect == "stale_hash":
        submission["request_sha256"] = "0" * 64
    elif defect == "extra_field":
        submission["label"] = True
    elif defect == "missing_evidence":
        submission["evidence"].pop()
    elif defect == "extra_evidence_field":
        submission["evidence"][0]["label"] = True
    elif defect == "hidden_evidence_field":
        submission["evidence"][0]["field"] = "label"
    elif defect == "unselected_evidence":
        submission["evidence"][0]["candidate_id"] = unselected
    elif defect == "wrong_evidence_value":
        submission["evidence"][0]["value"] += 0.000001
    elif defect == "nan":
        submission["evidence"][0]["value"] = float("nan")
    elif defect == "infinity":
        submission["evidence"][0]["value"] = float("inf")
    elif defect == "boolean_score":
        submission["evidence"][0]["value"] = True
    elif defect == "blank_reason":
        submission["reason"] = "  "
    elif defect == "blank_harness":
        submission["actor"]["harness"] = "  "
    elif defect == "extra_actor_field":
        submission["actor"]["verified"] = True
    before = snapshot(session)
    with pytest.raises(ValueError):
        validate_submission(request, submission)
    with pytest.raises(ValueError):
        apply_benchmark(session, "pool_01", submission)
    assert snapshot(session) == before


def test_missing_score_cannot_be_used_as_numeric_evidence(make_session, synthetic_data):
    synthetic_data["rows"][0]["scores"]["ef2_ipsae"] = None
    session = make_session(synthetic_data)
    request = observe_benchmark(session, "pool_01")
    submission = submission_for(request)
    candidate = max(request["payload"]["candidates"], key=lambda row: row["scores"]["ef2_iptm"])
    assert candidate["scores"]["ef2_ipsae"] is None
    submission["evidence"][0] = {
        "candidate_id": candidate["candidate_id"],
        "field": "ef2_ipsae",
        "value": 0.0,
    }
    before = snapshot(session)
    with pytest.raises(ValueError, match="finite visible"):
        apply_benchmark(session, "pool_01", submission)
    assert snapshot(session) == before


def test_reveal_requires_all_six_committed_submissions(session):
    for index in range(1, 7):
        before = snapshot(session)
        with pytest.raises(ValueError, match="all host selections"):
            report_benchmark(session)
        assert snapshot(session) == before
        assert not (session / "report.json").exists()
        pool_id = f"pool_{index:02d}"
        request = observe_benchmark(session, pool_id)
        result = apply_benchmark(session, pool_id, submission_for(request))
        assert result["status"] == "accepted"
    report = report_benchmark(session)
    assert len(report["by_target"]) == 6
    assert len(report["host_submissions"]) == 6


def test_submissions_are_idempotent_and_conflicting_replacements_are_rejected(session):
    request = observe_benchmark(session, "pool_01")
    submission = submission_for(request)
    receipt = apply_benchmark(session, "pool_01", submission)
    before = snapshot(session)
    assert apply_benchmark(session, "pool_01", deepcopy(submission)) == receipt
    assert snapshot(session) == before
    replacement = deepcopy(submission)
    replacement["reason"] = "A different synthetic reason changes the committed submission."
    with pytest.raises(ValueError, match="already committed"):
        apply_benchmark(session, "pool_01", replacement)
    assert snapshot(session) == before


@pytest.mark.parametrize("relative", ["private.json", "requests/pool_01.json", "imported.json"])
def test_artifact_checksum_tampering_blocks_observe_apply_and_reveal(session, relative):
    request = observe_benchmark(session, "pool_01")
    path = session / relative
    path.write_text(path.read_text() + " ")
    before = snapshot(session)
    for operation in (
        lambda: observe_benchmark(session, "pool_01"),
        lambda: apply_benchmark(session, "pool_01", submission_for(request)),
        lambda: report_benchmark(session),
    ):
        with pytest.raises(ValueError, match="checksum"):
            operation()
        assert snapshot(session) == before


@pytest.mark.parametrize("recompute_self_hash", [False, True])
def test_mutated_receipt_is_rejected_even_with_recomputed_embedded_hash(
    session, recompute_self_hash
):
    commit_all(session)
    path = session / "submissions" / "pool_01.json"
    receipt = read(path)
    receipt["submission"]["reason"] = "Mutated after commitment."
    if recompute_self_hash:
        receipt["submission_sha256"] = canonical_sha256(receipt["submission"])
    write_json(path, receipt)
    before = snapshot(session)
    with pytest.raises(ValueError, match="checksum|commit"):
        report_benchmark(session)
    assert snapshot(session) == before
    assert not (session / "report.json").exists()


def test_commitment_records_cannot_be_swapped_between_pools(session):
    commit_all(session)
    path = session / "commitments.json"
    commitments = read(path)
    commitments["pool_01"], commitments["pool_02"] = (
        commitments["pool_02"],
        commitments["pool_01"],
    )
    write_json(path, commitments)
    before = snapshot(session)
    request = observe_benchmark(session, "pool_01")
    with pytest.raises(ValueError, match="commitment path does not match its pool"):
        apply_benchmark(session, "pool_01", submission_for(request))
    with pytest.raises(ValueError, match="commitment path does not match its pool"):
        report_benchmark(session)
    assert snapshot(session) == before
    assert not (session / "report.json").exists()


def test_reveal_rejects_an_extra_commitment_pool(session):
    commit_all(session)
    path = session / "commitments.json"
    commitments = read(path)
    commitments["pool_07"] = commitments["pool_01"]
    write_json(path, commitments)
    before = snapshot(session)
    with pytest.raises(ValueError, match="commitment pool set does not match requests"):
        report_benchmark(session)
    assert snapshot(session) == before
    assert not (session / "report.json").exists()


@pytest.mark.parametrize("target", [TARGETS[0], "PD-L1"])
@pytest.mark.parametrize("label", [True, False])
def test_prepare_rejects_an_eligible_candidate_without_any_finite_evidence(
    tmp_path, synthetic_data, protocol, target, label
):
    candidate = next(row for row in synthetic_data["rows"] if row["target"] == target)
    candidate["label"] = label
    candidate["scores"] = dict.fromkeys(FEATURES)
    imported = write_json(tmp_path / "import.json", synthetic_data)
    settings = write_json(tmp_path / "protocol.json", protocol)
    output = tmp_path / "session"
    with pytest.raises(ValueError, match="at least one finite evidence field"):
        prepare_benchmark(imported, settings, output)
    assert not output.exists()


@pytest.mark.parametrize("explicit_argv", [True, False])
def test_cli_benchmark_help_reaches_benchmark_subcommands(capsys, monkeypatch, explicit_argv):
    arguments = ["benchmark", "--help"]
    monkeypatch.setattr("sys.argv", ["interaction-design", *arguments])
    with pytest.raises(SystemExit) as stopped:
        cli_main(arguments if explicit_argv else None)
    assert stopped.value.code == 0
    output = capsys.readouterr()
    assert "{prepare,observe,apply,report}" in output.out
    assert "retrospective external candidate evaluation" in output.out
    assert output.err == ""


def test_report_handles_zero_positive_targets_and_excludes_undefined_ranking_metrics(
    make_session, synthetic_data
):
    for row in synthetic_data["rows"]:
        if row["target"] == TARGETS[0]:
            row["label"] = False
        if row["target"] == TARGETS[1]:
            row["scores"]["ef2_ipsae"] = None
        if row["uuid"] == f"synthetic-{TARGETS[2]}-0":
            row["scores"]["ef2_iptm"] = None
    session = make_session(synthetic_data)
    accepted = commit_all(session)
    report = report_benchmark(session)
    zero = report["by_target"][TARGETS[0]]
    assert zero["n"] == 3
    assert zero["positives"] == 0
    assert zero["random_expected_hits"] == 0.0
    for metrics in zero["policies"].values():
        assert metrics["hits"] == 0
        assert metrics["precision"] == 0.0
        assert metrics["recall"] is None
        assert metrics["enrichment"] is None
    for metrics in zero["ranking"].values():
        assert metrics["auroc"] is None
        assert metrics["average_precision"] is None
    absent = report["by_target"][TARGETS[1]]["ranking"]["ef2_ipsae"]
    assert absent == {
        "n": 0,
        "positives": 0,
        "auroc": None,
        "average_precision": None,
        "missing": 3,
    }
    partial = report["by_target"][TARGETS[2]]["ranking"]["ef2_iptm"]
    assert partial["n"] == 2
    assert partial["missing"] == 1
    assert partial["positives"] == 1
    assert report["macro_ranking"]["ef2_ipsae"]["average_precision"]["n"] == 4
    assert report["macro_ranking"]["ef2_ipsae"]["auroc"]["n"] == 4
    assert report["macro_ranking"]["ef2_iptm"]["average_precision"]["n"] == 5
    assert report["macro_precision"]["harness"]["n"] == 6
    assert report["host_submissions"] == {
        pool_id: receipt["submission_sha256"] for pool_id, receipt in accepted.items()
    }
    assert set(report["paired_precision_differences"]) == {"heuristic", "development_best_single"}
    assert report["new_gpu_inference_runs"] == 0
    assert report["new_wetlab_experiments"] == 0
    assert report["actor_metadata_verified"] is False
    assert report["host_token_usage"] is None
    assert read(session / "report.json") == report
    before = snapshot(session)
    assert report_benchmark(session) == report
    request = observe_benchmark(session, "pool_01")
    assert apply_benchmark(session, "pool_01", submission_for(request)) == accepted["pool_01"]
    assert snapshot(session) == before


def test_vendor_missing_statuses_are_excluded_instead_of_counted_as_negatives(session):
    commit_all(session)
    report = report_benchmark(session)
    for target in report["by_target"].values():
        adaptyv = target["vendor_sensitivity"]["adaptyv"]["harness"]
        assert adaptyv == {
            "selected": 1,
            "hits": 1,
            "precision": 1.0,
            "recall": 1.0,
            "enrichment": 1.0,
            "pool_size": 1,
            "pool_positives": 1,
            "selected_unknown": 1,
        }
        twist = target["vendor_sensitivity"]["twist"]["harness"]
        assert twist == {
            "selected": 1,
            "hits": 0,
            "precision": 0.0,
            "recall": None,
            "enrichment": None,
            "pool_size": 1,
            "pool_positives": 0,
            "selected_unknown": 1,
        }


@pytest.mark.parametrize(
    "missing_status", [None, "not_expressed", "not_measured", "not_tested", "inconclusive"]
)
def test_all_vendor_missing_statuses_remain_unknown(make_session, synthetic_data, missing_status):
    for row in synthetic_data["rows"]:
        row["adaptyv_binding"] = missing_status
    session = make_session(synthetic_data)
    commit_all(session)
    report = report_benchmark(session)
    for target in report["by_target"].values():
        metrics = target["vendor_sensitivity"]["adaptyv"]["harness"]
        assert metrics["pool_size"] == 0
        assert metrics["selected"] == 0
        assert metrics["selected_unknown"] == 2
        assert metrics["precision"] is None
        assert metrics["recall"] is None


def test_changed_revealed_report_is_rejected(session):
    commit_all(session)
    report = report_benchmark(session)
    report["new_wetlab_experiments"] = 1
    write_json(session / "report.json", report)
    before = snapshot(session)
    with pytest.raises(ValueError, match="revealed report differs"):
        report_benchmark(session)
    assert snapshot(session) == before


def test_prepare_requires_a_fresh_output_directory(session, tmp_path, protocol, synthetic_data):
    imported = write_json(tmp_path / "second_import.json", synthetic_data)
    settings = write_json(tmp_path / "second_protocol.json", protocol)
    before = snapshot(session)
    with pytest.raises(ValueError, match="fresh directory"):
        prepare_benchmark(imported, settings, session)
    assert snapshot(session) == before


def test_changed_heuristic_protocol_rule_is_rejected_before_output_creation(
    tmp_path, protocol, synthetic_data
):
    protocol["heuristic"] = "Use only ef2_iptm and ignore all other computational scores."
    imported = write_json(tmp_path / "import.json", synthetic_data)
    settings = write_json(tmp_path / "protocol.json", protocol)
    output = tmp_path / "session"
    with pytest.raises(ValueError, match="unsupported benchmark protocol rules"):
        prepare_benchmark(imported, settings, output)
    assert not output.exists()


def test_development_null_scores_remain_in_full_pool_average_precision(
    make_session, synthetic_data
):
    development_rows = [row for row in synthetic_data["rows"] if row["target"] == "PD-L1"]
    # ipTM has one observed positive and a tied missing group with one positive
    # and one negative: AP = (1/2)(1) + (1/2)(2/3) = 5/6, not 1 from deletion.
    for row in development_rows[1:]:
        row["scores"]["ef2_iptm"] = None
    for row, score in zip(development_rows, [0.9, 0.1, 0.8], strict=True):
        row["scores"]["ef2_sc_dockq"] = score
    session = make_session(synthetic_data)
    development = read(session / "development.json")
    metrics = development["metrics"]["ef2_iptm"]
    assert metrics["n"] == 3
    assert metrics["positives"] == 2
    assert metrics["missing"] == 2
    assert metrics["missing_order"] == "tied bottom group"
    assert metrics["auroc"] == 0.75
    assert metrics["average_precision"] == pytest.approx(5 / 6)
    assert development["metrics"]["ef2_sc_dockq"]["average_precision"] == 1.0
    assert development["best_single"] == "ef2_sc_dockq"

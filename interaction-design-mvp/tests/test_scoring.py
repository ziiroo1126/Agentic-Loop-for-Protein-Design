from __future__ import annotations

import copy
import json

from evedesign.system import EntityInstance, Protein, System, SystemInstance

from interaction_design.scoring import (
    AF3ConfidenceScorer,
    PyRosettaScorer,
    extract_af3_metrics,
    rank_instances,
)
from interaction_design.specs import EvaluationSpec


def af3_payload(iptm=0.60, binder_ptm=0.50, ipae=12.0):
    return {
        "iptm": iptm,
        "ptm": 0.70,
        "chain_iptm": [0.8, iptm],
        "chain_ptm": [0.75, binder_ptm],
        "chain_pair_pae_min": [[0.0, ipae], [ipae, 0.0]],
    }


def test_af3_metric_extraction_matches_pipeline_definitions():
    metrics = extract_af3_metrics(af3_payload(), binder_index=1)
    assert metrics == {
        "iptm": 0.60,
        "binder_ptm": 0.50,
        "complex_ptm": 0.70,
        "chain_ptm_avg": 0.625,
        "ipae_min": 12.0,
        "ipae_avg": 12.0,
    }


def test_scorers_do_not_mutate_inputs_and_thresholds_are_inclusive(tmp_path):
    af3_path = tmp_path / "candidate.af3.json"
    rosetta_path = tmp_path / "candidate.rosetta.json"
    af3_path.write_text(json.dumps(af3_payload()), encoding="utf-8")
    rosetta_path.write_text(
        json.dumps(
            {
                "ddg": -44.0,
                "sap_score": 40.0,
                "contact_molecular_surface": 400.0,
            }
        ),
        encoding="utf-8",
    )
    system = System([Protein(id="target", rep="AAAA"), Protein(id="binder", rep="AAAA")])
    original = SystemInstance(
        [EntityInstance(rep="AAAA"), EntityInstance(rep="AAAA")],
        id="candidate",
        metadata={
            "artifacts": {
                "af3_summary": str(af3_path),
                "rosetta_metrics": str(rosetta_path),
            }
        },
    )
    snapshot = copy.deepcopy(original.metadata)
    scored = AF3ConfidenceScorer(1).build(system).score([original])
    scored = PyRosettaScorer().build(system).score(scored)
    ranked = rank_instances(scored, EvaluationSpec(binder_molecule="binder"))

    assert original.metadata == snapshot
    assert ranked[0].metadata["ranking"]["threshold_pass"] is True
    assert all(ranked[0].metadata["ranking"]["thresholds"].values())


def test_multi_chain_af3_uses_binder_chain_iptm():
    payload = {
        "iptm": 0.1,
        "ptm": 0.7,
        "chain_iptm": [0.4, 0.88, 0.5],
        "chain_ptm": [0.7, 0.8, 0.6],
        "chain_pair_pae_min": [
            [0, 5, 9],
            [6, 0, 7],
            [10, 8, 0],
        ],
    }
    assert extract_af3_metrics(payload, binder_index=1)["iptm"] == 0.88

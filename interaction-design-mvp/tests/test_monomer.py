from __future__ import annotations

import importlib
from pathlib import Path

import numpy as np
import pytest

from interaction_design.assets import sha256_file
from interaction_design.evaluation.monomer import load_monomer_job, prepare_monomer_batch
from interaction_design.evaluation.monomer_report import aligned_rmsd, select_representatives
from interaction_design.generator import ODesignGenerator
from interaction_design.persistence import write_json
from interaction_design.runtime import MockODesignExecutor
from interaction_design.specs import InteractionDesignSpec
from interaction_design.workflow import DesignWorkflow


@pytest.fixture
def monomer_generation(tmp_path):
    spec = InteractionDesignSpec.model_validate(
        {
            "name": "synthetic_monomer_batch",
            "model": "odesign_base_prot_flex",
            "design_modality": "protein",
            "reference_structure": str(tmp_path / "mock_reference.cif"),
            "molecules": [
                {
                    "id": "target",
                    "type": "protein",
                    "role": "context",
                    "segments": [{"kind": "fixed", "chain": "A", "start": 1, "end": 8}],
                },
                {
                    "id": "binder",
                    "type": "protein",
                    "role": "design",
                    "segments": [{"kind": "generated", "min_length": 5, "max_length": 5}],
                },
            ],
            "generation": {"seeds": [1, 2]},
            "evaluation": {"binder_molecule": "binder"},
        }
    )
    return DesignWorkflow(ODesignGenerator(MockODesignExecutor(), tmp_path / "runs")).run(
        spec, evaluate=False
    )


def test_batch_preparation_preserves_generation_and_selects_binder_b(monomer_generation):
    run = monomer_generation.run_dir
    before = (run / "manifest.json").read_bytes()
    job = prepare_monomer_batch(run)
    manifest = load_monomer_job(job)
    assert len(manifest["requests"]) == 2
    import json

    for row in manifest["requests"]:
        request = json.loads((job / row["path"]).read_text())
        assert request["reference"]["chain"] == "B"
        assert len(request["reference_ca"]) == len(request["sequence"]) == 5
        assert len(request["context_sequences"]["target"]) == 8
    assert (run / "manifest.json").read_bytes() == before


def test_monomer_rejects_changed_input_and_reference(monomer_generation):
    job = prepare_monomer_batch(monomer_generation.run_dir)
    manifest = load_monomer_job(job)
    request = job / manifest["requests"][0]["path"]
    request.write_text(request.read_text() + " ")
    with pytest.raises(ValueError, match="checksum"):
        load_monomer_job(job)
    second = prepare_monomer_batch(monomer_generation.run_dir)
    import json

    data = json.loads((second / load_monomer_job(second)["requests"][0]["path"]).read_text())
    reference = Path(data["reference"]["path"])
    reference.write_text(reference.read_text() + "\n")
    with pytest.raises(ValueError, match="reference structure changed"):
        load_monomer_job(second)


def test_frozen_count_and_seed_policy_are_enforced(monomer_generation, tmp_path):
    protocol = write_json(
        tmp_path / "protocol.json",
        {"candidate_count": 16, "seeds": list(range(43, 59)), "fixed": {"binder_length": 5}},
    )
    with pytest.raises(ValueError, match="candidate count"):
        prepare_monomer_batch(monomer_generation.run_dir, protocol)
    write_json(protocol, {"candidate_count": 2, "seeds": [43, 44], "fixed": {"binder_length": 5}})
    with pytest.raises(ValueError, match="seeds"):
        prepare_monomer_batch(monomer_generation.run_dir, protocol)


def test_alignment_and_fixed_representative_selection():
    points = np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0], [0, 0, 3]], dtype=float)
    rotation = np.array([[0, -1, 0], [1, 0, 0], [0, 0, 1]])
    assert aligned_rmsd(points, points @ rotation + [10, -4, 2]) < 1e-5
    reflected = points.copy()
    reflected[:, 0] *= -1
    assert aligned_rmsd(points, reflected) > 0.1
    rows = [
        {"candidate_id": name, "mean_plddt_ca": score}
        for name, score in [("z", 90), ("a", 90), ("mid", 70), ("low", 20)]
    ]
    assert [x["candidate_id"] for x in select_representatives(rows, [0, 2, 3])] == [
        "a",
        "mid",
        "low",
    ]


def test_batch_loads_once_and_retains_partial_failure(tmp_path, monkeypatch):
    # Synthetic worker outputs are confined to this test's temporary directory.
    monkeypatch.syspath_prepend(str(Path(__file__).parents[1] / "scripts"))
    worker = importlib.import_module("esmfold_batch")
    requests = []
    for index in range(3):
        path = write_json(
            tmp_path / f"request{index}.json",
            {"candidate_id": f"synthetic{index}", "sequence": "AAAA"},
        )
        requests.append(
            {"candidate_id": f"synthetic{index}", "path": path.name, "sha256": sha256_file(path)}
        )
    manifest = write_json(tmp_path / "requests.json", {"requests": requests})
    calls = []

    def load(args):
        calls.append("load")
        return object(), {}, 3.0

    def predict(args, model, metadata, **kwargs):
        calls.append(args.input.name)
        if args.input.name == "request1.json":
            raise RuntimeError("deliberate synthetic failure")
        write_json(args.output_dir / "metrics.json", {"synthetic": True})
        return {"timing": {"inference_seconds": 1.0}}

    monkeypatch.setattr(worker, "load_model", load)
    monkeypatch.setattr(worker, "predict", predict)
    monkeypatch.setattr(
        "sys.argv",
        [
            "esmfold_batch",
            "--model-dir",
            str(tmp_path),
            "--requests",
            str(manifest),
            "--output-dir",
            str(tmp_path / "out"),
        ],
    )
    with pytest.raises(RuntimeError, match="deliberate"):
        worker.main()
    import json

    status = json.loads((tmp_path / "out/status.json").read_text())
    assert calls == ["load", "request0.json", "request1.json"]
    assert status["status"] == "failed" and len(status["candidates"]) == 1
    assert status["failure"]["next_request_index"] == 1
    assert json.loads((tmp_path / "out/manifest.json").read_text())["completed"] == 1

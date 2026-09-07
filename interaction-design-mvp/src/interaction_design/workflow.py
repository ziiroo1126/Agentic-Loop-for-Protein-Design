"""End-to-end generation, evaluation, ranking, and reporting."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from evedesign.system import SystemInstance

from interaction_design.assets import (
    AssetPin,
    default_asset_lock,
    load_asset_pins,
    load_odesign_revision,
    sha256_file,
    task_asset_pins,
)
from interaction_design.conversion import spec_to_system
from interaction_design.generator import ODesignGenerator
from interaction_design.manifest import _artifact_records, canonical_sha256, write_run_manifest
from interaction_design.outputs import parse_odesign_outputs
from interaction_design.persistence import write_json
from interaction_design.report import write_reports
from interaction_design.scoring import (
    AF3ConfidenceScorer,
    PyRosettaScorer,
    rank_instances,
)
from interaction_design.specs import InteractionDesignSpec


@dataclass(frozen=True)
class WorkflowResult:
    run_dir: Path
    instances: list[SystemInstance]
    report_json: Path
    report_markdown: Path
    manifest: Path


class DesignWorkflow:
    def __init__(
        self,
        generator: ODesignGenerator,
        *,
        asset_pins: list[AssetPin] | None = None,
        asset_lock: str | Path | None = None,
        project_root: str | Path | None = None,
    ) -> None:
        self.generator = generator
        self.project_root = Path(project_root or Path(__file__).parents[2]).resolve()
        if asset_pins is not None and asset_lock is not None:
            raise ValueError("provide asset_pins or asset_lock, not both")
        selected_lock = Path(asset_lock or default_asset_lock())
        self.asset_pins = asset_pins if asset_pins is not None else load_asset_pins(selected_lock)
        self.expected_odesign_revision = load_odesign_revision(selected_lock)

    def run(
        self,
        spec: InteractionDesignSpec,
        *,
        num_designs: int | None = None,
        evaluate: bool = True,
    ) -> WorkflowResult:
        system = spec_to_system(spec)
        self.generator.build(system, spec)
        requested = (
            num_designs
            if num_designs is not None
            else (
                len(spec.generation.seeds)
                * spec.generation.samples_per_seed
                * spec.generation.inverse_fold_topk
            )
        )
        if requested < 1:
            raise ValueError("num_designs must be at least 1")
        pins = task_asset_pins(self.asset_pins, spec.model, spec.design_modality)
        design_entities = [
            index for index, molecule in enumerate(spec.molecules) if molecule.role == "design"
        ]
        self.generator.last_run_dir = None
        self.generator.last_execution = None
        try:
            instances = self.generator.generate(
                requested,
                entities=design_entities,
                temperature=spec.generation.inverse_fold_temperature,
            )
        except Exception:
            if self.generator.last_run_dir is not None:
                write_run_manifest(
                    self.generator,
                    spec,
                    [],
                    pins,
                    self.expected_odesign_revision,
                    self.project_root,
                    status="generation_failed",
                )
                write_json(
                    self.generator.last_run_dir / "status.json", {"status": "generation_failed"}
                )
            raise

        assert self.generator.last_run_dir is not None
        report_json, report_markdown = write_reports(
            self.generator.last_run_dir, spec, instances, evaluated=False
        )
        manifest = write_run_manifest(
            self.generator,
            spec,
            instances,
            pins,
            self.expected_odesign_revision,
            self.project_root,
        )
        write_json(self.generator.last_run_dir / "status.json", {"status": "generated"})
        if evaluate:
            return evaluate_run(self.generator.last_run_dir)
        return WorkflowResult(
            run_dir=self.generator.last_run_dir,
            instances=instances,
            report_json=report_json,
            report_markdown=report_markdown,
            manifest=manifest,
        )


def evaluate_run(
    run_dir: str | Path,
    *,
    evaluator_outputs: dict[str, dict[str, str]] | None = None,
    af3_binder_index: int | None = None,
    evaluation_provenance: dict[str, object] | None = None,
) -> WorkflowResult:
    """Score a saved generation without launching a generator.

    Each evaluation has its own report and manifest. Generation records are
    immutable, including when evaluators fail or new sidecars arrive later.
    """

    root = Path(run_dir).resolve()
    generation_manifest = root / "manifest.json"
    payload = json.loads(generation_manifest.read_text(encoding="utf-8"))
    if payload.get("stage", "generation") != "generation":
        raise ValueError("evaluate requires a generation run directory")
    if payload.get("status") == "generation_failed":
        raise ValueError("cannot evaluate a failed generation")
    spec = InteractionDesignSpec.model_validate(payload["task"])
    binder_index = spec.binder_index if af3_binder_index is None else af3_binder_index
    if canonical_sha256(spec.model_dump(mode="json")) != payload["task_sha256"]:
        raise ValueError("saved task does not match the generation manifest checksum")
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    attempt = root / "evaluations" / f"{timestamp}-{uuid.uuid4().hex[:8]}"
    attempt.mkdir(parents=True, exist_ok=False)
    input_records: list[dict[str, object]] = []
    evaluation_manifest = {
        **payload,
        "stage": "evaluation",
        "created_at": datetime.now(UTC).isoformat(),
        "evaluation_id": attempt.name,
        "generation_manifest_sha256": sha256_file(generation_manifest),
        "evaluation_inputs": input_records,
        "evaluation": {
            "mode": "sidecar-import",
            "af3_binder_index": binder_index,
            "runs_external_predictors": False,
            "provenance": evaluation_provenance or {},
        },
    }
    write_json(
        root / "status.json",
        {"status": "evaluating", "evaluation": str(attempt.relative_to(root))},
    )
    try:
        system = spec_to_system(spec)
        instances = parse_odesign_outputs(root / "output", system, spec)
        recorded = {record["path"]: record for record in payload["artifacts"]}
        expected_structures = {
            path
            for path in recorded
            if Path(path).suffix == ".cif" and "predictions" in Path(path).parts
        }
        actual_structures = {
            str(Path(instance.metadata["artifacts"]["structure"]).relative_to(root))
            for instance in instances
        }
        if actual_structures != expected_structures:
            raise ValueError("candidate set differs from the saved generation")
        by_id = {instance.id: instance for instance in instances}
        if len(by_id) != len(instances):
            raise ValueError("duplicate candidate IDs in saved output")
        # ODesign may round up the batch to satisfy the requested candidate count.
        instances = [by_id[candidate_id] for candidate_id in payload["candidate_ids"]]
        if evaluator_outputs is not None:
            if set(evaluator_outputs) != set(payload["candidate_ids"]):
                raise ValueError("evaluator outputs must cover the saved candidate set exactly")
            for instance in instances:
                # Keep generation geometry; imported refolds are a distinct artifact.
                instance.metadata["artifacts"] = {
                    "structure": instance.metadata["artifacts"]["structure"],
                    **evaluator_outputs[instance.id],
                }
                if "structure" in evaluator_outputs[instance.id]:
                    raise ValueError("evaluator outputs cannot replace generation structures")
        for instance in instances:
            for kind, raw in instance.metadata["artifacts"].items():
                path = Path(raw)
                relative = str(path.relative_to(root))
                checksum = sha256_file(path)
                if kind == "structure" and (
                    relative not in recorded or checksum != recorded[relative]["sha256"]
                ):
                    raise ValueError(f"generated structure checksum mismatch: {relative}")
                input_records.append(
                    {
                        "candidate_id": instance.id,
                        "kind": kind,
                        "path": relative,
                        "sha256": checksum,
                        "size": path.stat().st_size,
                    }
                )
        instances = AF3ConfidenceScorer(binder_index).build(system).score(instances)
        instances = PyRosettaScorer().build(system).score(instances)
        instances = rank_instances(instances, spec.evaluation)
        report_json, report_markdown = write_reports(attempt, spec, instances, evaluated=True)
    except Exception as error:
        write_json(
            attempt / "failure.json",
            {"stage": "evaluation", "error_type": type(error).__name__, "message": str(error)},
        )
        write_json(
            attempt / "manifest.json",
            {
                **evaluation_manifest,
                "status": "evaluation_failed",
                "artifacts": _artifact_records(attempt),
            },
        )
        write_json(
            root / "status.json",
            {"status": "evaluation_failed", "evaluation": str(attempt.relative_to(root))},
        )
        raise ValueError(
            f"evaluation failed: {error}; generation preserved at {root}; "
            f"details: {attempt / 'failure.json'}"
        ) from error

    manifest = write_json(
        attempt / "manifest.json",
        {**evaluation_manifest, "status": "completed", "artifacts": _artifact_records(attempt)},
    )
    write_json(
        root / "status.json",
        {"status": "completed", "evaluation": str(attempt.relative_to(root))},
    )
    return WorkflowResult(root, instances, report_json, report_markdown, manifest)

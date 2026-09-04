"""End-to-end generation, evaluation, ranking, and reporting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evedesign.system import SystemInstance

from interaction_design.assets import (
    AssetPin,
    default_asset_lock,
    load_asset_pins,
    load_odesign_revision,
)
from interaction_design.conversion import spec_to_system
from interaction_design.generator import ODesignGenerator
from interaction_design.manifest import write_run_manifest
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
        requested = num_designs or (
            len(spec.generation.seeds)
            * spec.generation.samples_per_seed
            * spec.generation.inverse_fold_topk
        )
        design_entities = [
            index for index, molecule in enumerate(spec.molecules) if molecule.role == "design"
        ]
        instances = self.generator.generate(
            requested,
            entities=design_entities,
            temperature=spec.generation.inverse_fold_temperature,
        )
        if evaluate:
            af3 = AF3ConfidenceScorer(spec.binder_index).build(system)
            instances = af3.score(instances)
            rosetta = PyRosettaScorer().build(system)
            instances = rosetta.score(instances)
            instances = rank_instances(instances, spec.evaluation)

        assert self.generator.last_run_dir is not None
        report_json, report_markdown = write_reports(
            self.generator.last_run_dir, spec, instances, evaluated=evaluate
        )
        manifest = write_run_manifest(
            self.generator,
            spec,
            instances,
            self.asset_pins,
            self.expected_odesign_revision,
            self.project_root,
        )
        return WorkflowResult(
            run_dir=self.generator.last_run_dir,
            instances=instances,
            report_json=report_json,
            report_markdown=report_markdown,
            manifest=manifest,
        )

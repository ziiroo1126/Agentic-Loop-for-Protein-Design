"""Machine-readable and reviewer-readable design reports."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

from evedesign.system import SystemInstance

from interaction_design.specs import InteractionDesignSpec


def _sequences(instance: SystemInstance, spec: InteractionDesignSpec) -> dict[str, str | None]:
    return {
        molecule.id: "".join(map(str, entity.rep)) if entity.rep is not None else None
        for molecule, entity in zip(spec.molecules, instance, strict=True)
    }


def candidate_record(
    instance: SystemInstance, spec: InteractionDesignSpec, run_dir: Path
) -> dict[str, object]:
    metadata = instance.metadata or {}
    artifacts = {}
    for name, raw_path in metadata.get("artifacts", {}).items():
        path = Path(raw_path)
        try:
            artifacts[name] = str(path.relative_to(run_dir))
        except ValueError:
            artifacts[name] = str(path)
    return {
        "id": instance.id,
        "score": instance.score,
        "rank": metadata.get("ranking", {}).get("rank"),
        "threshold_pass": metadata.get("ranking", {}).get("threshold_pass"),
        "sequences": _sequences(instance, spec),
        "evaluations": metadata.get("evaluations", {}),
        "ranking": metadata.get("ranking", {}),
        "artifacts": artifacts,
        "odesign": metadata.get("odesign", {}),
    }


def write_reports(
    run_dir: str | Path,
    spec: InteractionDesignSpec,
    instances: Sequence[SystemInstance],
    evaluated: bool,
) -> tuple[Path, Path]:
    root = Path(run_dir)
    records = [candidate_record(instance, spec, root.resolve()) for instance in instances]
    json_path = root / "report.json"
    json_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "task_name": spec.name,
                "evaluated": evaluated,
                "candidate_count": len(records),
                "candidates": records,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    lines = [
        f"# Interaction-design report: {spec.name}",
        "",
        f"Candidates: {len(records)}  ",
        f"Evaluation and ranking: {'complete' if evaluated else 'not requested'}",
        "",
        "| Rank | Candidate | Pass | Score | Structure |",
        "| ---: | --- | :---: | ---: | --- |",
    ]
    for record in records:
        score = "" if record["score"] is None else f"{record['score']:.5f}"
        rank = record["rank"] or "-"
        passed = record["threshold_pass"]
        passed_label = "-" if passed is None else ("yes" if passed else "no")
        structure = record["artifacts"].get("structure", "")
        lines.append(f"| {rank} | `{record['id']}` | {passed_label} | {score} | `{structure}` |")
    lines.extend(
        [
            "",
            "The complete metric values, threshold decisions, sequences, and artifact paths "
            "are recorded in `report.json`; software, seed, command, model revisions, and "
            "checksums are recorded in `manifest.json`.",
            "",
        ]
    )
    markdown_path = root / "report.md"
    markdown_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, markdown_path

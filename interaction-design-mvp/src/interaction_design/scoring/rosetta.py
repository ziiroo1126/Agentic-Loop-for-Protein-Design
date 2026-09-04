"""PyRosetta interface-quality artifact extraction."""

from __future__ import annotations

from typing import Any

from interaction_design.scoring.base import ArtifactScorer

ROSETTA_METRICS = ("ddg", "sap_score", "contact_molecular_surface")


def extract_rosetta_metrics(payload: dict[str, Any]) -> dict[str, float]:
    try:
        return {name: float(payload[name]) for name in ROSETTA_METRICS}
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid PyRosetta metric schema: {error}") from error


class PyRosettaScorer(ArtifactScorer):
    name = "PyRosetta interface metrics"
    citations = ["doi:10.1093/bioinformatics/btaa339"]
    artifact_key = "rosetta_metrics"
    evaluation_key = "rosetta"

    def parse_metrics(self, payload: dict[str, Any]) -> dict[str, float]:
        return extract_rosetta_metrics(payload)

    def primary_score(self, metrics: dict[str, float]) -> float:
        # evedesign scores are higher-is-better, while Rosetta ddG is lower-is-better.
        return -metrics["ddg"]

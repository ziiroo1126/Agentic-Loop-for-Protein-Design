"""AlphaFold 3 confidence artifact extraction."""

from __future__ import annotations

from typing import Any

from interaction_design.scoring.base import ArtifactScorer


def extract_af3_metrics(payload: dict[str, Any], binder_index: int) -> dict[str, float]:
    """Extract the ODesign-pipeline AF3 metrics with numeric, checked output."""

    try:
        chain_ptm = [float(value) for value in payload["chain_ptm"]]
        pair_pae = [[float(value) for value in row] for row in payload["chain_pair_pae_min"]]
        complex_ptm = float(payload["ptm"])
        complex_iptm = float(payload["iptm"])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid AF3 summary schema: {error}") from error

    chain_count = len(pair_pae)
    if chain_count < 2:
        raise ValueError("AF3 interaction scoring requires at least two chains")
    if not 0 <= binder_index < chain_count:
        raise ValueError(f"binder index {binder_index} is outside {chain_count} AF3 chains")
    if len(chain_ptm) != chain_count or any(len(row) != chain_count for row in pair_pae):
        raise ValueError("AF3 chain metric dimensions do not agree")

    if chain_count > 2:
        try:
            iptm = float(payload["chain_iptm"][binder_index])
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError("multi-chain AF3 summary requires chain_iptm") from error
    else:
        iptm = complex_iptm

    interface_pae = [
        pair_pae[binder_index][other] for other in range(chain_count) if other != binder_index
    ] + [pair_pae[other][binder_index] for other in range(chain_count) if other != binder_index]
    return {
        "iptm": iptm,
        "binder_ptm": chain_ptm[binder_index],
        "complex_ptm": complex_ptm,
        "chain_ptm_avg": sum(chain_ptm) / len(chain_ptm),
        "ipae_min": min(interface_pae),
        "ipae_avg": sum(interface_pae) / len(interface_pae),
    }


class AF3ConfidenceScorer(ArtifactScorer):
    name = "AlphaFold 3 confidence"
    citations = ["doi:10.1038/s41586-024-07487-w"]
    artifact_key = "af3_summary"
    evaluation_key = "af3"

    def __init__(self, binder_index: int):
        super().__init__()
        self.binder_index = binder_index

    def parse_metrics(self, payload: dict[str, Any]) -> dict[str, float]:
        return extract_af3_metrics(payload, self.binder_index)

    def primary_score(self, metrics: dict[str, float]) -> float:
        pae_component = max(0.0, min(1.0, 1.0 - metrics["ipae_avg"] / 31.0))
        return 0.65 * metrics["iptm"] + 0.25 * metrics["binder_ptm"] + 0.10 * pae_component

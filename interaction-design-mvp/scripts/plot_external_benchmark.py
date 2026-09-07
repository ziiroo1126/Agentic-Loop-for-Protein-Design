#!/usr/bin/env python3
"""Render descriptive external-benchmark results without recomputing metrics.

The input is a completed benchmark session/report.json. All displayed values
are exported to CSV, and the input hash, font, style helper, and rendering
environment are recorded beside the PDF/SVG/PNG outputs. No network is used.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import platform
import shutil
from pathlib import Path

import matplotlib as mpl

mpl.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.colors import Normalize
from matplotlib.lines import Line2D

TARGETS = ("BBF-14", "EGFR", "IL-7Ra", "MBP", "TREM2", "TrkA")
POLICIES = (
    ("fixed", "Fixed order"),
    ("development_best_single", "Dev. best single"),
    ("ef2_ipsae", "EF2 ipSAE"),
    ("heuristic", "Heuristic"),
    ("harness", "Harness"),
)
SCORES = (
    ("ef2_iptm", "EF2\nipTM"),
    ("ef2_ipsae", "EF2\nipSAE"),
    ("ef2_sc_dockq", "EF2\nscDockQ"),
    ("ensemble_ipsae", "Ensemble\nipSAE"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--style-utils",
        type=Path,
        default=Path("/home/yusen/.codex/skills/nature-figure/nature_figure_utils.py"),
        help="Used only to create the initial vendored style snapshot.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.report.resolve()
    report = json.loads(source.read_text())
    by_target = report["by_target"]
    if set(by_target) != set(TARGETS):
        raise ValueError("Expected the six external-benchmark evaluation targets")
    for target in TARGETS:
        pool = by_target[target]
        if pool["n"] != 90:
            raise ValueError(f"{target}: expected the frozen 90-candidate pool")
        for policy, _ in POLICIES:
            result = pool["policies"][policy]
            if result["selected"] != 10 or result["pool_size"] != 90:
                raise ValueError(f"{target}/{policy}: expected 10 selections from 90")
            if not 0 <= result["hits"] <= 10:
                raise ValueError(f"{target}/{policy}: invalid hit count")

    outdir = (args.output_dir or source.parent.parent / "figures").resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    style_path = outdir / "nature_figure_utils.py"
    if not style_path.exists():
        shutil.copyfile(args.style_utils, style_path)
    spec = importlib.util.spec_from_file_location("nature_figure_utils", style_path)
    style = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(style)
    style.retro_style()
    # Resolve installed fonts explicitly; do not silently label a fallback Arial.
    available = {entry.name for entry in font_manager.fontManager.ttflist}
    font = next(name for name in ("Arial", "Liberation Sans", "DejaVu Sans") if name in available)
    mpl.rcParams.update({"font.sans-serif": [font], "svg.fonttype": "none"})

    fig = plt.figure(
        figsize=style.figsize_nature("double", aspect=0.39, scale=1.0), layout="constrained"
    )
    grid = fig.add_gridspec(2, 2, height_ratios=[1, 0.24], width_ratios=[1.44, 1])
    ax_hits = fig.add_subplot(grid[0, 0])
    ax_legend = fig.add_subplot(grid[1, 0])
    ax_note = fig.add_subplot(grid[1, 1])
    ax_legend.set_axis_off()
    ax_note.set_axis_off()
    x = np.arange(len(TARGETS))
    colors = [
        style.GRAY["200"],
        style.BLUE["300"],
        style.SAGE["300"],
        style.GOLD["300"],
        style.RUST["400"],
    ]
    handles = []
    for index, ((policy, label), color) in enumerate(zip(POLICIES, colors, strict=True)):
        heights = [by_target[target]["policies"][policy]["hits"] for target in TARGETS]
        bars = ax_hits.bar(
            x + (index - 2) * 0.145,
            heights,
            width=0.132,
            color=color,
            edgecolor="none",
            alpha=0.85,
            label=label,
        )
        handles.append(bars)
    for index, target in enumerate(TARGETS):
        expected = by_target[target]["random_expected_hits"]
        ax_hits.plot(
            [index - 0.40, index + 0.40],
            [expected, expected],
            color=style.INK,
            linestyle=(0, (2, 1.5)),
            linewidth=0.8,
            clip_on=False,
            zorder=5,
        )
    handles.append(
        Line2D(
            [], [], color=style.INK, linestyle=(0, (2, 1.5)), linewidth=0.8, label="Random expected"
        )
    )
    ax_hits.set_xticks(x, TARGETS)
    ax_hits.set_yticks(np.arange(0, 11, 2))
    ax_hits.set_ylabel("Hits among 10 selections")
    ax_hits.set_title("Selection yield", loc="left", fontsize=8, pad=5)
    # A reserved legend axes avoids covering any zero or nonzero bar.
    ax_legend.legend(
        handles=handles,
        labels=[label for _, label in POLICIES] + ["Random expected"],
        loc="center left",
        ncol=3,
        frameon=False,
        fontsize=7,
        handlelength=1.5,
        columnspacing=1.0,
        handletextpad=0.5,
        borderaxespad=0,
        labelspacing=0.55,
    )

    valid_targets = [
        target
        for target in TARGETS
        if all(by_target[target]["ranking"][score]["auroc"] is not None for score, _ in SCORES)
    ]
    omitted_targets = [target for target in TARGETS if target not in valid_targets]
    if omitted_targets != ["MBP"] or by_target["MBP"]["positives"] != 0:
        raise ValueError("Expected only MBP to have undefined AUROC (zero positives)")
    matrix = np.array(
        [
            [by_target[target]["ranking"][score]["auroc"] for score, _ in SCORES]
            for target in valid_targets
        ]
    )
    if not np.isfinite(matrix).all() or not ((matrix >= 0) & (matrix <= 1)).all():
        raise ValueError("Invalid score AUROC in report")
    # Isolate heatmap defaults from the floating-spine theme. pcolormesh keeps
    # cells vector in PDF and SVG; text values provide an exact scale key.
    with mpl.rc_context():
        mpl.rcdefaults()
        mpl.rcParams.update(
            {
                "font.family": "sans-serif",
                "font.sans-serif": [font],
                "font.size": 7,
                "axes.labelsize": 7,
                "xtick.labelsize": 7,
                "ytick.labelsize": 7,
                "text.color": style.INK,
            }
        )
        ax_auc = fig.add_subplot(grid[0, 1])
        cmap = style.CMAP_DIVERGE
        norm = Normalize(vmin=0, vmax=1)
        ax_auc.pcolormesh(
            np.arange(5),
            np.arange(6),
            matrix,
            cmap=cmap,
            norm=norm,
            edgecolors="white",
            linewidth=1,
        )
        ax_auc.set_xlim(0, 4)
        ax_auc.set_ylim(5, 0)
        ax_auc.set_xticks(np.arange(4) + 0.5, [label for _, label in SCORES])
        ax_auc.set_yticks(np.arange(5) + 0.5, valid_targets)
        ax_auc.tick_params(length=0, pad=3)
        for tick in ax_auc.get_xticklabels():
            tick.set_linespacing(1.05)
        for spine in ax_auc.spines.values():
            spine.set_visible(False)
        for row in range(matrix.shape[0]):
            for col in range(matrix.shape[1]):
                value = matrix[row, col]
                rgba = cmap(norm(value))
                brightness = 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]
                ax_auc.text(
                    col + 0.5,
                    row + 0.5,
                    f"{value:.2f}",
                    ha="center",
                    va="center",
                    fontsize=7,
                    color="white" if brightness < 0.5 else style.INK,
                )
        ax_auc.set_title("Score AUROC (chance = 0.5)", loc="left", fontsize=8, pad=5)
    ax_note.text(
        0,
        0.5,
        "MBP: undefined AUROC (0 positives)",
        transform=ax_note.transAxes,
        ha="left",
        va="center",
        fontsize=7,
    )

    style.trim_axes(fig, exclude=[ax_auc, ax_legend, ax_note])
    ax_hits.set_xlim(-0.60, len(TARGETS) - 0.40)
    ax_hits.set_ylim(0, 11)
    ax_hits.spines["left"].set_bounds(0, 10)
    ax_hits.tick_params(axis="x", pad=3)
    axd = {"selection": ax_hits, "ranking": ax_auc, "legend": ax_legend, "note": ax_note}
    qc = style.iterative_figure_qc(fig, axd, max_iter=1)
    print(style.format_qc_report(qc))
    if not qc["passed"]:
        raise RuntimeError("Figure failed strict QC; adjust layout before export")

    stem = outdir / "external_benchmark_summary"
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".svg"), bbox_inches="tight")
    # PNG is the requested raster twin for preview/report insertion.
    fig.savefig(
        stem.with_suffix(".png"), bbox_inches="tight", dpi=600, facecolor="white", transparent=False
    )
    style.save_for_review(fig, outdir / "external_benchmark_summary_review.png", dpi=150)

    rows = []
    for target in TARGETS:
        pool = by_target[target]
        for policy, _ in POLICIES:
            result = pool["policies"][policy]
            rows.append(
                {
                    "panel": "selection",
                    "target": target,
                    "series": policy,
                    "metric": "hits_at_10",
                    "value": result["hits"],
                    "n": result["pool_size"],
                    "positives": result["pool_positives"],
                    "selected": result["selected"],
                    "missing": 0,
                }
            )
        rows.append(
            {
                "panel": "selection",
                "target": target,
                "series": "random_expected",
                "metric": "expected_hits_at_10",
                "value": pool["random_expected_hits"],
                "n": pool["n"],
                "positives": pool["positives"],
                "selected": 10,
                "missing": 0,
            }
        )
        for score, _ in SCORES:
            result = pool["ranking"][score]
            rows.append(
                {
                    "panel": "ranking",
                    "target": target,
                    "series": score,
                    "metric": "auroc",
                    "value": result["auroc"],
                    "n": result["n"],
                    "positives": result["positives"],
                    "selected": "",
                    "missing": result["missing"],
                }
            )
    with (outdir / "external_benchmark_summary_data.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    caption = (
        "Exploratory retrospective external candidate-selection benchmark. Left: "
        "hits among ten selections per target for fixed anonymous-ID order, the best "
        "single score selected on development target PD-L1 (EF2 ipTM), EF2 ipSAE, "
        "the equal-weight mean-rank heuristic, and the harness. Every policy uses "
        "the same pool of 90 candidates per evaluation target, the same visible "
        "scores, and a quota of ten. Dashed segments show the reported expected "
        "hits for uniform random selection (10 times the target positive fraction). "
        "Right: reported within-target AUROC for four score rankings, with a fixed "
        "0–1 color scale centered at 0.5 and cell values rounded to two decimals. "
        "Five of six evaluation targets have defined AUROC; MBP has zero positive "
        "labels and is omitted from the heatmap. AUROC excludes missing score "
        "values: EF2 scDockQ uses n=89, 82, 79, 79, and 90 for BBF-14, EGFR, "
        "IL-7Ra, TREM2, and TrkA, respectively; the other scores use n=90. "
        "Hits use the publication-derived binder_final combined vendor/refit "
        "assessment with sensorgram review. The six targets and correlated "
        "candidates support descriptive comparisons, not a claim of superiority "
        "or independent prospective validation. No significance test or new "
        "wet-lab measurement is shown.\n\n"
        "Source: Anthropic, claude-protein-binder-design, revision "
        f"{report['source']['revision']}; "
        "https://huggingface.co/datasets/Anthropic/claude-protein-binder-design. "
        "Source data and documentation: CC BY 4.0 "
        "(https://creativecommons.org/licenses/by/4.0/). This figure re-expresses "
        "the completed benchmark report as selection bars and a score heatmap.\n"
    )
    (outdir / "external_benchmark_summary_caption.txt").write_text(caption)
    provenance = {
        "report": str(source),
        "report_sha256": sha256(source),
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256(Path(__file__)),
        "style_snapshot": style_path.name,
        "style_sha256": sha256(style_path),
        "python": platform.python_version(),
        "matplotlib": mpl.__version__,
        "numpy": np.__version__,
        "font_family": font,
        "font_file": font_manager.findfont(font, fallback_to_default=False),
        "source": report["source"],
        "qc": qc,
        "target_order": list(TARGETS),
        "heatmap_targets": valid_targets,
        "policies": [policy for policy, _ in POLICIES],
        "scores": [score for score, _ in SCORES],
        "figure_inches": list(fig.get_size_inches()),
        "raster_dpi": 600,
        "data_transformation": "Display report values; round heatmap annotations to 2 decimals",
    }
    (outdir / "external_benchmark_summary_provenance.json").write_text(
        json.dumps(provenance, indent=2) + "\n"
    )
    plt.close(fig)
    print(stem.with_suffix(".pdf"))
    print(stem.with_suffix(".svg"))
    print(stem.with_suffix(".png"))


if __name__ == "__main__":
    main()

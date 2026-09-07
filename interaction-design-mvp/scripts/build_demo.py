#!/usr/bin/env python3
"""Build a portable, offline walkthrough from existing ALPD result exports."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from interaction_design.adaptive_export import render_replay
from interaction_design.intake import DesignBrief, review_brief
from interaction_design.structure_viewer import export_structure_viewer

TEMPLATES = Path(__file__).with_name("demo")
REPO = Path(__file__).resolve().parents[2]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8"
    )


def copy_record(root: Path, output: Path, relative: str, expected: str) -> None:
    source, target = (root / relative).resolve(), output / relative
    if (
        Path(relative).is_absolute()
        or ".." in Path(relative).parts
        or not source.is_relative_to(root)
        or not source.is_file()
        or digest(source) != expected
    ):
        raise ValueError(f"invalid source artifact: {relative}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    if digest(target) != expected:
        raise ValueError(f"source changed while copying: {relative}")


def build_demo(bundle: Path, replay: Path, output: Path) -> dict:
    bundle, replay, output = bundle.resolve(), replay.resolve(), output.absolute()
    archive = output.parent / (output.name + ".zip")
    for path in (output, archive):
        if path.exists() or path.is_symlink():
            raise FileExistsError(f"demo output must be new: {path}")
    if any(output.resolve().is_relative_to(root) for root in (bundle, replay)):
        raise ValueError("demo output must be outside source exports")
    manifest = json.loads((bundle / "manifest.json").read_text())
    if manifest.get("stage") != "screening_export" or manifest.get("status") != "completed":
        raise ValueError("demo requires a completed screening export")
    replay_manifest = json.loads((replay / "manifest.json").read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".alpd-demo-", dir=output.parent))
    try:
        results = staging / "results"
        results.mkdir()
        for record in manifest["artifacts"]:
            copy_record(bundle, results, record["path"], record["sha256"])
        write_json(results / "manifest.json", manifest)
        export_structure_viewer(results, staging / "structures.html")

        archive_replay = staging / "replay"
        archive_replay.mkdir()
        for name, expected in replay_manifest["files"].items():
            copy_record(replay, archive_replay, name, expected)
        replay_data = json.loads((archive_replay / "replay.json").read_text())
        (archive_replay / "index.html").write_text(render_replay(replay_data), encoding="utf-8")
        # The presentation is regenerated under ALPD; the archived inputs stay untouched.
        write_json(
            archive_replay / "manifest.json",
            {
                "version": "alpd-demo-replay-v1",
                "source_export_manifest_sha256": digest(replay / "manifest.json"),
                "presentation": "Regenerated HTML; JSON records copied without changes.",
                "files": {
                    str(p.relative_to(archive_replay)): digest(p)
                    for p in sorted(archive_replay.rglob("*"))
                    if p.is_file() and p.name != "manifest.json"
                },
            },
        )

        draft = DesignBrief(name="demo_request")
        review = review_brief(draft)
        intake = staging / "intake"
        intake.mkdir()
        write_json(intake / "brief.json", draft.model_dump(mode="json"))
        write_json(intake / "review.json", review)
        data = {
            "task": json.loads((results / "task.json").read_text()),
            "candidates": json.loads((results / "candidates.json").read_text())["candidates"],
            "trace": json.loads((results / "decision_trace.json").read_text()),
            "report": json.loads((results / "report.json").read_text()),
            "intake_review": review,
            "demo_inference": "not_run",
        }
        encoded = json.dumps(data, ensure_ascii=True, allow_nan=False)
        for char, escaped in (("<", r"\u003c"), (">", r"\u003e"), ("&", r"\u0026")):
            encoded = encoded.replace(char, escaped)
        page = (TEMPLATES / "index.html").read_text(encoding="utf-8")
        (staging / "index.html").write_text(
            page.replace("@@DEMO_DATA@@", encoded), encoding="utf-8"
        )
        for name in ("demo.js", "demo.css"):
            shutil.copyfile(TEMPLATES / name, staging / name)
        shutil.copyfile(REPO / "interaction-design-mvp/docs/DEMO.md", staging / "使用说明.md")
        write_json(
            staging / "manifest.json",
            {
                "kind": "alpd-offline-demo",
                "new_model_inference": False,
                "sources": {
                    "pipeline_export_manifest_sha256": digest(bundle / "manifest.json"),
                    "adaptive_export_manifest_sha256": digest(replay / "manifest.json"),
                },
                "files": {
                    str(p.relative_to(staging)): digest(p)
                    for p in sorted(staging.rglob("*"))
                    if p.is_file()
                },
            },
        )
        # Reserve the public names exclusively before publishing; never replace another run.
        output.mkdir()
        archive_created = False
        try:
            for source in sorted(staging.iterdir(), key=lambda p: p.name == "manifest.json"):
                shutil.move(source, output / source.name)
            private_zip = Path(
                shutil.make_archive(str(staging / "package"), "zip", output.parent, output.name)
            )
            with archive.open("xb") as handle:
                archive_created = True
                handle.write(private_zip.read_bytes())
        except BaseException:
            if archive_created:
                archive.unlink(missing_ok=True)
            shutil.rmtree(output)
            raise
    finally:
        shutil.rmtree(staging)
    return {"page": str(output / "index.html"), "zip": str(archive), "new_model_inference": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result-bundle", type=Path, required=True)
    parser.add_argument("--replay", type=Path, default=REPO / "docs/evidence/adaptive-loop-pdl1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_demo(args.result_bundle, args.replay, args.output), ensure_ascii=False))


if __name__ == "__main__":
    main()

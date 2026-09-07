"""Portable, read-only acquisition history. Export never reveals new outcomes."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from interaction_design.adaptive import LIMITATIONS, _digest, _history, _load, _report
from interaction_design.evaluation.jobs import job_lock, read_json
from interaction_design.persistence import write_json


def render_replay(payload: dict) -> str:
    # Escape HTML parser delimiters even inside a non-executable JSON script tag.
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False)
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026")):
        encoded = encoded.replace(char, escaped)
    template = Path(__file__).with_name("adaptive_replay.html").read_text(encoding="utf-8")
    return template.replace("__REPLAY_DATA__", encoded)


def export_adaptive(session: Path, output: Path) -> dict:
    """Export paid observations only; include a report only if already revealed."""
    if output.exists() or output.is_symlink():
        raise ValueError("export output must be a fresh directory")
    with job_lock(session):
        data, protocol, ledger = _load(session)
        report = None
        if (session / "report.json").exists():
            report = _report(data, protocol, ledger)
            if read_json(session / "report.json") != report:
                raise ValueError("existing report differs from replayed ledger")
        payload = {
            "version": "molclaw-adaptive-replay-v1",
            "purpose": "development",
            "policy": protocol["policy"],
            "limitations": LIMITATIONS,
            "pools": [
                _history(pool, protocol, ledger["events"][pool["pool_id"]])
                for pool in sorted(data["pools"], key=lambda pool: pool["pool_id"])
            ],
            "report": report,
        }
        records = {
            "replay.json": payload,
            "ledger.json": ledger,
            "protocol.json": protocol,
            "source-manifest.json": read_json(session / "manifest.json"),
        }
        if report is not None:
            records["report.json"] = report
            records["source.json"] = data["source"]
        output.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
        try:
            for name, record in records.items():
                write_json(staging / name, record)
            (staging / "index.html").write_text(render_replay(payload), encoding="utf-8")
            (staging / "README.md").write_text(
                "# Adaptive development replay\n\n"
                "Open index.html directly in a browser; no server or network is required.\n"
                "replay.json contains the initial paid observation and event deltas.\n"
                "ledger.json preserves the accepted actions and host-authored reviews.\n"
                "Prediction counts are logical acquisition cost, not measured GPU savings.\n"
                "Only already-paid predictions are exported. Outcomes appear only if the\n"
                "source session had already revealed its report after all pools committed.\n"
                "A completed replay is retrospective and must not be used as a blinded\n"
                "decision interface. Host interpretations are not calibrated probabilities.\n"
                "source-manifest.json identifies the original private input and code;\n"
                "private inputs and model weights are intentionally absent from this bundle.\n"
                "manifest.json checks exported file bytes, not scientific validity.\n",
                encoding="utf-8",
            )
            write_json(
                staging / "manifest.json",
                {
                    "version": "molclaw-adaptive-export-v1",
                    "files": {path.name: _digest(path) for path in sorted(staging.iterdir())},
                },
            )
            staging.rename(output)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return {
        "status": "exported",
        "output": str(output),
        "page": str(output / "index.html"),
        "outcomes_included": report is not None,
    }

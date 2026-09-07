"""Run provenance and artifact checksums."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from evedesign.system import SystemInstance

from interaction_design.assets import AssetPin, sha256_file
from interaction_design.generator import ODesignGenerator
from interaction_design.persistence import write_json
from interaction_design.specs import InteractionDesignSpec


def canonical_sha256(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def git_state(path: str | Path) -> dict[str, object] | None:
    root = Path(path)
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "-C", str(root), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"revision": revision, "dirty": dirty}


def _artifact_records(run_dir: Path) -> list[dict[str, object]]:
    return [
        {
            "path": str(path.relative_to(run_dir)),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for path in sorted(run_dir.rglob("*"))
        if path.is_file() and path.name not in {"manifest.json", "status.json"}
    ]


def write_run_manifest(
    generator: ODesignGenerator,
    spec: InteractionDesignSpec,
    instances: Sequence[SystemInstance],
    asset_pins: Sequence[AssetPin],
    expected_odesign_revision: str,
    project_root: str | Path,
    *,
    status: str = "generated",
) -> Path:
    if generator.last_run_dir is None:
        raise ValueError("generator has no run directory")
    run_dir = generator.last_run_dir
    execution = generator.last_execution
    invocation_path = run_dir / "execution.json"
    invocation = (
        json.loads(invocation_path.read_text(encoding="utf-8")) if invocation_path.is_file() else {}
    )
    metadata = execution.metadata if execution else invocation.get("metadata", {})
    reference = Path(spec.reference_structure) if spec.reference_structure else None
    payload = {
        "schema_version": "1",
        "created_at": datetime.now(UTC).isoformat(),
        "run_id": run_dir.name,
        "stage": "generation",
        "status": status,
        "task_sha256": canonical_sha256(spec.model_dump(mode="json")),
        "task": spec.model_dump(mode="json"),
        "inputs": {
            "reference_structure": {
                "path": str(reference.resolve()),
                "sha256": sha256_file(reference),
                "size": reference.stat().st_size,
            }
            if reference is not None and reference.is_file()
            else None,
        },
        "seeds": spec.generation.seeds,
        "candidate_ids": [instance.id for instance in instances],
        "executor": {
            "name": execution.executor if execution else generator.executor.name,
            "command": list(execution.command) if execution else invocation.get("command", []),
            "metadata": metadata,
        },
        "software": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "evedesign": importlib.metadata.version("evedesign"),
            "odesign_expected_revision": expected_odesign_revision,
            "project_git": git_state(project_root),
            "odesign_git": git_state(metadata["odesign_repo"])
            if metadata.get("odesign_repo")
            else None,
        },
        "model_assets": [
            {"name": pin.name, "repo_id": pin.repo_id, "revision": pin.revision}
            for pin in asset_pins
        ],
        "artifacts": _artifact_records(run_dir),
    }
    return write_json(run_dir / "manifest.json", payload)

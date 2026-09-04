"""Pinned Hugging Face model-asset acquisition and local checksum verification."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

PINNED_REVISION = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class AssetPin:
    name: str
    repo_id: str
    revision: str
    include: tuple[str, ...]
    local_subdir: str


def default_asset_lock() -> Path:
    """Return the packaged lock path in editable and wheel installations."""

    packaged = Path(str(files("interaction_design").joinpath("data", "assets.lock.toml")))
    if packaged.is_file():
        return packaged
    source_checkout = Path(__file__).parents[2] / "config" / "assets.lock.toml"
    if source_checkout.is_file():
        return source_checkout
    raise FileNotFoundError("packaged assets.lock.toml is missing")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_asset_pins(lock_path: str | Path) -> list[AssetPin]:
    payload = tomllib.loads(Path(lock_path).read_text(encoding="utf-8"))
    pins = [
        AssetPin(
            name=item["name"],
            repo_id=item["repo_id"],
            revision=item["revision"],
            include=tuple(item["include"]),
            local_subdir=item["local_subdir"],
        )
        for item in payload.get("assets", [])
    ]
    if not pins:
        raise ValueError("asset lock contains no assets")
    for pin in pins:
        if not PINNED_REVISION.fullmatch(pin.revision):
            raise ValueError(f"asset {pin.name!r} must use a full 40-character commit revision")
    return pins


def load_odesign_revision(lock_path: str | Path) -> str:
    payload = tomllib.loads(Path(lock_path).read_text(encoding="utf-8"))
    try:
        revision = str(payload["software"]["odesign"]["revision"])
    except KeyError as error:
        raise ValueError("asset lock has no software.odesign.revision") from error
    if not PINNED_REVISION.fullmatch(revision):
        raise ValueError("ODesign source must use a full 40-character commit revision")
    return revision


def hf_download_command(pin: AssetPin, destination: Path) -> list[str]:
    local_dir = destination / pin.local_subdir
    return [
        "hf",
        "download",
        pin.repo_id,
        "--revision",
        pin.revision,
        "--local-dir",
        str(local_dir),
        "--include",
        *pin.include,
    ]


def hf_verify_command(pin: AssetPin, destination: Path) -> list[str]:
    return [
        "hf",
        "cache",
        "verify",
        pin.repo_id,
        "--revision",
        pin.revision,
        "--local-dir",
        str(destination / pin.local_subdir),
        "--format",
        "json",
    ]


def _asset_files(destination: Path) -> list[Path]:
    return sorted(
        path
        for path in destination.rglob("*")
        if path.is_file() and ".cache" not in path.parts and path.name != "asset-manifest.json"
    )


def write_asset_manifest(
    destination: Path,
    pins: list[AssetPin],
    hub_verification: list[dict[str, object]] | None = None,
) -> Path:
    manifest_path = destination / "asset-manifest.json"
    payload = {
        "schema_version": "1",
        "created_at": datetime.now(UTC).isoformat(),
        "pins": [
            {
                "name": pin.name,
                "repo_id": pin.repo_id,
                "revision": pin.revision,
                "include": list(pin.include),
                "local_subdir": pin.local_subdir,
            }
            for pin in pins
        ],
        "hub_verification": hub_verification or [],
        "files": {
            str(path.relative_to(destination)): {
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
            for path in _asset_files(destination)
        },
    }
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def download_assets(lock_path: str | Path, destination: str | Path) -> Path:
    """Download every pin using hf CLI, then record byte-level checksums."""

    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    pins = load_asset_pins(lock_path)
    hub_verification: list[dict[str, object]] = []
    for pin in pins:
        command = hf_download_command(pin, target)
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(f"hf download failed for {pin.name}: {completed.stderr[-2000:]}")
        verify_command = hf_verify_command(pin, target)
        verified = subprocess.run(verify_command, text=True, capture_output=True, check=False)
        if verified.returncode != 0:
            raise RuntimeError(f"hf cache verify failed for {pin.name}: {verified.stderr[-2000:]}")
        hub_verification.append(
            {
                "name": pin.name,
                "command": verify_command,
                "result": verified.stdout.strip(),
            }
        )
    return write_asset_manifest(target, pins, hub_verification)


def verify_assets(destination: str | Path) -> dict[str, object]:
    """Verify the locally recorded cache without loading model weights."""

    target = Path(destination).resolve()
    manifest_path = target / "asset-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    for relative, expected in payload["files"].items():
        path = target / relative
        if not path.is_file():
            failures.append(f"missing: {relative}")
            continue
        actual = sha256_file(path)
        if actual != expected["sha256"]:
            failures.append(f"checksum mismatch: {relative}")
    discovered = {str(path.relative_to(target)) for path in _asset_files(target)}
    unexpected = sorted(discovered.difference(payload["files"]))
    failures.extend(f"untracked asset: {path}" for path in unexpected)
    return {
        "ok": not failures,
        "checked_files": len(payload["files"]),
        "failures": failures,
    }

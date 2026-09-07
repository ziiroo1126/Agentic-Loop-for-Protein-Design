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

MODEL_ASSET_NAMES = {
    "odesign_base_prot_flex": "odesign-prot-flex",
    "odesign_base_prot_rigid": "odesign-prot-rigid",
    "odesign_base_ligand_rigid": "odesign-ligand-rigid",
    "odesign_base_na_rigid": "odesign-na-rigid",
}

INVERSE_FOLDING_ASSET_NAMES = {
    "protein": "oinvfold-protein",
    "ligand": "oinvfold-ligand",
    "rna": "oinvfold-rna",
    "dna": "oinvfold-dna",
}


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


def select_asset_pins(pins: list[AssetPin], names: list[str] | None) -> list[AssetPin]:
    """Select named pins while retaining lock-file order."""

    if not names:
        return pins
    requested = set(names)
    available = {pin.name for pin in pins}
    unknown = sorted(requested.difference(available))
    if unknown:
        raise ValueError(
            f"unknown asset name(s): {', '.join(unknown)}; "
            f"available: {', '.join(sorted(available))}"
        )
    return [pin for pin in pins if pin.name in requested]


def task_asset_pins(pins: list[AssetPin], model: str, modality: str) -> list[AssetPin]:
    """Return the diffusion and inverse-folding checkpoints required by one task."""

    try:
        names = [MODEL_ASSET_NAMES[model], INVERSE_FOLDING_ASSET_NAMES[modality]]
    except KeyError as error:
        raise ValueError(f"no asset mapping for {error.args[0]!r}") from error
    return select_asset_pins(pins, names)


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


def download_assets(
    lock_path: str | Path,
    destination: str | Path,
    only: list[str] | None = None,
) -> Path:
    """Download selected pins using hf CLI, then record byte-level checksums."""

    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=True)
    pins = select_asset_pins(load_asset_pins(lock_path), only)
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


def verify_task_checkpoints(
    checkpoint_root: Path,
    model: str,
    modality: str,
) -> dict[str, object]:
    """Bind actual checkpoint bytes to locally recorded, pinned model assets.

    A local manifest checksum establishes cache integrity; it is not independent
    proof of publisher authenticity. Hub verification evidence is kept separately.
    """

    destination = checkpoint_root.resolve().parent
    manifest_path = destination / "asset-manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    pins = task_asset_pins(load_asset_pins(default_asset_lock()), model, modality)
    recorded_pins = {item["name"]: item for item in payload["pins"]}
    records = []
    for pin in pins:
        recorded = recorded_pins.get(pin.name, {})
        if any(recorded.get(key) != getattr(pin, key) for key in ("repo_id", "revision")):
            raise ValueError(f"checkpoint manifest pin mismatch: {pin.name}")
        for filename in pin.include:
            path = destination / pin.local_subdir / filename
            if path.resolve().parent != checkpoint_root.resolve():
                raise ValueError(f"checkpoint lock path does not match checkpoint root: {filename}")
            relative = str(path.resolve().relative_to(destination))
            expected = payload["files"].get(relative)
            if expected is None:
                raise ValueError(f"checkpoint absent from asset manifest: {relative}")
            checksum = sha256_file(path)
            size = path.stat().st_size
            if checksum != expected["sha256"] or size != expected["size"]:
                raise ValueError(f"checkpoint checksum/size mismatch: {relative}")
            records.append({"path": str(path.resolve()), "sha256": checksum, "size": size})
    return {
        "verification": "local-manifest",
        "manifest_sha256": sha256_file(manifest_path),
        "hub_verification": payload.get("hub_verification", []),
        "files": records,
    }

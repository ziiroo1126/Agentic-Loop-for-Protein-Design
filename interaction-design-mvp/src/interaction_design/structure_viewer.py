"""Offline, read-only 3Dmol.js views of portable screening exports."""

from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

WEB = Path(__file__).with_name("structure_viewer")
KINDS = ("generated_structure", "monomer_structure", "predicted_complex")


def render_structure_viewer(root: Path, *, records: dict | None = None) -> str:
    """Embed local structures once, with no browser fetches or absolute runtime paths.

    The main exporter calls this on its validated staging directory. For existing
    exports, ``export_structure_viewer`` supplies the saved checksum inventory.
    """
    root = root.resolve()
    sources = {}

    def read(relative: str) -> str:
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError("viewer artifacts must use relative paths")
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("viewer artifact is missing or escapes the export directory")
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if records is not None and records.get(relative, {}).get("sha256") != digest:
            raise ValueError(f"viewer artifact checksum mismatch: {relative}")
        sources[relative] = digest
        return raw.decode("utf-8")

    structures = {}

    def structure(relative: str | None) -> str | None:
        if relative is None:
            return None
        suffix = Path(relative).suffix.lower()
        if suffix not in {".pdb", ".cif", ".mmcif"}:
            raise ValueError(f"unsupported viewer structure format: {suffix}")
        if relative not in structures:
            structures[relative] = {
                "format": "pdb" if suffix == ".pdb" else "cif",
                "text": read(relative),
            }
        return relative

    task = json.loads(read("task.json"))
    candidates = json.loads(read("candidates.json"))["candidates"]
    rows = []
    for candidate in candidates:
        evaluated = candidate["complex_evaluation_status"] == "evaluated"
        artifacts = candidate["artifacts"]
        if not evaluated and (artifacts.get("predicted_complex") or candidate.get("post")):
            raise ValueError("unevaluated candidate contains complex results")
        rows.append(
            {
                "candidate_id": candidate["candidate_id"],
                "seed": candidate["seed"],
                "length": candidate["length"],
                "sequence": candidate["sequence"],
                "pre": candidate["pre"],
                "post": candidate["post"] if evaluated else None,
                "evaluated": evaluated,
                "structures": {kind: structure(artifacts.get(kind)) for kind in KINDS},
            }
        )
    payload = {
        "schema_version": 1,
        "name": task.get("name", "ALPD"),
        "reference": structure(task.get("reference_structure")),
        "candidates": rows,
        "structures": structures,
        "sources": sources,
        "acceptance": None,
        "binding_validated": False,
    }
    encoded = json.dumps(payload, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    for char, escaped in (("<", r"\u003c"), (">", r"\u003e"), ("&", r"\u0026")):
        encoded = encoded.replace(char, escaped)
    vendor = WEB / "vendor"
    pinned = json.loads((vendor / "provenance.json").read_text(encoding="utf-8"))
    for name, digest in pinned["sha256"].items():
        if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"bundled 3Dmol.js checksum mismatch: {name}")
    library = (vendor / "3Dmol-min.js").read_text(encoding="utf-8")
    # Source maps are development aids, and must not trigger an external request.
    library = "\n".join(
        line for line in library.splitlines() if not line.startswith("//# sourceMappingURL=")
    )
    if "</script" in library.lower():
        raise ValueError("3Dmol.js bundle cannot be embedded safely")
    notices = "\n\n".join(
        (vendor / name).read_text(encoding="utf-8")
        for name in ("NOTICE", "LICENSE", "3Dmol-min.js.LICENSE.txt")
    )
    template = (WEB / "index.html").read_text(encoding="utf-8")
    # Split the template once: user-supplied text must never become a template token.
    substitutions = {
        "STYLE": (WEB / "viewer.css").read_text(encoding="utf-8"),
        "DATA": encoded,
        "LIBRARY": library,
        "APP": (WEB / "viewer.js").read_text(encoding="utf-8"),
        "NOTICES": html.escape(notices),
    }
    return re.sub(r"@@(STYLE|DATA|LIBRARY|APP|NOTICES)@@", lambda m: substitutions[m[1]], template)


def export_structure_viewer(root: Path, destination: Path) -> Path:
    """Create one new HTML file outside an existing, checksum-verified result bundle."""
    root, destination = root.resolve(), destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("viewer destination already exists")
    if destination.resolve().is_relative_to(root):
        raise ValueError("viewer destination must be outside the original result bundle")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("stage") != "screening_export" or manifest.get("status") != "completed":
        raise ValueError("viewer requires a completed screening export")
    records = {record["path"]: record for record in manifest["artifacts"]}
    if len(records) != len(manifest["artifacts"]):
        raise ValueError("duplicate export artifact paths")
    # Check the entire source bundle, including diagnostics not shown on the page.
    for relative, record in records.items():
        path = (root / relative).resolve()
        if Path(relative).is_absolute() or not path.is_relative_to(root):
            raise ValueError("export artifact escapes its directory")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
            raise ValueError(f"export artifact checksum mismatch: {relative}")
    page = render_structure_viewer(root, records=records)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as handle:
        handle.write(page)
    return destination.resolve()

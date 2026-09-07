#!/usr/bin/env python3
"""Build ALPD's static gallery and portable downloads from checked-in case records."""

import argparse
import hashlib
import importlib.util
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def archive_tree(source, output):
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(str(Path(source.name) / path.relative_to(source)))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())


def build(output):
    output = output.expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"output must be new: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    spec = importlib.util.spec_from_file_location(
        "alpd_demo_builder", ROOT / "interaction-design-mvp/scripts/build_demo.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory(prefix=".alpd-site-", dir=output.parent) as folder:
        site = Path(folder) / "site"
        site.mkdir()
        module.build_demo(
            ROOT / "examples/pdl1-binder/run",
            ROOT / "docs/evidence/adaptive-loop-pdl1",
            site / "demo",
        )
        downloads = site / "downloads"
        downloads.mkdir()
        # Use deterministic ZIP timestamps for reproducible release assets.
        (site / "demo.zip").unlink()
        archive_tree(site / "demo", downloads / "alpd-demo.zip")
        shutil.copytree(ROOT / "examples/pdl1-binder", site / "case")
        for name in ["LICENSE", "THIRD_PARTY_NOTICES.md"]:
            shutil.copyfile(ROOT / name, site / "case" / name)
        shutil.copyfile(ROOT / "interaction-design-mvp/LICENSE", site / "case/CORE_LICENSE")
        shutil.copyfile(
            ROOT / "interaction-design-mvp/config/ODESIGN_PIPELINE_LICENSE",
            site / "case/REFERENCE_LICENSE",
        )
        archive_tree(site / "case", downloads / "alpd-pdl1-case.zip")
        for path in (ROOT / "tools/site").iterdir():
            shutil.copyfile(path, site / path.name)
        shutil.copyfile(ROOT / "docs/images/alpd-structure-viewer.png", site / "structure.png")
        (site / ".nojekyll").touch()
        checksums = {
            str(p.relative_to(site)): sha256(p) for p in sorted(site.rglob("*")) if p.is_file()
        }
        (site / "manifest.json").write_text(
            json.dumps(
                {
                    "kind": "alpd-public-gallery-v1",
                    "new_model_inference": False,
                    "files": checksums,
                },
                indent=2,
            )
            + "\n"
        )
        sums = "".join(f"{sha256(p)}  {p.name}\n" for p in sorted(downloads.glob("*.zip")))
        (downloads / "SHA256SUMS").write_text(sums)
        # Manifest also covers the download checksum file.
        checksums["downloads/SHA256SUMS"] = sha256(downloads / "SHA256SUMS")
        (site / "manifest.json").write_text(
            json.dumps(
                {
                    "kind": "alpd-public-gallery-v1",
                    "new_model_inference": False,
                    "files": checksums,
                },
                indent=2,
            )
            + "\n"
        )
        site.rename(output)
    return {"page": str(output / "index.html"), "downloads": str(output / "downloads")}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(build(parser.parse_args().output), indent=2))

#!/usr/bin/env python3
"""Check the actual release inputs, static dependencies, manifests and ZIP contents."""

import argparse
import hashlib
import json
import re
import tomllib
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest(path):
    manifest = json.loads(path.read_text())
    records = manifest.get("files")
    if records is None:
        records = {item["path"]: item["sha256"] for item in manifest["artifacts"]}
    for relative, expected in records.items():
        target = (path.parent / relative).resolve()
        if not target.is_relative_to(path.parent.resolve()) or not target.is_file():
            raise ValueError(f"missing or escaping manifest member: {path}: {relative}")
        if digest(target) != expected:
            raise ValueError(f"checksum mismatch: {path}: {relative}")
    return len(records)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in {"href", "src", "data-src"} and value:
                self.links.append(value)


def verify_site(site):
    checked = 0
    for manifest in site.rglob("manifest.json"):
        data = json.loads(manifest.read_text())
        if "files" in data or "artifacts" in data:
            checked += verify_manifest(manifest)
    for page in site.rglob("*.html"):
        parser = Links()
        parser.feed(page.read_text())
        for link in parser.links:
            url = urlsplit(link)
            if url.scheme in {"http", "https", "data", "blob", "mailto"} or not url.path:
                continue
            target = (page.parent / unquote(url.path)).resolve()
            if not target.is_relative_to(site.resolve()) or not target.is_file():
                raise ValueError(f"broken local HTML dependency: {page}: {link}")
    for filename, folder in [("alpd-demo.zip", "demo"), ("alpd-pdl1-case.zip", "case")]:
        with zipfile.ZipFile(site / "downloads" / filename) as archive:
            expected = {
                str(Path(folder) / p.relative_to(site / folder)): p
                for p in (site / folder).rglob("*")
                if p.is_file()
            }
            if set(archive.namelist()) != set(expected):
                raise ValueError(f"archive contents differ from shipped directory: {filename}")
            for name, source in expected.items():
                if archive.read(name) != source.read_bytes():
                    raise ValueError(f"archive bytes differ: {filename}: {name}")
    for line in (site / "downloads/SHA256SUMS").read_text().splitlines():
        expected, name = line.split("  ", 1)
        if digest(site / "downloads" / name) != expected:
            raise ValueError(f"download checksum mismatch: {name}")
    return checked


def verify_metadata():
    project = tomllib.loads((ROOT / "interaction-design-mvp/pyproject.toml").read_text())["project"]
    assert project["name"] == "agentic-loop-protein-design"
    assert project["scripts"]["alpd"] == "interaction_design.cli:main"
    plugin = ROOT / "plugins/alpd"
    lock = json.loads((plugin / "package-lock.json").read_text())
    for package in lock["packages"].values():
        assert not package.get("resolved", "").startswith("file:"), "non-portable SDK lock"
    for relative in [".codex-plugin/plugin.json", ".claude-plugin/plugin.json", "package.json"]:
        data = json.loads((plugin / relative).read_text())
        assert data["version"] == project["version"], relative
        assert "alpd" in data["name"].lower(), relative
    skill = (plugin / "skills/alpd/SKILL.md").read_text()
    assert re.search(r"^name: alpd$", skill, re.MULTILINE)
    for path in plugin.rglob("*"):
        if (
            path.is_file()
            and "node_modules" not in path.parts
            and path.suffix in {".md", ".json", ".yaml", ".yml", ".mjs", ".py"}
        ):
            assert "molclaw" not in path.read_text().lower(), path
    return project["version"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    args = parser.parse_args()
    version = verify_metadata()
    original = verify_manifest(ROOT / "examples/pdl1-binder/run/manifest.json")
    checked = verify_site(args.site.resolve())
    print(
        json.dumps(
            {
                "status": "passed",
                "version": version,
                "original_artifacts": original,
                "verified_manifest_entries": checked,
                "checks": [
                    "metadata",
                    "original_case_hashes",
                    "all_manifests",
                    "html_dependencies",
                    "complete_zip_contents",
                    "download_checksums",
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

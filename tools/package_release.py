#!/usr/bin/env python3
"""Assemble versioned ALPD downloads after the release gates have passed."""

import argparse
import shutil
import tempfile
import tomllib
from pathlib import Path

from build_site import ROOT, archive_tree, sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "interaction-design-mvp/pyproject.toml").read_text())[
        "project"
    ]["version"]
    output = args.output.absolute()
    output.mkdir(parents=True, exist_ok=False)
    for name in ["alpd-demo.zip", "alpd-pdl1-case.zip"]:
        shutil.copyfile(args.site / "downloads" / name, output / name)
    wheel = args.dist / f"agentic_loop_protein_design-{version}-py3-none-any.whl"
    source = args.dist / f"agentic_loop_protein_design-{version}.tar.gz"
    for path in [wheel, source]:
        shutil.copyfile(path, output / path.name)
    with tempfile.TemporaryDirectory(prefix="alpd-host-package-") as temporary:
        host = Path(temporary) / "alpd"
        shutil.copytree(
            ROOT / "plugins/alpd",
            host,
            ignore=shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc"),
        )
        shutil.copyfile(ROOT / "LICENSE", host / "LICENSE")
        shutil.copyfile(ROOT / "THIRD_PARTY_NOTICES.md", host / "THIRD_PARTY_NOTICES.md")
        (host / "INSTALL.md").write_text(
            "# ALPD host bundle\n\n"
            "Install the separate core from the repository first. Set ALPD_PROJECT_ROOT to that "
            "checkout. Copy or link this bundle's skills/alpd directory into your project's "
            ".agents/skills/alpd for Codex (or .claude/skills/alpd for Claude Code). "
            "The launcher uses the checkout's installed .venv/bin/alpd; ALPD_CLI can select "
            "another installed executable. No weights or host credentials are included.\n\n"
            "Full instructions: https://github.com/ziiroo1126/Agentic-Loop-for-Protein-Design/"
            f"blob/v{version}/docs/CODEX.md\n"
        )
        archive_tree(host, output / f"alpd-hosts-{version}.zip")
    (output / "SHA256SUMS").write_text(
        "".join(
            f"{sha256(path)}  {path.name}\n" for path in sorted(output.iterdir()) if path.is_file()
        )
    )
    print(output)


if __name__ == "__main__":
    main()

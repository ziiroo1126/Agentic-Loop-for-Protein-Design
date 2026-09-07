#!/usr/bin/env python3
"""ALPD project-local skill installation and environment checks (standard library only)."""

import runpy
from pathlib import Path

if __name__ == "__main__":
    runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "plugins/alpd/tools/local.py"),
        run_name="__main__",
    )

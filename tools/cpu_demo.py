#!/usr/bin/env python3
"""Run the packaged synthetic CPU example through an installed ALPD executable."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cli", default="alpd")
    args = parser.parse_args()
    cli = shutil.which(args.cli)
    if cli is None:
        parser.error("ALPD is not installed; run bash tools/setup.sh and activate the environment")
    cli = str(Path(cli).resolve())
    output = args.output.expanduser().absolute()
    output.mkdir(parents=True, exist_ok=False)
    source = (
        Path(__file__).resolve().parents[1]
        / "interaction-design-mvp/examples/adaptive-synthetic.json"
    )
    shutil.copyfile(source, output / "input.json")
    commands = [
        [
            "adaptive",
            "prepare",
            "--imported",
            "input.json",
            "--output",
            "session",
            "--quota",
            "2",
            "--budget-per-candidate",
            "3",
            "--policy",
            "uniform",
        ],
        ["adaptive", "run", "session"],
        ["adaptive", "report", "session"],
        ["adaptive", "export", "session", "--output", "view"],
    ]
    for index, command in enumerate(commands):
        result = subprocess.run(
            [cli, *command], cwd=output, capture_output=True, text=True, check=False
        )
        (output / f"{index + 1:02d}.stdout.json").write_text(result.stdout)
        (output / f"{index + 1:02d}.stderr.log").write_text(result.stderr)
        if result.returncode:
            raise SystemExit(f"ALPD failed at {command[:2]}; inspect {output}")
    print(
        json.dumps(
            {
                "page": str(output / "view/index.html"),
                "data": "synthetic",
                "policy": "uniform (no LLM)",
                "new_model_inference": False,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

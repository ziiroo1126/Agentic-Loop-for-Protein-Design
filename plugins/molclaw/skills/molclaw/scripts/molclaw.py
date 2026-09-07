#!/usr/bin/env python3
"""Launch an existing local MolClaw CLI without installing or downloading anything."""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        usage="%(prog)s [--project-root ROOT] [--cli EXECUTABLE] -- CLI_ARGUMENTS",
    )
    parser.add_argument(
        "--project-root",
        default=os.environ.get("MOLCLAW_PROJECT_ROOT"),
        help="MolClaw repository root; defaults to MOLCLAW_PROJECT_ROOT",
    )
    parser.add_argument(
        "--cli",
        default=os.environ.get("MOLCLAW_CLI"),
        help="existing CLI executable path; defaults to MOLCLAW_CLI or the local .venv",
    )
    parser.add_argument("cli_arguments", nargs=argparse.REMAINDER)
    options = parser.parse_args()
    if not options.project_root:
        parser.error("provide --project-root or MOLCLAW_PROJECT_ROOT")
    project_root = Path(options.project_root).expanduser().resolve()
    workflow = project_root / "interaction-design-mvp"
    if not (workflow / "pyproject.toml").is_file() or not (
        workflow / "src" / "interaction_design" / "cli.py"
    ).is_file():
        parser.error(f"not a MolClaw checkout: {project_root}")

    if options.cli:
        executable = Path(options.cli).expanduser().resolve()
    else:
        executable = workflow / ".venv" / "bin" / "interaction-design"
        if not executable.is_file():
            located = shutil.which("interaction-design")
            if located is None:
                parser.error("CLI unavailable; supply an existing executable with --cli")
            executable = Path(located).resolve()
    if not executable.is_file() or not os.access(executable, os.X_OK):
        parser.error(f"CLI is not an executable file: {executable}")

    arguments = options.cli_arguments
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    if not arguments:
        parser.error("provide CLI arguments after -- (for example: -- screen --help)")
    # Preserve cwd and argument boundaries so paths and JSON stay with the caller.
    try:
        os.execv(str(executable), [str(executable), *arguments])
    except OSError as error:
        parser.error(f"cannot execute {executable}: {error}")


if __name__ == "__main__":
    main()

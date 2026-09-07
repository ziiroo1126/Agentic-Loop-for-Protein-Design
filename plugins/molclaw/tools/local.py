#!/usr/bin/env python3
"""Link the shared skill or check an existing local checkout. No dependency downloads."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOSTS = {"codex": ".agents", "claude": ".claude", "deepseek": ".dsh"}


def source_skill(root: Path) -> Path:
    skill = root / "plugins" / "molclaw" / "skills" / "molclaw"
    for path in (
        skill / "SKILL.md",
        skill / "scripts" / "molclaw.py",
        root / "interaction-design-mvp" / "src" / "interaction_design" / "cli.py",
    ):
        if not path.is_file():
            raise ValueError(f"incomplete MolClaw checkout: {path}")
    return skill


def destination(project: Path, host: str) -> Path:
    if not project.is_dir():
        raise ValueError(f"project directory does not exist: {project}")
    return project / HOSTS[host] / "skills" / "molclaw"


def link_status(source: Path, target: Path) -> str:
    if target.is_symlink() and target.resolve() == source.resolve():
        return "linked"
    return "conflict" if target.exists() or target.is_symlink() else "missing"


def install(root: Path, project: Path, host: str) -> dict:
    source, target = source_skill(root), destination(project, host)
    state = link_status(source, target)
    if state == "conflict":
        raise ValueError(f"existing skill has a different source; refusing to replace: {target}")
    if state == "missing":
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    return {
        "status": "installed" if state == "missing" else "already_installed",
        "host": host,
        "skill": str(target),
        "source": str(source),
        "environment": {"MOLCLAW_PROJECT_ROOT": str(root)},
        "model_session": "not_started",
    }


def _probe(command: list[str], cwd: Path, timeout: float) -> dict:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        return {"status": "failed", "detail": str(error)}
    return {
        "status": "passed" if result.returncode == 0 else "failed",
        "returncode": result.returncode,
        **({"detail": (result.stderr or result.stdout)[-2500:]} if result.returncode else {}),
    }


def doctor(
    root: Path,
    project: Path,
    host: str,
    *,
    cli: Path | None = None,
    task: Path | None = None,
    runtime: Path | None = None,
    timeout: float = 120,
) -> dict:
    if (task is None) != (runtime is None):
        raise ValueError("resource preflight requires both --task and --runtime")
    skill, target = source_skill(root), destination(project, host)
    command = [sys.executable, str(skill / "scripts" / "molclaw.py"), "--project-root", str(root)]
    if cli is not None:
        command.extend(["--cli", str(cli)])
    command.append("--")
    checks = {
        "cli": _probe([*command, "adaptive", "--help"], project, timeout),
        "skill_path": {"status": link_status(skill, target), "path": str(target)},
        "host_executable": {
            "path": shutil.which("dsh" if host == "deepseek" else host),
            "status": "informational",
        },
        "host_discovery": {"status": "not_verified_in_host"},
        "model_session": {"status": "not_started"},
        "gpu": {"status": "not_probed"},
        "resources": {"status": "not_checked", "detail": "supply --task and --runtime"},
    }
    if task is not None:
        checks["resources"] = _probe(
            [*command, "pipeline", "preflight", str(task), "--runtime", str(runtime)],
            project,
            timeout,
        )
    ready = checks["cli"]["status"] == "passed" and checks["skill_path"]["status"] == "linked"
    return {
        "status": "passed" if ready and checks["resources"]["status"] != "failed" else "incomplete",
        "checks": checks,
        "replay_cli_available": checks["cli"]["status"] == "passed",
        "live_inference_verified": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "doctor"):
        child = commands.add_parser(name)
        child.add_argument("--host", choices=HOSTS, required=True)
        child.add_argument("--project-dir", type=Path, required=True)
        child.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[3])
        if name == "doctor":
            child.add_argument("--cli", type=Path)
            child.add_argument("--task", type=Path)
            child.add_argument("--runtime", type=Path)
            child.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args(argv)
    try:
        root = args.project_root.expanduser().resolve()
        project = args.project_dir.expanduser().resolve()
        if args.command == "install":
            result = install(root, project, args.host)
        else:
            result = doctor(
                root,
                project,
                args.host,
                cli=args.cli.expanduser().resolve() if args.cli else None,
                task=args.task.expanduser().resolve() if args.task else None,
                runtime=args.runtime.expanduser().resolve() if args.runtime else None,
                timeout=args.timeout,
            )
    except (OSError, ValueError) as error:
        parser.exit(2, f"molclaw local: {error}\n")
    print(json.dumps(result, indent=2))
    return 1 if result["status"] == "incomplete" else 0


if __name__ == "__main__":
    raise SystemExit(main())

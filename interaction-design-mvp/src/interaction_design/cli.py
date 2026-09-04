"""Small CLI over the domain workflow; no web service is required for the MVP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from interaction_design.assets import (
    default_asset_lock,
    download_assets,
    load_odesign_revision,
    verify_assets,
)
from interaction_design.conversion import spec_to_system, system_to_odesign_input
from interaction_design.generator import ODesignGenerator
from interaction_design.runtime import (
    ContainerODesignExecutor,
    LocalODesignExecutor,
    MockODesignExecutor,
)
from interaction_design.specs import load_spec
from interaction_design.workflow import DesignWorkflow


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="interaction-design",
        description="Reproducible evedesign + ODesign interaction-design MVP",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser("validate", help="validate a task JSON")
    validate.add_argument("task", type=Path)

    materialize = commands.add_parser(
        "materialize", help="write the native ODesign input without running a model"
    )
    materialize.add_argument("task", type=Path)
    materialize.add_argument("--output", type=Path, required=True)

    run = commands.add_parser("run", help="run generation and optionally evaluation")
    run.add_argument("task", type=Path)
    run.add_argument("--executor", choices=("mock", "local", "container"), default="mock")
    run.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    run.add_argument("--num-designs", type=int)
    run.add_argument(
        "--generation-only",
        action="store_true",
        help="do not require AF3/PyRosetta sidecars or rank candidates",
    )
    run.add_argument("--odesign-repo", type=Path)
    run.add_argument("--data-root", type=Path)
    run.add_argument("--checkpoint-root", type=Path)
    run.add_argument("--python-executable", default="python")
    run.add_argument("--cuda-visible-devices", default="0")
    run.add_argument("--container-image")
    run.add_argument("--container-runtime", default="docker")
    run.add_argument("--container-gpus", default="all")

    assets = commands.add_parser("assets", help="manage pinned external model assets")
    asset_commands = assets.add_subparsers(dest="asset_command", required=True)
    asset_download = asset_commands.add_parser("download")
    asset_download.add_argument("--destination", type=Path, required=True)
    asset_download.add_argument("--lock", type=Path, default=default_asset_lock())
    asset_verify = asset_commands.add_parser("verify")
    asset_verify.add_argument("--destination", type=Path, required=True)
    return parser


def _executor(args: argparse.Namespace):
    if args.executor == "mock":
        return MockODesignExecutor()
    required = {
        "--odesign-repo": args.odesign_repo,
        "--data-root": args.data_root,
        "--checkpoint-root": args.checkpoint_root,
    }
    missing = [flag for flag, value in required.items() if value is None]
    if missing:
        raise ValueError(f"{args.executor} executor requires {', '.join(missing)}")
    expected_revision = load_odesign_revision(default_asset_lock())
    if args.executor == "local":
        return LocalODesignExecutor(
            odesign_repo=args.odesign_repo,
            data_root=args.data_root,
            checkpoint_root=args.checkpoint_root,
            python_executable=args.python_executable,
            cuda_visible_devices=args.cuda_visible_devices,
            expected_revision=expected_revision,
        )
    if not args.container_image:
        raise ValueError("container executor requires --container-image")
    return ContainerODesignExecutor(
        image=args.container_image,
        odesign_repo=args.odesign_repo,
        data_root=args.data_root,
        checkpoint_root=args.checkpoint_root,
        runtime=args.container_runtime,
        gpu_devices=args.container_gpus,
        expected_revision=expected_revision,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            spec = load_spec(args.task)
            print(f"valid: {spec.name} ({len(spec.molecules)} molecules)")
            return 0
        if args.command == "materialize":
            spec = load_spec(args.task)
            native = system_to_odesign_input(spec_to_system(spec), spec)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(native, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            print(args.output.resolve())
            return 0
        if args.command == "assets":
            if args.asset_command == "download":
                print(download_assets(args.lock, args.destination))
                return 0
            result = verify_assets(args.destination)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.command == "run":
            spec = load_spec(args.task)
            generator = ODesignGenerator(_executor(args), args.artifacts)
            result = DesignWorkflow(generator).run(
                spec,
                num_designs=args.num_designs,
                evaluate=not args.generation_only,
            )
            print(f"run: {result.run_dir.resolve()}")
            print(f"report: {result.report_markdown.resolve()}")
            print(f"manifest: {result.manifest.resolve()}")
            return 0
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

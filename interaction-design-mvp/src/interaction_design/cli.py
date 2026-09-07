"""Small CLI over the domain workflow; no web service is required for the MVP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from interaction_design.assets import (
    default_asset_lock,
    download_assets,
    load_odesign_revision,
    verify_assets,
)
from interaction_design.campaign_tools import load_feedback
from interaction_design.campaigns import prepare_campaign, run_campaign
from interaction_design.conversion import spec_to_system, system_to_odesign_input
from interaction_design.decisions import DecisionConfig
from interaction_design.evaluation.complex import (
    prepare_complex_batch,
    report_complex_batch,
    run_complex_batch,
)
from interaction_design.evaluation.feedback import write_interface_feedback
from interaction_design.evaluation.jobs import (
    import_result,
    prepare_assessment,
    read_json,
    report_assessment,
)
from interaction_design.evaluation.monomer import prepare_monomer_batch, run_monomer_batch
from interaction_design.evaluation.monomer_report import report_monomer_batch
from interaction_design.evaluation.runtime import check_assessment, load_runtime, run_assessment
from interaction_design.generator import ODesignGenerator
from interaction_design.pipeline import (
    apply_pipeline,
    export_pipeline,
    observe_pipeline,
    prepare_pipeline,
    run_pipeline,
)
from interaction_design.runtime import (
    ContainerODesignExecutor,
    LocalODesignExecutor,
    MockODesignExecutor,
)
from interaction_design.screening import (
    apply_screening,
    observe_screening,
    prepare_screening,
    run_screening,
)
from interaction_design.selection import SelectionConfig
from interaction_design.specs import load_spec
from interaction_design.workflow import DesignWorkflow, evaluate_run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="interaction-design",
        description="Reproducible evedesign + ODesign interaction-design MVP",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    benchmark = commands.add_parser(
        "benchmark", help="retrospective external candidate evaluation", add_help=False
    )
    benchmark.add_argument("benchmark_args", nargs=argparse.REMAINDER)

    repetitions = commands.add_parser(
        "benchmark-repeat", help="repeat host decisions on frozen benchmark pools", add_help=False
    )
    repetitions.add_argument("repetition_args", nargs=argparse.REMAINDER)

    adaptive = commands.add_parser(
        "adaptive", help="sequential development replay, reflection and export", add_help=False
    )
    adaptive.add_argument("adaptive_args", nargs=argparse.REMAINDER)

    pipeline = commands.add_parser("pipeline", help="complete local binder design through export")
    pipelines = pipeline.add_subparsers(dest="pipeline_command", required=True)
    preflight = pipelines.add_parser("preflight", help="check local inputs without inference")
    preflight.add_argument("task", type=Path)
    preflight.add_argument("--runtime", type=Path, required=True)
    pipeline_prepare = pipelines.add_parser("prepare")
    pipeline_prepare.add_argument("task", type=Path)
    pipeline_prepare.add_argument("--runtime", type=Path, required=True)
    pipeline_prepare.add_argument(
        "--strategy", choices=("fixed", "heuristic", "harness"), default="harness"
    )
    pipeline_prepare.add_argument("--batch-size", type=int, default=2)
    pipeline_prepare.add_argument("--max-evaluations", type=int, default=8)
    pipeline_prepare.add_argument("--tool-wall-budget-seconds", type=float)
    pipeline_prepare.add_argument("--artifacts", type=Path, default=Path("artifacts/pipelines"))
    for action in ("run", "observe", "apply", "export"):
        command = pipelines.add_parser(action)
        command.add_argument("pipeline_dir", type=Path)
        if action == "apply":
            command.add_argument(
                "--decision", type=Path, required=True, help="JSON file or - for stdin"
            )
        elif action == "export":
            command.add_argument("--output", type=Path, required=True)

    screen = commands.add_parser("screen", help="harness-neutral candidate observe/apply screening")
    screening = screen.add_subparsers(dest="screen_command", required=True)
    screen_prepare = screening.add_parser("prepare")
    screen_prepare.add_argument("monomer_job", type=Path)
    screen_prepare.add_argument(
        "--strategy", choices=("fixed", "heuristic", "harness"), default="harness"
    )
    backend = screen_prepare.add_mutually_exclusive_group(required=True)
    backend.add_argument("--feedback", type=Path, help="saved complex feedback for replay")
    backend.add_argument("--complex-config", type=Path, help="runtime for new complex inference")
    screen_prepare.add_argument("--batch-size", type=int, default=2)
    screen_prepare.add_argument("--max-evaluations", type=int, default=8)
    screen_prepare.add_argument("--tool-wall-budget-seconds", type=float)
    screen_prepare.add_argument("--artifacts", type=Path, default=Path("artifacts/screening"))
    for action in ("observe", "apply", "run"):
        command = screening.add_parser(action)
        command.add_argument("session_dir", type=Path)
        if action == "apply":
            command.add_argument(
                "--decision", type=Path, required=True, help="submission JSON file, or - for stdin"
            )

    campaign = commands.add_parser("campaign", help="sample/observe/stop development baselines")
    campaigns = campaign.add_subparsers(dest="campaign_command", required=True)
    campaign_prepare = campaigns.add_parser("prepare")
    campaign_prepare.add_argument("task", type=Path)
    campaign_prepare.add_argument(
        "--strategy", type=Path, required=True, help="frozen decision configuration JSON"
    )
    campaign_prepare.add_argument("--runtime", type=Path, required=True)
    campaign_prepare.add_argument("--initial-feedback", type=Path)
    campaign_prepare.add_argument("--artifacts", type=Path, default=Path("artifacts/campaigns"))
    campaign_run = campaigns.add_parser("run")
    campaign_run.add_argument("campaign_dir", type=Path)
    replay = campaigns.add_parser(
        "replay", help="reveal saved observations without model inference"
    )
    replay.add_argument("feedback_dir", type=Path)
    replay.add_argument("--strategy", choices=("fixed", "feedback"), default="fixed")
    replay.add_argument("--batch-size", type=int, default=4)
    replay.add_argument("--max-rounds", type=int, default=4)
    replay.add_argument("--patience", type=int, default=1)
    replay.add_argument("--artifacts", type=Path, default=Path("artifacts/campaign-replays"))

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
        "--evaluation-backend",
        choices=("esmfold2", "af3-sidecars"),
        default="esmfold2",
        help="ESMFold2 by default; af3-sidecars selects the original AF3/PyRosetta protocol",
    )
    run.add_argument(
        "--complex-config", type=Path, help="run ESMFold2 complex assessment after generation"
    )
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
    run.add_argument("--timeout", type=float, help="maximum generation process duration in seconds")

    evaluate = commands.add_parser(
        "evaluate", help="evaluate saved candidates without regenerating"
    )
    evaluate.add_argument("run_dir", type=Path)

    complex_command = commands.add_parser(
        "complex", help="evaluate saved protein complexes with ESMFold2"
    )
    complex_commands = complex_command.add_subparsers(dest="complex_command", required=True)
    prepare_complex = complex_commands.add_parser("prepare")
    prepare_complex.add_argument("run_dir", type=Path)
    prepare_complex.add_argument("--candidate", action="append")
    prepare_complex.add_argument(
        "--artifacts", type=Path, default=Path("artifacts/complex-assessments")
    )
    prepare_complex.add_argument("--budget-seconds", type=float, default=1800)
    run_complex = complex_commands.add_parser("run")
    run_complex.add_argument("job_dir", type=Path)
    run_complex.add_argument("--config", type=Path, required=True)
    report_complex = complex_commands.add_parser("report")
    report_complex.add_argument("job_dir", type=Path)
    feedback = complex_commands.add_parser(
        "feedback", help="derive interface geometry observations"
    )
    feedback.add_argument("job_dirs", nargs="+", type=Path)
    feedback.add_argument("--artifacts", type=Path, default=Path("artifacts/interface-feedback"))
    feedback.add_argument("--control-structure", type=Path)

    assessment = commands.add_parser("assessment", help="optional legacy AF3/PyRosetta assessment")
    assessment_commands = assessment.add_subparsers(dest="assessment_command", required=True)
    prepare = assessment_commands.add_parser("prepare")
    prepare.add_argument("run_dir", type=Path)
    prepare.add_argument("--msa-mode", choices=("search", "provided", "none"), default="search")
    prepare.add_argument("--target-data", type=Path)
    prepare.add_argument("--seed", type=int, default=1)
    prepare.add_argument("--budget-seconds", type=float, default=1800)
    for action in ("check", "run"):
        command = assessment_commands.add_parser(action)
        command.add_argument("job_dir", type=Path)
        command.add_argument("--config", type=Path, required=True)
    for stage in ("af3", "rosetta"):
        command = assessment_commands.add_parser(f"import-{stage}")
        command.add_argument("job_dir", type=Path)
        command.add_argument("--candidate", required=True)
        command.add_argument("--source", type=Path, required=True)
    assessment_report = assessment_commands.add_parser("report")
    assessment_report.add_argument("job_dir", type=Path)

    monomer = commands.add_parser("monomer", help="offline ESMFold checks of saved binders")
    monomer_commands = monomer.add_subparsers(dest="monomer_command", required=True)
    prepare_monomer = monomer_commands.add_parser("prepare")
    prepare_monomer.add_argument("run_dir", type=Path)
    prepare_monomer.add_argument("--protocol", type=Path)
    run_monomer = monomer_commands.add_parser("run")
    run_monomer.add_argument("job_dir", type=Path)
    run_monomer.add_argument("--python", type=Path, required=True)
    run_monomer.add_argument("--model-dir", type=Path, required=True)
    run_monomer.add_argument("--gpu", type=int, default=0)
    run_monomer.add_argument("--timeout", type=float, default=900)
    monomer_report = monomer_commands.add_parser("report")
    monomer_report.add_argument("job_dir", type=Path)

    assets = commands.add_parser("assets", help="manage pinned external model assets")
    asset_commands = assets.add_subparsers(dest="asset_command", required=True)
    asset_download = asset_commands.add_parser("download")
    asset_download.add_argument("--destination", type=Path, required=True)
    asset_download.add_argument("--lock", type=Path, default=default_asset_lock())
    asset_download.add_argument(
        "--only",
        action="append",
        help="download one named lock entry (repeatable); the default downloads all entries",
    )
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
            timeout_seconds=args.timeout,
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
        timeout_seconds=args.timeout,
    )


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "adaptive":
        from interaction_design.adaptive import main as adaptive_main

        return adaptive_main(arguments[1:])
    if arguments and arguments[0] == "benchmark":
        from interaction_design.benchmark import main as benchmark_main

        return benchmark_main(arguments[1:])
    if arguments and arguments[0] == "benchmark-repeat":
        from interaction_design.benchmark_repeats import main as repetition_main

        return repetition_main(arguments[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "pipeline":
            action = args.pipeline_command
            if action == "preflight":
                from interaction_design.pipeline_config import validate_pipeline_runtime

                validate_pipeline_runtime(load_spec(args.task), read_json(args.runtime))
                result = {"status": "passed", "inference": "not_run", "gpu": "not_probed"}
            elif action == "prepare":
                directory = prepare_pipeline(
                    load_spec(args.task),
                    read_json(args.runtime),
                    SelectionConfig(
                        strategy=args.strategy,
                        batch_size=args.batch_size,
                        max_evaluations=args.max_evaluations,
                        tool_wall_budget_seconds=args.tool_wall_budget_seconds,
                    ),
                    artifacts=args.artifacts,
                )
                result = {"pipeline_dir": str(directory), "status": "prepared"}
            elif action == "run":
                result = run_pipeline(args.pipeline_dir)
            elif action == "observe":
                result = observe_pipeline(args.pipeline_dir)
            elif action == "apply":
                result = apply_pipeline(
                    args.pipeline_dir,
                    json.load(sys.stdin) if str(args.decision) == "-" else read_json(args.decision),
                )
            else:
                destination = export_pipeline(args.pipeline_dir, args.output)
                result = {
                    "pipeline_dir": str(args.pipeline_dir.resolve()),
                    "export_dir": str(destination),
                    "status": "exported",
                }
            print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
            return 0
        if args.command == "screen":
            if args.screen_command == "prepare":
                print(
                    prepare_screening(
                        args.monomer_job,
                        SelectionConfig(
                            strategy=args.strategy,
                            batch_size=args.batch_size,
                            max_evaluations=args.max_evaluations,
                            tool_wall_budget_seconds=args.tool_wall_budget_seconds,
                        ),
                        feedback=args.feedback,
                        runtime=read_json(args.complex_config) if args.complex_config else None,
                        artifacts=args.artifacts,
                    )
                )
            else:
                result = (
                    apply_screening(
                        args.session_dir,
                        json.load(sys.stdin)
                        if str(args.decision) == "-"
                        else read_json(args.decision),
                    )
                    if args.screen_command == "apply"
                    else observe_screening(args.session_dir)
                    if args.screen_command == "observe"
                    else run_screening(args.session_dir)
                )
                print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
            return 0
        if args.command == "campaign":
            if args.campaign_command == "prepare":
                print(
                    prepare_campaign(
                        DecisionConfig.model_validate(read_json(args.strategy)),
                        task=load_spec(args.task),
                        runtime=read_json(args.runtime),
                        feedback=args.initial_feedback,
                        artifacts=args.artifacts,
                    )
                )
            elif args.campaign_command == "run":
                print(run_campaign(args.campaign_dir))
            else:
                payload = load_feedback(args.feedback_dir)
                config = DecisionConfig(
                    strategy=args.strategy,
                    first_seed=min(x["generation_seed"] for x in payload["observations"]),
                    batch_size=args.batch_size,
                    max_rounds=args.max_rounds,
                    patience=args.patience,
                )
                directory = prepare_campaign(
                    config, feedback=args.feedback_dir, artifacts=args.artifacts, replay=True
                )
                print(run_campaign(directory))
            return 0
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
                print(download_assets(args.lock, args.destination, args.only))
                return 0
            result = verify_assets(args.destination)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0 if result["ok"] else 1
        if args.command == "complex":
            if args.complex_command == "prepare":
                print(
                    prepare_complex_batch(
                        args.run_dir,
                        artifacts=args.artifacts,
                        candidates=args.candidate,
                        budget_seconds=args.budget_seconds,
                    )
                )
            elif args.complex_command == "run":
                print(run_complex_batch(args.job_dir, config=args.config))
            elif args.complex_command == "feedback":
                print(
                    write_interface_feedback(
                        args.job_dirs,
                        artifacts=args.artifacts,
                        control_structure=args.control_structure,
                    )
                )
            else:
                print(report_complex_batch(args.job_dir))
            return 0
        if args.command == "assessment":
            action = args.assessment_command
            if action == "prepare":
                print(
                    prepare_assessment(
                        args.run_dir,
                        msa_mode=args.msa_mode,
                        target_data=args.target_data,
                        seed=args.seed,
                        budget_seconds=args.budget_seconds,
                    )
                )
                return 0
            if action in {"check", "run"}:
                config = load_runtime(args.config.resolve())
                result = (check_assessment if action == "check" else run_assessment)(
                    args.job_dir,
                    config,
                )
                print(json.dumps(result, indent=2))
                if action == "check":
                    return 0 if result["ready"] else 2
                if result["status"] != "ready_to_report":
                    return 2
                result = report_assessment(args.job_dir)
                print(f"report: {result.report_markdown}")
                return 0
            if action.startswith("import-"):
                result = import_result(
                    args.job_dir, args.candidate, action[7:], args.source.resolve()
                )
                print(json.dumps(result, indent=2))
                return 0
            result = report_assessment(args.job_dir)
            print(f"report: {result.report_markdown}")
            return 0
        if args.command == "monomer":
            if args.monomer_command == "prepare":
                print(prepare_monomer_batch(args.run_dir, args.protocol))
                return 0
            if args.monomer_command == "run":
                run_monomer_batch(
                    args.job_dir,
                    python=args.python,
                    model_dir=args.model_dir,
                    gpu=args.gpu,
                    timeout=args.timeout,
                )
            print(report_monomer_batch(args.job_dir))
            return 0
        if args.command == "run":
            if args.generation_only and args.complex_config:
                raise ValueError("--generation-only cannot be combined with --complex-config")
            if args.complex_config and args.evaluation_backend != "esmfold2":
                raise ValueError("--complex-config requires the esmfold2 evaluation backend")
            spec = load_spec(args.task)
            complex_config = None
            if not args.generation_only and args.evaluation_backend == "esmfold2":
                complex_config = args.complex_config or Path(".cache/esmfold2-runtime.local.json")
                if not complex_config.is_file():
                    raise ValueError(
                        "ESMFold2 evaluation requires --complex-config <runtime.json>; "
                        "use --generation-only to save candidates first"
                    )
                if len(spec.molecules) != 2 or any(
                    x.type != "protein" or x.cyclic for x in spec.molecules
                ):
                    raise ValueError(
                        "ESMFold2 PPI evaluation requires two linear protein chains; "
                        "use --generation-only for other design modalities"
                    )
            generator = ODesignGenerator(_executor(args), args.artifacts)
            result = DesignWorkflow(generator).run(
                spec,
                num_designs=args.num_designs,
                evaluate=not args.generation_only and args.evaluation_backend == "af3-sidecars",
            )
            print(f"run: {result.run_dir.resolve()}")
            print(f"report: {result.report_markdown.resolve()}")
            print(f"manifest: {result.manifest.resolve()}")
            if complex_config:
                job = prepare_complex_batch(
                    result.run_dir, artifacts=args.artifacts / "complex-assessments"
                )
                print(f"complex job: {job}", flush=True)
                print(f"complex report: {run_complex_batch(job, config=complex_config)}")
            return 0
        if args.command == "evaluate":
            result = evaluate_run(args.run_dir)
            print(f"run: {result.run_dir}")
            print(f"report: {result.report_markdown}")
            print(f"manifest: {result.manifest}")
            return 0
    except (OSError, RuntimeError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

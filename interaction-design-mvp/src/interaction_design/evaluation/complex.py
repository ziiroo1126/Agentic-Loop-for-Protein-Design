"""Sequence-only ESMFold2 complex evaluation of immutable saved designs."""

from __future__ import annotations

import csv
import math
import os
import shutil
from pathlib import Path

import biotite.structure as bs
import numpy as np
from biotite.structure.io.pdbx import CIFFile, get_structure

from interaction_design.assets import sha256_file
from interaction_design.conversion import spec_to_system
from interaction_design.evaluation.jobs import (
    checked_path,
    file_record,
    job_lock,
    read_json,
    timestamp_id,
)
from interaction_design.evaluation.monomer import _worker_source
from interaction_design.manifest import canonical_sha256
from interaction_design.outputs import PROTEIN_3_TO_1, _chain_id, parse_odesign_outputs
from interaction_design.persistence import write_json
from interaction_design.runtime.process import run_logged
from interaction_design.specs import InteractionDesignSpec


def default_policy() -> dict:
    from importlib.resources import files

    packaged = Path(str(files("interaction_design").joinpath("data", "esmfold2-ppi.protocol.json")))
    path = packaged if packaged.is_file() else Path(__file__).parents[3] / "config" / packaged.name
    return read_json(path)


def _ca(atoms, chain: str, sequence: str):
    ca = atoms[(atoms.chain_id == chain) & (atoms.atom_name == "CA")]
    keys = list(zip(ca.res_id.tolist(), ca.ins_code.tolist(), strict=True))
    if (
        "".join(PROTEIN_3_TO_1.get(x, "?") for x in ca.res_name) != sequence
        or len(keys) != len(set(keys))
        or not np.isfinite(ca.coord).all()
    ):
        raise ValueError(f"chain {chain} C-alpha atoms do not match the requested sequence")
    return ca


def prepare_complex_batch(
    run_dir: Path,
    *,
    artifacts: Path = Path("artifacts/complex-assessments"),
    candidates: list[str] | None = None,
    budget_seconds: float = 1800,
) -> Path:
    root = run_dir.resolve()
    generation = read_json(root / "manifest.json")
    if generation.get("stage") != "generation" or generation.get("status") != "generated":
        raise ValueError("complex evaluation requires a successful saved generation")
    spec = InteractionDesignSpec.model_validate(generation["task"])
    if canonical_sha256(spec.model_dump(mode="json")) != generation["task_sha256"]:
        raise ValueError("generation task checksum mismatch")
    if (
        len(spec.molecules) != 2
        or any(x.type != "protein" or x.cyclic for x in spec.molecules)
        or spec.molecules[spec.binder_index].role != "design"
        or spec.molecules[1 - spec.binder_index].role != "context"
    ):
        raise ValueError(
            "ESMFold2 PPI evaluation requires one linear protein binder and one target"
        )
    if not math.isfinite(budget_seconds) or budget_seconds <= 0:
        raise ValueError("budget must be positive and finite")
    instances = parse_odesign_outputs(root / "output", spec_to_system(spec), spec)
    by_id = {x.id: x for x in instances}
    if len(by_id) != len(instances) or set(by_id) != set(generation["candidate_ids"]):
        raise ValueError("candidate set differs from generation")
    selected = candidates if candidates is not None else generation["candidate_ids"]
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(by_id):
        raise ValueError("candidate selection contains unknown or duplicate IDs")
    records = {r["path"]: r for r in generation["artifacts"]}
    requests = []
    for candidate_id in selected:
        instance = by_id[candidate_id]
        source = Path(instance.metadata["artifacts"]["structure"])
        record = records.get(str(source.relative_to(root)))
        if record is None:
            raise ValueError("candidate absent from generation artifact manifest")
        checked_path(root, record)
        atoms = get_structure(CIFFile.read(source), model=1, use_author_fields=False)
        chains, reference_ca = [], {}
        for chain, index, role in [
            ("A", spec.binder_index, "binder"),
            ("B", 1 - spec.binder_index, "target"),
        ]:
            sequence = "".join(instance[index].rep)
            if not sequence or set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"):
                raise ValueError("ESMFold2 PPI protocol requires canonical protein sequences")
            ca = _ca(atoms, _chain_id(index), sequence)
            chains.append(
                {
                    "id": chain,
                    "role": role,
                    "sequence": sequence,
                    "generation_chain": _chain_id(index),
                }
            )
            reference_ca[chain] = ca.coord.astype(float).tolist()
        requests.append(
            {
                "candidate_id": candidate_id,
                "chains": chains,
                "odesign": instance.metadata["odesign"],
                "reference": {"path": str(source), "sha256": record["sha256"]},
                "reference_ca": reference_ca,
            }
        )
    job = artifacts.resolve() / timestamp_id()
    (job / "requests").mkdir(parents=True)
    manifest = {
        "stage": "complex_batch_preparation",
        "backend": "esmfold2",
        "generation_run": str(root),
        "generation_manifest_sha256": sha256_file(root / "manifest.json"),
        "policy": default_policy(),
        "budget_seconds": budget_seconds,
        "requests": [],
    }
    for index, request in enumerate(requests):
        path = write_json(job / "requests" / f"candidate{index:04d}.json", request)
        manifest["requests"].append(
            {"candidate_id": request["candidate_id"], **file_record(path, job)}
        )
    write_json(job / "manifest.json", manifest)
    write_json(job / "status.json", {"status": "prepared", "backend": "esmfold2"})
    return job


def load_complex_job(job: Path) -> dict:
    manifest = read_json(job / "manifest.json")
    if manifest.get("stage") != "complex_batch_preparation" or manifest["backend"] != "esmfold2":
        raise ValueError("not an ESMFold2 complex job")
    if (
        sha256_file(Path(manifest["generation_run"]) / "manifest.json")
        != manifest["generation_manifest_sha256"]
    ):
        raise ValueError("generation manifest changed")
    for row in manifest["requests"]:
        request = read_json(checked_path(job, row))
        if request["candidate_id"] != row["candidate_id"]:
            raise ValueError("request identity mismatch")
        if sha256_file(Path(request["reference"]["path"])) != request["reference"]["sha256"]:
            raise ValueError("reference structure changed")
    return manifest


def completed_complex_output(job: Path) -> Path:
    receipt = read_json(job / "completed.json")
    if receipt["request_manifest_sha256"] != sha256_file(job / "manifest.json"):
        raise ValueError("request manifest changed since execution")
    checked_path(job, receipt["execution"])
    manifest = checked_path(job, receipt["output_manifest"])
    for row in read_json(manifest)["artifacts"]:
        checked_path(manifest.parent, row)
    return manifest.parent


def run_complex_batch(job: Path, *, config: Path) -> Path:
    job = job.resolve()
    with job_lock(job):
        manifest = load_complex_job(job)
        if (job / "completed.json").exists():
            return report_complex_batch(job)
        runtime = read_json(config.resolve())
        missing = {"python", "model_dir", "esmc_dir", "ccd"} - runtime.keys()
        if missing:
            raise ValueError(f"ESMFold2 runtime is missing fields: {', '.join(sorted(missing))}")
        python = Path(runtime["python"]).absolute()
        model, esmc, ccd = (Path(runtime[k]).resolve() for k in ["model_dir", "esmc_dir", "ccd"])
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError("ESMFold2 Python executable is unavailable")
        for folder, revision in [
            (model, manifest["policy"]["model_revision"]),
            (esmc, manifest["policy"]["esmc_revision"]),
        ]:
            if folder.name != revision or not (folder / "config.json").is_file():
                raise ValueError("model snapshot differs from pinned protocol or is incomplete")
        if not ccd.is_file():
            raise ValueError("local CCD file is unavailable")
        gpu, threads = runtime.get("gpu", 0), runtime.get("cpu_threads", 4)
        timeout = runtime.get("timeout_seconds", 1800)
        if (
            not isinstance(gpu, int)
            or gpu < 0
            or not isinstance(threads, int)
            or threads < 1
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("invalid GPU, CPU threads or timeout")
        spent = 0.0
        for path in (job / "executions").glob("*/execution.json"):
            previous = read_json(path)
            if previous["status"] == "running":
                raise ValueError("inspect unfinished previous complex process before retrying")
            spent += previous["elapsed_seconds"]
        remaining = min(timeout, manifest["budget_seconds"] - spent)
        if remaining <= 0:
            raise ValueError("complex process time budget exhausted")
        attempt = job / "executions" / timestamp_id()
        attempt.mkdir(parents=True)
        worker = attempt / "esmfold2_batch.py"
        shutil.copyfile(_worker_source(worker.name), worker)
        write_json(attempt / "runtime.json", runtime)
        env = os.environ.copy()
        if runtime.get("unset_ld_library_path", False):
            env.pop("LD_LIBRARY_PATH", None)
        env.update(
            HF_HUB_OFFLINE="1",
            TRANSFORMERS_OFFLINE="1",
            HF_HUB_DISABLE_TELEMETRY="1",
            PYTHONDONTWRITEBYTECODE="1",
            CUDA_VISIBLE_DEVICES=str(gpu),
            OMP_NUM_THREADS=str(threads),
            MKL_NUM_THREADS=str(threads),
            MPLCONFIGDIR=str(attempt / "mpl-cache"),
            TMPDIR="/tmp",
        )
        command = [
            str(python),
            str(worker),
            "--requests",
            str(job / "manifest.json"),
            "--model-dir",
            str(model),
            "--esmc-dir",
            str(esmc),
            "--ccd",
            str(ccd),
            "--output-dir",
            str(attempt / "output"),
            "--cpu-threads",
            str(threads),
        ]
        write_json(job / "status.json", {"status": "running", "attempt": str(attempt)})
        try:
            run_logged(
                command,
                attempt,
                metadata={"stage": "esmfold2_complex_batch", "offline": True},
                env=env,
                timeout_seconds=remaining,
                label="ESMFold2",
                log_prefix="esmfold2",
            )
            output = attempt / "output"
            validate_complex_output(job, output)
            write_json(
                job / "completed.json",
                {
                    "request_manifest_sha256": sha256_file(job / "manifest.json"),
                    "execution": file_record(attempt / "execution.json", job),
                    "output_manifest": file_record(output / "manifest.json", job),
                },
            )
            result = report_complex_batch(job)
        except BaseException as error:
            write_json(job / "status.json", {"status": "failed", "error": str(error)})
            raise
        write_json(job / "status.json", {"status": "completed", "backend": "esmfold2"})
        return result


def validate_complex_output(job: Path, output: Path) -> list[dict]:
    manifest = load_complex_job(job)
    records = read_json(output / "manifest.json")["artifacts"]
    recorded = {row["path"] for row in records}
    if len(recorded) != len(records) or not {"status.json", "provenance.json"} <= recorded:
        raise ValueError("incomplete or duplicate output artifact records")
    for record in records:
        checked_path(output, record)
    status = read_json(output / "status.json")
    if status["status"] != "completed" or status["requests_sha256"] != sha256_file(
        job / "manifest.json"
    ):
        raise ValueError("complex output is incomplete or belongs to another request")
    by_id = {row["candidate_id"]: row for row in status["candidates"]}
    if len(by_id) != len(status["candidates"]) or set(by_id) != {
        r["candidate_id"] for r in manifest["requests"]
    }:
        raise ValueError("complex output candidate set mismatch")
    provenance = read_json(output / "provenance.json")
    if provenance["backend"] != "esmfold2" or provenance["policy"] != manifest["policy"]:
        raise ValueError("complex output protocol mismatch")
    rows = []
    for entry in manifest["requests"]:
        request = read_json(checked_path(job, entry))
        candidate = by_id[entry["candidate_id"]]
        folder = (output / candidate["output_dir"]).resolve()
        if not folder.is_relative_to(output.resolve()):
            raise ValueError("output path escapes batch")
        required = {
            str((folder / name).relative_to(output.resolve()))
            for name in ["metrics.json", "complex.cif", "confidence.npz"]
        }
        if not required <= recorded:
            raise ValueError("candidate output files are absent from the artifact manifest")
        metrics = read_json(folder / "metrics.json")
        if (
            metrics["candidate_id"] != entry["candidate_id"]
            or metrics["backend"] != "esmfold2"
            or metrics["request_sha256"] != entry["sha256"]
            or candidate["request_sha256"] != entry["sha256"]
            or metrics["policy"] != manifest["policy"]
        ):
            raise ValueError("complex output identity or protocol mismatch")
        atoms = get_structure(
            CIFFile.read(folder / "complex.cif"),
            model=1,
            use_author_fields=False,
            extra_fields=["b_factor"],
        )
        if set(atoms.chain_id) != {"A", "B"} or not np.isfinite(atoms.coord).all():
            raise ValueError("predicted complex must contain finite coordinates for chains A/B")
        ca = {x["id"]: _ca(atoms, x["id"], x["sequence"]) for x in request["chains"]}
        lengths = [len(ca[k]) for k in ["A", "B"]]
        with np.load(folder / "confidence.npz", allow_pickle=False) as raw:
            plddt, pae, labels = raw["plddt_0_to_1"], raw["pae_angstrom"], raw["chain_ids"]
            if (
                plddt.shape != (sum(lengths),)
                or pae.shape != (sum(lengths), sum(lengths))
                or not np.isfinite(plddt).all()
                or not np.isfinite(pae).all()
                or (plddt < 0).any()
                or (plddt > 1).any()
                or (pae < 0).any()
                or not np.array_equal(labels, np.array(["A"] * lengths[0] + ["B"] * lengths[1]))
            ):
                raise ValueError("invalid per-residue confidence or chain mapping")
            expected = {
                "mean_plddt_residue": float(plddt.mean() * 100),
                "binder_plddt_residue": float(plddt[labels == "A"].mean() * 100),
                "target_plddt_residue": float(plddt[labels == "B"].mean() * 100),
                "pae_a_to_b_angstrom": float(pae[: lengths[0], lengths[0] :].mean()),
                "pae_b_to_a_angstrom": float(pae[lengths[0] :, : lengths[0]].mean()),
            }
            if any(not math.isclose(metrics[k], v, abs_tol=1e-4) for k, v in expected.items()):
                raise ValueError("summary confidence disagrees with raw prediction")
            if not np.allclose(atoms.coord, raw["atom_positions"], atol=0.00051, rtol=0):
                raise ValueError("CIF coordinates differ from raw prediction")
            if not np.allclose(
                np.concatenate([ca[k].b_factor for k in ["A", "B"]]),
                plddt * 100,
                atol=0.0051,
                rtol=0,
            ):
                raise ValueError("CIF confidence scale differs from raw prediction")
        for name in ["iptm", "ptm"]:
            if not math.isfinite(metrics[name]) or not 0 <= metrics[name] <= 1:
                raise ValueError("invalid complex confidence score")
        for name in [
            "inference_seconds",
            "peak_torch_allocated_bytes",
            "peak_torch_reserved_bytes",
        ]:
            if not math.isfinite(metrics[name]) or metrics[name] < 0:
                raise ValueError("invalid resource metric")
        reference = {k: np.asarray(request["reference_ca"][k]) for k in ["A", "B"]}
        fitted_a, _ = bs.superimpose(reference["A"], ca["A"].coord)
        _, transform = bs.superimpose(reference["B"], ca["B"].coord)
        placed_a = transform.apply(ca["A"].coord)
        rows.append(
            {
                **metrics,
                "generation_seed": request["odesign"]["seed"],
                "binder_ca_rmsd_angstrom": float(bs.rmsd(reference["A"], fitted_a)),
                "binder_rmsd_after_target_alignment_angstrom": float(
                    bs.rmsd(reference["A"], placed_a)
                ),
                "structure": str((folder / "complex.cif").relative_to(job)),
                "threshold_pass": None,
            }
        )
    return sorted(rows, key=lambda x: (-x["iptm"], x["candidate_id"]))


def report_complex_batch(job: Path) -> Path:
    job = job.resolve()
    output = completed_complex_output(job)
    rows = validate_complex_output(job, output)
    manifest, provenance = load_complex_job(job), read_json(output / "provenance.json")
    elapsed = sum(
        read_json(p)["elapsed_seconds"] for p in (job / "executions").glob("*/execution.json")
    )
    write_json(
        job / "report.json",
        {
            "backend": "esmfold2",
            "policy": manifest["policy"],
            "candidates": rows,
            "provenance": provenance,
            "process_seconds_including_failures": elapsed,
            "budget_seconds": manifest["budget_seconds"],
            "binding_validated": False,
            "rosetta_evaluated": False,
        },
    )
    keys = [
        "candidate_id",
        "generation_seed",
        "iptm",
        "ptm",
        "binder_plddt_residue",
        "target_plddt_residue",
        "pae_a_to_b_angstrom",
        "pae_b_to_a_angstrom",
        "binder_ca_rmsd_angstrom",
        "binder_rmsd_after_target_alignment_angstrom",
        "inference_seconds",
        "peak_torch_allocated_bytes",
        "threshold_pass",
        "structure",
    ]
    with (job / "candidates.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# ESMFold2 complex assessment",
        "",
        "Standard ESMFold2; two protein chains; no MSA or structural templates.",
        "One sample per candidate, seed 1, 20 loops and 100 diffusion steps.",
        "",
        "Sorted by ESMFold2 ipTM. No acceptance threshold or experimental binding claim.",
        "RMSDs compare with the generated design, not an experimental complex.",
        "",
        "| Generation seed | ipTM | Binder pLDDT | Binder CA RMSD (Å) | "
        "Binder RMSD after target alignment (Å) |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['generation_seed']} | {row['iptm']:.4f} | {row['binder_plddt_residue']:.2f} | "
            f"{row['binder_ca_rmsd_angstrom']:.3f} | "
            f"{row['binder_rmsd_after_target_alignment_angstrom']:.3f} |"
        )
    lines += [
        "",
        f"Cumulative subprocess wall time: {elapsed:.2f} seconds (including failed attempts).",
        "Includes model hashing/loading; excludes report construction. "
        "Shared GPU timings are not dedicated throughput.",
        "Native CIF pLDDT is preserved; report pLDDT uses the 0–100 scale.",
        "",
    ]
    (job / "report.md").write_text("\n".join(lines))
    return job / "report.md"

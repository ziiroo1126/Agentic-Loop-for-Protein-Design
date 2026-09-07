"""Offline two-protein ESMFold2 worker; runs in the external Biohub environment."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import time
import traceback
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def write(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def record(path: Path, root: Path) -> dict:
    return {
        "path": str(path.relative_to(root)),
        "sha256": digest(path),
        "size": path.stat().st_size,
    }


def model_records(folder: Path) -> list[dict]:
    """Verify every weight shard against its local HF LFS blob name when available."""
    index = folder / "model.safetensors.index.json"
    weights = (
        sorted(set(json.loads(index.read_text())["weight_map"].values()))
        if index.exists()
        else ["model.safetensors"]
    )
    names = ["config.json", *weights]
    if index.exists():
        names.append(index.name)
    rows = []
    for name in names:
        path = folder / name
        if not path.resolve().is_relative_to(folder.parent.parent.resolve()):
            raise ValueError("model asset escapes snapshot/cache repository")
        row = record(path, folder)
        blob = path.resolve().name
        if len(blob) == 64 and all(c in "0123456789abcdef" for c in blob):
            if row["sha256"] != blob:
                raise ValueError(f"cached model checksum mismatch: {path}")
            row["hf_lfs_blob_verified"] = True
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--esmc-dir", type=Path, required=True)
    parser.add_argument("--ccd", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cpu-threads", type=int, default=4)
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=False)
    status = {"status": "running", "requests_sha256": digest(args.requests), "candidates": []}
    try:
        for name in ["HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_HUB_DISABLE_TELEMETRY"]:
            os.environ[name] = "1"
        os.environ["ESMCFOLD_CCD_PATH"] = str(args.ccd.resolve())
        manifest = json.loads(args.requests.read_text())
        policy = manifest["policy"]
        if policy["backend"] != "esmfold2" or policy["msa_mode"] != "none":
            raise ValueError("worker requires the ESMFold2 no-MSA protocol")
        if policy["num_diffusion_samples"] != 1:
            raise ValueError("this protocol requires exactly one diffusion sample")
        print("Verifying cached model files", flush=True)
        provenance = {
            "backend": "esmfold2",
            "policy": policy,
            "offline": True,
            "model_dir": str(args.model_dir.resolve()),
            "esmc_dir": str(args.esmc_dir.resolve()),
            "folding_model": model_records(args.model_dir),
            "language_model": model_records(args.esmc_dir),
            "ccd": {"path": str(args.ccd.resolve()), "sha256": digest(args.ccd)},
            "worker_sha256": digest(Path(__file__)),
            "python": platform.python_version(),
            "packages": {},
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "ld_library_path_unset": "LD_LIBRARY_PATH" not in os.environ,
        }
        for name in ["torch", "esm", "transformers", "biotite", "rdkit", "safetensors"]:
            dist = importlib.metadata.distribution(name)
            direct = dist.read_text("direct_url.json")
            provenance["packages"][name] = {
                "version": dist.version,
                "source": json.loads(direct) if direct else None,
            }
        write(out / "provenance.json", provenance)
        import numpy as np
        import torch
        from esm.models.esmfold2 import (
            ESMFold2InputBuilder,
            ProteinInput,
            StructurePredictionInput,
        )
        from transformers.models.esmfold2.modeling_esmfold2 import ESMFold2Model

        torch.set_num_threads(args.cpu_threads)
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is unavailable in the ESMFold2 worker")
        torch.cuda.set_device(0)
        print("Loading ESMFold2 and ESMC-6B", flush=True)
        started = time.monotonic()
        model, loading = ESMFold2Model.from_pretrained(
            str(args.model_dir),
            local_files_only=True,
            load_esmc=False,
            output_loading_info=True,
        )
        write(out / "loading.json", loading)
        if any(
            loading.get(k)
            for k in ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"]
        ):
            raise ValueError("ESMFold2 checkpoint does not exactly match the folding model")
        model = model.cuda().eval()
        model.load_esmc(str(args.esmc_dir), precision="bf16")
        model.set_kernel_backend(None)
        builder = ESMFold2InputBuilder()
        torch.cuda.synchronize()
        provenance.update(
            model_load_seconds=time.monotonic() - started,
            gpu_name=torch.cuda.get_device_name(0),
            torch_cuda=torch.version.cuda,
            folding_dtype=str(next(model.parameters()).dtype),
            esmc_dtype=str(next(model._esmc.parameters()).dtype),
        )
        write(out / "provenance.json", provenance)
        for row in manifest["requests"]:
            request_path = (args.requests.parent / row["path"]).resolve()
            if not request_path.is_relative_to(args.requests.parent.resolve()):
                raise ValueError("request path escapes job")
            if digest(request_path) != row["sha256"]:
                raise ValueError("request checksum mismatch")
            request = json.loads(request_path.read_text())
            if request["candidate_id"] != row["candidate_id"]:
                raise ValueError("candidate identity mismatch")
            chains = request["chains"]
            if [x["id"] for x in chains] != ["A", "B"]:
                raise ValueError("expected binder A and target B")
            if any(
                not x["sequence"] or set(x["sequence"]) - set("ACDEFGHIKLMNPQRSTVWY")
                for x in chains
            ):
                raise ValueError("expected canonical protein sequences")
            folder = out / request_path.stem
            folder.mkdir()
            spi = StructurePredictionInput(
                sequences=[ProteinInput(id=x["id"], sequence=x["sequence"]) for x in chains]
            )
            print(f"Folding {request['candidate_id']}", flush=True)
            torch.cuda.reset_peak_memory_stats()
            torch.cuda.synchronize()
            started = time.monotonic()
            result = builder.fold(
                model,
                spi,
                num_loops=policy["num_loops"],
                num_sampling_steps=policy["num_sampling_steps"],
                num_diffusion_samples=1,
                seed=policy["seed"],
                lm_dropout=policy["lm_dropout"],
                early_exit=False,
                complex_id=request_path.stem,
            )
            torch.cuda.synchronize()
            elapsed = time.monotonic() - started
            mc = result.complex
            plddt = np.asarray(mc.plddt, dtype=np.float32)
            pae = result.pae.float().numpy()
            labels = np.asarray([mc.metadata.chain_lookup[int(x)] for x in mc.chain_id])
            lengths = [len(x["sequence"]) for x in chains]
            if (
                len(plddt) != sum(lengths)
                or pae.shape != (sum(lengths), sum(lengths))
                or not np.isfinite(plddt).all()
                or not np.isfinite(pae).all()
                or (plddt < 0).any()
                or (plddt > 1).any()
            ):
                raise ValueError("unexpected ESMFold2 confidence shape or scale")
            # Biohub returns per-residue pLDDT in [0,1]; its CIF writer multiplies by 100.
            (folder / "complex.cif").write_text(mc.to_mmcif())
            np.savez_compressed(
                folder / "confidence.npz",
                plddt_0_to_1=plddt,
                pae_angstrom=pae,
                chain_ids=labels,
                pair_chains_iptm=result.pair_chains_iptm.float().numpy(),
                atom_positions=mc.atom_positions,
            )
            a, b = np.flatnonzero(labels == "A"), np.flatnonzero(labels == "B")
            metrics = {
                "backend": "esmfold2",
                "candidate_id": request["candidate_id"],
                "request_sha256": row["sha256"],
                "policy": policy,
                "ptm": float(result.ptm),
                "iptm": float(result.iptm),
                "mean_plddt_residue": float(plddt.mean() * 100),
                "binder_plddt_residue": float(plddt[a].mean() * 100),
                "target_plddt_residue": float(plddt[b].mean() * 100),
                "pae_a_to_b_angstrom": float(pae[np.ix_(a, b)].mean()),
                "pae_b_to_a_angstrom": float(pae[np.ix_(b, a)].mean()),
                "inference_seconds": elapsed,
                "peak_torch_allocated_bytes": torch.cuda.max_memory_allocated(),
                "peak_torch_reserved_bytes": torch.cuda.max_memory_reserved(),
                "threshold_pass": None,
            }
            write(folder / "metrics.json", metrics)
            status["candidates"].append(
                {
                    "candidate_id": request["candidate_id"],
                    "output_dir": folder.name,
                    "request_sha256": row["sha256"],
                }
            )
            write(out / "status.json", status)
            print(json.dumps(metrics), flush=True)
            del result, mc
        status["status"] = "completed"
    except BaseException as error:
        status.update(status="failed", error=str(error), traceback=traceback.format_exc())
        raise
    finally:
        write(out / "status.json", status)
        write(
            out / "manifest.json",
            {
                "artifacts": [
                    record(path, out)
                    for path in sorted(out.rglob("*"))
                    if path.is_file() and path.name != "manifest.json"
                ]
            },
        )


if __name__ == "__main__":
    main()

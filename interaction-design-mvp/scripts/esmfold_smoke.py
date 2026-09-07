"""Offline, single-chain ESMFold check in a separately installed Python environment.

This script records a monomer refold; it does not satisfy the AF3/PPI assessment
protocol. It accepts a local snapshot and a JSON request with a sequence and
optional sequence-matched reference C-alpha coordinates.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def load_model(args):
    # Set before importing libraries; from_pretrained also explicitly forbids downloads.
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import torch
    import transformers.models.esm.modeling_esm as esm_implementation
    import transformers.models.esm.modeling_esmfold as implementation
    from transformers import EsmForProteinFolding

    if args.cpu_threads < 1 or args.chunk_size < 1:
        raise ValueError("CPU threads and chunk size must be positive")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable; this resource check must run on a GPU")
    torch.set_num_threads(args.cpu_threads)
    torch.manual_seed(0)
    torch.backends.cuda.matmul.allow_tf32 = False
    torch.backends.cudnn.allow_tf32 = False
    torch.cuda.reset_peak_memory_stats()

    model_dir = args.model_dir.resolve(strict=True)
    weight = model_dir / "pytorch_model.bin"
    config = model_dir / "config.json"
    assets = []
    print("Checking local model bytes", flush=True)
    for path in [config, weight]:
        sha = checksum(path)
        # HF LFS cache blob names are content SHA-256 values.
        blob_name = path.resolve().name
        if len(blob_name) == 64 and sha != blob_name:
            raise ValueError(f"Cached asset does not match its content hash: {path}")
        assets.append({"path": str(path), "sha256": sha, "size": path.stat().st_size})
    metadata = {
        "kind": "esmfold_monomer_smoke",
        "model_snapshot": str(model_dir),
        "model_assets": assets,
        "model_validation": "local bytes checked against HF cache content hashes; no Hub request",
        "software": {
            "python": sys.version,
            "executable": sys.executable,
            "platform": platform.platform(),
            "packages": {
                name: importlib.metadata.version(name)
                for name in ["torch", "transformers", "numpy", "huggingface-hub"]
            },
            "implementation_sha256": checksum(Path(implementation.__file__)),
            "esm_implementation_sha256": checksum(Path(esm_implementation.__file__)),
            "worker_sha256": checksum(Path(__file__)),
        },
        "hardware": {
            "gpu": torch.cuda.get_device_name(0),
            "visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "cuda_runtime": torch.version.cuda,
            "total_gpu_bytes": torch.cuda.get_device_properties(0).total_memory,
        },
        "settings": {
            "offline": True,
            "dtype": "float32",
            "allow_tf32": False,
            "cpu_threads": args.cpu_threads,
            "chunk_size": args.chunk_size,
            "seed": 0,
            "msa": "not used by ESMFold",
            "partner_chain_in_input": False,
        },
    }
    write_json(args.output_dir / "provenance.json", metadata)
    print("Loading cached ESMFold on GPU 0", flush=True)
    start = time.perf_counter()
    model, loading = EsmForProteinFolding.from_pretrained(
        str(model_dir),
        local_files_only=True,
        use_safetensors=False,
        weights_only=True,
        torch_dtype=torch.float32,
        output_loading_info=True,
    )
    write_json(args.output_dir / "loading.json", loading)
    allowed_missing = set()
    allowed_unexpected = set()
    if (
        importlib.metadata.version("transformers") == "4.52.4"
        and model.config.position_embedding_type == "rotary"
    ):
        # Audited in 4.52.4: this auxiliary contact head is only invoked by
        # EsmModel.predict_contacts(), not by ESMFold's hidden-state forward.
        # The old absolute-position tensor is unused by rotary embeddings.
        allowed_missing = {
            "esm.contact_head.regression.bias",
            "esm.contact_head.regression.weight",
        }
        allowed_unexpected = {"esm.embeddings.position_embeddings.weight"}

        def forbid_unused_contact_head(module, inputs):
            raise RuntimeError("Uninitialized auxiliary contact head unexpectedly called")

        model.esm.contact_head.register_forward_pre_hook(forbid_unused_contact_head)
    if (
        set(loading.get("missing_keys", [])) - allowed_missing
        or set(loading.get("unexpected_keys", [])) - allowed_unexpected
        or loading.get("mismatched_keys")
        or loading.get("error_msgs")
    ):
        raise ValueError("Checkpoint loading mismatch; see loading.json")
    metadata["loading_exceptions"] = {
        "allowed_missing_unused": sorted(allowed_missing),
        "allowed_unexpected_unused": sorted(allowed_unexpected),
        "auxiliary_contact_head_guarded": bool(allowed_missing),
    }
    model = model.eval().cuda()
    model.trunk.set_chunk_size(args.chunk_size)
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - start
    metadata["settings"]["trunk_passes"] = model.config.esmfold_config.trunk.max_recycles
    write_json(args.output_dir / "provenance.json", metadata)

    return model, metadata, load_seconds


def predict(args, model, metadata, load_seconds=0.0, *, model_reused=False):
    import copy

    import numpy as np
    import torch
    from transformers.models.esm.openfold_utils import atom14_to_atom37, residue_constants

    request = json.loads(args.input.read_text())
    sequence = request["sequence"]
    if not sequence or set(sequence) - set("ACDEFGHIKLMNPQRSTVWY"):
        raise ValueError("Expected one nonempty canonical protein sequence; no chain separators")
    metadata = copy.deepcopy(metadata)
    metadata.update(request=request, input_sha256=checksum(args.input))
    metadata["settings"]["model_reused"] = model_reused
    write_json(args.output_dir / "provenance.json", metadata)
    torch.cuda.reset_peak_memory_stats()
    print(f"Predicting {len(sequence)} residues", flush=True)
    start = time.perf_counter()
    with torch.inference_mode():
        output = model.infer(sequence)
    torch.cuda.synchronize()
    inference_seconds = time.perf_counter() - start
    allocated = torch.cuda.max_memory_allocated()
    reserved = torch.cuda.max_memory_reserved()

    mask = output["atom37_atom_exists"][0].bool()
    confidence = output["plddt"][0]
    positions = atom14_to_atom37(output["positions"][-1], output)[0]
    checked = [positions[mask], confidence[mask], output["predicted_aligned_error"], output["ptm"]]
    for tensor in checked:
        if not torch.isfinite(tensor).all().item():
            raise ValueError("Non-finite coordinates or confidence")
    if ((confidence[mask] < 0) | (confidence[mask] > 1)).any().item():
        raise ValueError("Expected Transformers ESMFold confidence on the 0-1 scale")
    ca_index = residue_constants.atom_order["CA"]
    if not mask[:, ca_index].all().item():
        raise ValueError("Missing C-alpha atoms")
    decoded = "".join(residue_constants.restypes_with_x[i] for i in output["aatype"][0].tolist())
    if decoded != sequence:
        raise ValueError("Output sequence differs from request")
    ca = positions[:, ca_index].cpu().numpy().astype(np.float64)
    ca_plddt = confidence[:, ca_index].cpu().numpy() * 100
    raw = {
        "ca_coordinates": ca,
        "atom37_positions": positions.cpu().numpy(),
        "atom37_exists": mask.cpu().numpy(),
        "plddt_atom37_0_to_1": confidence.cpu().numpy(),
        "predicted_aligned_error": output["predicted_aligned_error"][0].cpu().numpy(),
    }
    np.savez_compressed(args.output_dir / "prediction.npz", **raw)
    # Transformers 4.52.4 emits 0-1 pLDDT. Export conventional 0-100 PDB B-factors.
    output["plddt"] = output["plddt"] * 100
    pdb = model.output_to_pdb(output)[0]
    (args.output_dir / "binder.pdb").write_text(pdb)
    (args.output_dir / "binder.fasta").write_text(f">{request['candidate_id']}\n{sequence}\n")
    scores = {
        "scope": "single-chain structural self-consistency; no interaction prediction",
        "length": len(sequence),
        "mean_plddt_atom_weighted": float(confidence[mask].mean().item() * 100),
        "mean_plddt_ca": float(ca_plddt.mean()),
        "min_plddt_ca": float(ca_plddt.min()),
        "plddt_ca_per_residue": ca_plddt.tolist(),
        "ptm": float(output["ptm"].item()),
        "mean_pae_angstrom": float(raw["predicted_aligned_error"].mean()),
        "timing": {
            "model_load_and_gpu_transfer_seconds": load_seconds,
            "inference_seconds": inference_seconds,
            "scope": "synchronized wall time, batch 1; model reuse is recorded in provenance",
        },
        "gpu_memory": {
            "peak_torch_allocated_bytes": allocated,
            "peak_torch_reserved_bytes": reserved,
            "scope": (
                "PyTorch allocator peaks for this prediction, including resident model; "
                "excludes CUDA context and other processes"
            ),
        },
        "threshold_pass": None,
        "binding_validated": False,
    }
    if "reference_ca" in request:
        reference = np.asarray(request["reference_ca"], dtype=np.float64)
        if reference.shape != ca.shape or not np.isfinite(reference).all():
            raise ValueError("Reference coordinates must match every residue of the input sequence")
        moving_center, fixed_center = ca.mean(0), reference.mean(0)
        u, _, vt = np.linalg.svd((ca - moving_center).T @ (reference - fixed_center))
        correction = np.eye(3)
        correction[-1, -1] = np.linalg.det(u @ vt)
        rotation = u @ correction @ vt
        aligned = (ca - moving_center) @ rotation + fixed_center
        distances = np.linalg.norm(aligned - reference, axis=1)
        scores["design_comparison"] = {
            "metric": "all-residue C-alpha RMSD after one global rigid superposition; no trimming",
            "reference": request["reference"],
            "ca_rmsd_angstrom": float(np.sqrt(np.mean(distances**2))),
            "ca_deviation_angstrom_per_residue": distances.tolist(),
        }
        np.savez_compressed(
            args.output_dir / "alignment.npz",
            reference_ca=reference,
            aligned_refold_ca=aligned,
            rotation=rotation,
            moving_center=moving_center,
            fixed_center=fixed_center,
        )
    write_json(args.output_dir / "metrics.json", scores)
    compact = {k: v for k, v in scores.items() if not k.endswith("per_residue")}
    print(json.dumps(compact), flush=True)
    return scores


def run(args):
    model, metadata, load_seconds = load_model(args)
    return predict(args, model, metadata, load_seconds)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--chunk-size", type=int, default=128)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    try:
        run(args)
    except BaseException as error:
        write_json(
            args.output_dir / "failure.json",
            {
                "type": type(error).__name__,
                "message": str(error),
                "elapsed_seconds": time.perf_counter() - started,
                "traceback": traceback.format_exc(),
            },
        )
        raise
    write_json(
        args.output_dir / "manifest.json",
        {
            "status": "completed",
            "stage": "esmfold_monomer_refold",
            "elapsed_seconds": time.perf_counter() - started,
            "artifacts": [
                {"path": p.name, "sha256": checksum(p), "size": p.stat().st_size}
                for p in sorted(args.output_dir.iterdir())
                if p.is_file()
            ],
        },
    )


if __name__ == "__main__":
    main()

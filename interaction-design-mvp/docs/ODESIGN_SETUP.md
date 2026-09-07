# Tested local ODesign inference setup

This recipe is for the source revision in `config/assets.lock.toml` and the protein
OInvFold checkpoint. The application uses Python >=3.12; ODesign runs in a separate
Python 3.10 environment. The first real checks used Torch 2.3.1/CUDA 12.1 on one NVIDIA
L20, without DeepSpeed or FlashAttention. Full observed package versions are saved in
each local run's `runtime-environment.json`.

Reuse existing checkouts, environments, caches, and weights first. For downloads below,
use a command-scoped direct connection (unset both cases of HTTP_PROXY, HTTPS_PROXY,
ALL_PROXY and set NO_PROXY/no_proxy to `*`). Do not change the global agent connection
or stop the proxy service. Diagnose direct failures before considering a scoped proxy.

From `interaction-design-mvp/`, using an existing Python 3.10 executable:

```bash
uv venv --python /path/to/python3.10 .odesign-venv
uv --no-config pip install --python .odesign-venv/bin/python \
  --default-index https://pypi.org/simple \
  -r config/odesign-inference-requirements.txt
uv --no-config pip install --python .odesign-venv/bin/python \
  --no-index --no-deps \
  --find-links https://data.pyg.org/whl/torch-2.3.1+cu121.html \
  'torch-scatter==2.1.2+pt23cu121'
```

This is the tested inference subset, not the full upstream training environment.
Biotite 1.2.0 is used for SMILES input support. The observed environment also contains
`torch-cluster==1.6.3+pt23cu121`; the pinned protein inference entrypoint does not require it.
Install the source revision and model assets following the main README. The source and
asset pins must remain compatible: newer ODesign commits switched protein inverse folding
to MPNN and require a different asset contract.

The two required CCD files were obtained from the URLs defined by
[Protenix v0.5.5](https://github.com/bytedance/Protenix/blob/v0.5.5/protenix/web_service/dependency_url.py),
which uses the same `v20240608` filenames required by ODesign:

| Filename | Download URL | Recorded SHA-256 |
| --- | --- | --- |
| `components.v20240608.cif` | `https://af3-dev.tos-cn-beijing.volces.com/release_data/components.v20240608.cif` | `7240b17369ccfbbcc86e2d02dc8c9db59f46c32e0420f58889c6c121c60bfef0` |
| `components.v20240608.cif.rdkit_mol.pkl` | `https://af3-dev.tos-cn-beijing.volces.com/release_data/components.v20240608.cif.rdkit_mol.pkl` | `2d6caced2d26c62015115a1d0a50f4106755a300e0c2a2d2b5c101c9038dcbcd` |

These are hashes of the files used here. They have not been byte-compared to ODesign's
Google Drive copies. Store them in `data/chemical-components/`. A local
`data/chemical-components/data-manifest.json` records their source and sizes, and every
real execution records actual CCD hashes.

## PD-L1 generation example

`examples/real_smoke_pdl1_binder.json` adapts the official ODesign-pipeline example at
commit `644b5ddfa25c395e84c527eb460be3552c307a04`. Reuse its cached
`examples/prot_binder/PDL1_truncated.pdb`, or obtain that file from the
[pinned source](https://github.com/OTeam-AI4S/ODesign-pipeline/blob/644b5ddfa25c395e84c527eb460be3552c307a04/examples/prot_binder/PDL1_truncated.pdb).
Place it at `data/examples/PDL1_truncated.pdb` and check SHA-256
`a818a160b7f266fe810f1420c1a6af8be438a33b51c8e8dd19f80b5bc702459a`.

The task fixes the target to chain B residues 20–133 and uses hotspots B64/B68/B126/B128.
For a small generation check, binder length is fixed at 60 instead of the upstream
60–150 range, with seed 42 and one backbone/sequence. It keeps binder A and target B.
The configured thresholds reflect the upstream binder filter example; the weights are
our report policy. This smoke task is not a controlled performance benchmark.

Use the local command in the main README with this task or
`examples/real_smoke_ligand_binder.json`, `--generation-only`, and an explicit timeout.
No AF3 parameters, databases, or PyRosetta deployment are installed by this recipe.

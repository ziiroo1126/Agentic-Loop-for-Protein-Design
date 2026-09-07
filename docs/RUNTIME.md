# Runtime and resource guide

The CPU core, ODesign generation, ESMFold v1 and ESMFold2 use separate environments.
Installing the core or linking a skill does not install model weights.

## Supported configurations

| Component | Release configuration / recorded execution |
| --- | --- |
| CPU core | Linux; Python 3.12 and 3.13 in CI, dependencies pinned in `interaction-design-mvp/uv.lock` |
| Codex | Existing authenticated CLI; project-local skill, see [host verification](RELEASE_VERIFICATION.md) |
| Generation | ODesign revision `43944e930aea7dd74d2762f6cce7855471edae7b`; separate Python 3.10, Torch 2.3.1/CUDA 12.1 |
| Monomer | ESMFold v1; recorded Python 3.10, Torch 2.7.1+cu118, Transformers 4.52.4 |
| Complex | Standard ESMFold2 + ESMC-6B; recorded Python 3.12, Torch 2.5.1+cu124, esm 3.3.0, Biohub Transformers 4.57.6 |
| GPU | Recorded on one NVIDIA L20 (approximately 48 GB); GPU workers run sequentially |
| Browser | JavaScript/WebGL for 3D; release browser checks cover desktop, tablet and narrow layouts |

The L20 is a tested configuration, not a universal minimum. Memory varies with target
length and backend. The earlier complex pilot recorded a peak Torch allocation of
13.44 GiB; total device usage and generation requirements differ. CPU replay requires
no CUDA. macOS/Windows full GPU execution is outside the tested release scope.
The old main case's core Python version is retained in its [software record](../examples/pdl1-binder/software.json).

## Prepare environments and assets

1. Install the CPU core with [QUICKSTART.md](QUICKSTART.md). Its locked dependencies
   include a pinned evedesign Git revision; use that lock when reproducing the release.
2. Prepare ODesign using the [tested inference recipe](../interaction-design-mvp/docs/ODESIGN_SETUP.md)
   and [pinned requirements](../interaction-design-mvp/config/odesign-inference-requirements.txt).
   Keep the pinned checkout: newer upstream commits use different inverse-folding assets.
3. Prepare the monomer environment and local snapshot as described in
   [ESMFOLD.md](../interaction-design-mvp/docs/ESMFOLD.md). The worker uses the
   Transformers ESMFold implementation and loads cached weights only.
4. Prepare the separate Biohub environment and trusted CCD asset using
   [ESMFOLD2.md](../interaction-design-mvp/docs/ESMFOLD2.md). Standard ESMFold2 and
   ESMFold2-Fast are different backends. Use the recorded standard-model versions.
5. Copy [pipeline-runtime.example.json](../interaction-design-mvp/config/pipeline-runtime.example.json)
   to a local file, replacing every placeholder with an existing absolute path.

For the complex environment, the recorded source revisions are
[`Biohub/transformers@ef32577f55da19a4989cd7b22e004dc43a4998cb`](https://github.com/Biohub/transformers/tree/ef32577f55da19a4989cd7b22e004dc43a4998cb)
and [`Biohub/esm@d96737e56af90af5f06126955d9dd670db3f930a`](https://github.com/Biohub/esm/tree/d96737e56af90af5f06126955d9dd670db3f930a).
Use their installation instructions at those revisions and the exact package
versions in [the case software record](../examples/pdl1-binder/software.json).
The ordinary Transformers package with the same version string is not necessarily
the same Biohub implementation.

ODesign's source checkout is separate from its weights. Obtain the repository at
the revision above using Git, following the command-scoped direct-network policy.
For a protein-flexible task, the existing asset command selects only the required
generation weights (inspect/reuse the destination before downloading):

```bash
alpd assets download --lock interaction-design-mvp/config/assets.lock.toml \
  --destination /your/asset-root/generation --only odesign-prot-flex --only oinvfold-protein
alpd assets verify --destination /your/asset-root/generation
```

Run downloads with both uppercase/lowercase proxy variables unset as described
below. The destination's `ckpt/` is the runtime `checkpoint_root`. This command
does not prepare the model environments, CCD files, monomer or complex snapshots.

Model/asset identities:

| Asset | Source and pinned revision |
| --- | --- |
| ODesign protein flexible weights | `The-Institute-for-AI-Molecular-Design/ODesign`, `ab808f9e947fc065129e2ce2bc3fbcb95ef6b496` |
| Protein OInvFold | `The-Institute-for-AI-Molecular-Design/OInvFold`, `e77a2b0200570757018751f33f06e32e2387413b` |
| ESMFold v1 | Upstream `facebook/esmfold_v1`; compare actual snapshot/file hashes with the saved monomer provenance |
| Standard ESMFold2 | `biohub/ESMFold2`, `1ebf0e3481a5184eb6171d40615c79e384b48796` |
| ESMC-6B | `biohub/ESMC-6B`, `45b0fa5d7fb06faefbd5e3b89bdcef35d564e79a` |
| Generation chemical components | Both `components.v20240608.cif` and its RDKit `.pkl`; source URLs and hashes in the ODesign recipe |
| Complex chemical components | Trusted upstream Biohub `ccd.pkl`; exact bytes hashed in every execution |

The source-of-truth pins are [assets.lock.toml](../interaction-design-mvp/config/assets.lock.toml)
and [the complex protocol](../interaction-design-mvp/config/esmfold2-ppi.protocol.json).
Model cards and upstream terms apply separately from ALPD's code license. Weights
and large chemical datasets are not included in releases.

## Cache and storage layout

Reuse existing model snapshots and environment executables. Runtime paths can point
to any accessible local cache; no copying into the checkout is required. A convenient
optional layout is:

```text
/your/asset-root/
  ODesign/                     pinned source checkout
  generation/ckpt/             generation and inverse-folding weights
  chemical-components/         generation CCD files
  esmfold_v1/snapshots/<rev>/   complete monomer snapshot
  ESMFold2/snapshots/<rev>/     complete complex snapshot, including all shards
  ESMC-6B/snapshots/<rev>/      complete embedding snapshot
  biohub/ccd.pkl               trusted complex CCD
  environments/               separate Python environments
```

Plan storage for all weights, environments, caches and per-candidate outputs;
the monomer checkpoint alone in the recorded case is about 8.44 GB. Use upstream
file sizes for the selected snapshots rather than assuming a small fixed download.
Keep output and temporary directories on a writable volume with sufficient space.

Downloads should reuse local caches and try direct access first, with proxy variables
unset for that command and Git/pip/tool proxy configuration checked as applicable.
Do not alter a global proxy service. A missing/gated model or wrong revision is not
a reason to switch networks. Enable a proxy only for a diagnosed blocked download.
Normal ALPD model execution sets offline loading and does not download missing assets.

## Check before starting

```bash
alpd pipeline preflight examples/pdl1-binder/run/task.json --runtime /path/to/runtime.json
python tools/alpd.py doctor --host codex --project-dir /path/to/work \
  --task examples/pdl1-binder/run/task.json --runtime /path/to/runtime.json
```

Preflight checks task mappings, paths, required shards, source revision and provenance.
It does not exercise CUDA. Generation/monomer timeouts are configured independently
from complex inference; `max_evaluations` controls only the complex stage.
Reports retain measured tool time; model token costs belong to the configured host.

## Troubleshooting

| Symptom | Next action |
| --- | --- |
| `alpd` unavailable | Activate the installed core environment; check `ALPD_CLI` and rerun doctor |
| Skill not visible | Start Codex in the installed project; inspect `.agents/skills/alpd`; restart the host |
| `needs_input` | Resolve the listed scientific choices; review is an input check, not a failed GPU job |
| Missing checkpoint / shard | Point to a complete pinned local snapshot; check file names and revision |
| Source revision mismatch | Restore the pinned ODesign checkout for this adapter; do not relabel a new checkout |
| Monomer checkpoint loader error | Match the documented Torch/Transformers environment; keep safe checkpoint loading enabled |
| `libnvJitLink` error in complex worker | Use the documented `unset_ld_library_path: true` worker setting; preserve the global environment |
| CUDA out of memory | Inspect the failed stage and device usage; do not silently shrink the supplied design task |
| Output already exists | Use a new destination or the documented observe/resume command for that existing session |
| Interrupted inference | Inspect receipts and logs; a failed/unknown job is not blindly retried |
| WebGL unavailable | Read metrics and download structures; use a browser/device with WebGL for 3D |

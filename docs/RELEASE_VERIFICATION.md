# ALPD 0.1.0 release verification

Local acceptance completed on 2026-09-07. Public CI and deployment are tracked
separately below; local checks do not establish that a URL is deployed.

| Gate | Observed result |
| --- | --- |
| Original records and upstream bytes | Existing evidence, dated historical documents, root/upstream licenses and vendored 3Dmol.js unchanged from `fd5aea0`; all 34 original main-case artifact hashes verified |
| Clean CPU installation | New Python 3.12.11 environment installed locked dependencies; replaced editable core with the built wheel; import resolved inside that environment's `site-packages` |
| Installed scientific core | **883 passed**, 0 failed, 44.12 s; Ruff passed; wheel and sdist built. Final wheel members match the wheel tested above |
| CPU workflow | New synthetic session, fixed uniform policy, report and HTML export completed from the installed CLI without model assets |
| Plugin / SDK | New directory and registry-only lock, Node 22.17.1: **30 passed**; full plugin and skill validators passed |
| Native Codex discovery | Codex 0.153.4 `skills/list`: `alpd:alpd`, repo scope, enabled, ALPD interface metadata |
| Qualified Codex host exchange | Fresh project; supplied task reviewed/compiled; one cached query, one reflection, final two-candidate selection, HTML export and separate real saved-structure viewer; terminal host turn completed |
| Static release inputs | 237 manifest entries checked across the gallery, demo and source results; local HTML dependencies, complete ZIP membership, byte equality and download hashes passed |
| Browser | Firefox 136.0 with software WebGL/Xvfb: gallery, input expansion, decision evidence, tool results, stop reason, candidate/structure controls, chains, rotation and adaptive rounds passed at 1440/768/450 widths; no HTTP page resources |
| Actual offline downloads | Standalone viewer saved a valid PNG and CIF matching the original case bytes. Firefox's opaque `file://` iframe download limitation is explained in the demo |
| Local model resources | Current main-case `pipeline preflight` passed without launching models; GPU execution was not probed |
| Public CI | Pending first release-candidate push; see [workflow](https://github.com/ziiroo1126/Agentic-Loop-for-Protein-Design/actions/workflows/interaction-design-mvp.yml) |
| Public gallery / release assets | Pending publication; Pages must be enabled with GitHub Actions as its source |

Machine-readable evidence: [local checks](release-evidence/local-checks.json),
[native discovery and host actions](release-evidence/codex-host.json),
[browser checks](release-evidence/browser.json).
The host record retains an empty file search and an unsupported `export --help`
probe that was corrected to `viewer export`; required workflow actions completed.
Machine-specific paths in that public record are replaced with placeholders.
Unknown host model metadata remains null.

The CPU demo and host test use synthetic cached predictions. The separate original
main case includes real historical generation, monomer checking, host selection and
complex evaluation; see [its provenance](../examples/pdl1-binder/README.md).
Rebuilding the case or checking resource paths is not new GPU inference. No new GPU
inference or biological experiment was performed for this release preparation.

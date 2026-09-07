# Third-party sources and license scope

ALPD preserves upstream copyright statements. Display-name changes do not remove
the attribution or historical names required in license and original provenance records.

| Material | Source | Scope / included notice |
| --- | --- | --- |
| Repository-level inherited code and host tooling | Original repository contributors | [MIT license](LICENSE), original copyright retained |
| Python scientific core and its scripts | ALPD contributors | [Apache-2.0](interaction-design-mvp/LICENSE) |
| evedesign dependency | [evedesignbio/evedesign](https://github.com/evedesignbio/evedesign/tree/05fa7ed4d882f886c98d8aa9042bcc4b6dcdb05f) | Separate upstream dependency pinned in the Python lock; its upstream license applies |
| 3Dmol.js 2.5.5 | [3dmol/3Dmol.js](https://github.com/3dmol/3Dmol.js/tree/2.5.5) | BSD-3-Clause; full [license](interaction-design-mvp/src/interaction_design/structure_viewer/vendor/LICENSE), [bundle notices](interaction-design-mvp/src/interaction_design/structure_viewer/vendor/3Dmol-min.js.LICENSE.txt) and hashes are bundled and embedded in exported viewers |
| PD-L1 example reference coordinates | [ODesign-pipeline pinned example](https://github.com/OTeam-AI4S/ODesign-pipeline/blob/644b5ddfa25c395e84c527eb460be3552c307a04/examples/prot_binder/PDL1_truncated.pdb) | Copied unchanged as `examples/pdl1-binder/run/reference.pdb`; upstream [Apache-2.0 license](interaction-design-mvp/config/ODESIGN_PIPELINE_LICENSE), ODesign Team and contributors |
| Optional Rosetta protocol `ppi.xml` | ODesign-pipeline at the same pinned revision | [Attribution](interaction-design-mvp/config/ppi.NOTICE) and upstream Apache-2.0 license retained |
| ODesign/OInvFold, ESMFold v1, ESMFold2 and ESMC | Their respective upstream source repositories and model cards | External code/weights, not redistributed in ALPD release assets; upstream terms apply separately |
| Chemical-component model assets | Sources recorded in [runtime setup](docs/RUNTIME.md) | External assets, not bundled; execution retains actual file hashes |
| Adaptive case using public experimental data | Source and transformation documented in [the original case](docs/evidence/ADAPTIVE_LOOP_DEVELOPMENT.md) and [benchmark record](docs/evidence/EXTERNAL_BENCHMARK_DEVELOPMENT.md) | Only the existing exported observations/review records are redistributed here; the full external dataset is not a release asset |
| Synthetic CPU fixture | ALPD software example | Fictional measurements, provided only to demonstrate software behavior |

The PD-L1 reference SHA-256 is
`a818a160b7f266fe810f1420c1a6af8be438a33b51c8e8dd19f80b5bc702459a`.
The adaptive data source is `Anthropic/claude-protein-binder-design`, pinned at
`9e1b81696da46835e9e9cde9a3da976e0abc92ab`; import definitions and attribution are
retained in `interaction-design-mvp/config/external-benchmark-v1.json` and the
linked benchmark record. External data is not relicensed as ALPD code.
Generated and predicted structures are computational outputs with provenance, not
experimentally validated complex structures. Original exported scientific records
remain unchanged; new HTML presentations are derived views.

For offline case archives, `LICENSE`, `CORE_LICENSE` and `REFERENCE_LICENSE` contain
the relevant full texts. Viewers embed the 3Dmol.js license. This inventory does not
relicense any dependency, dataset or model under the ALPD repository license.

# Research preview limits

ALPD is a computational-only project. Wet-lab work is outside its scope and is not
a missing release gate or a planned validation stage. Software acceptance covers
reproducible execution, records and integrations; research claims require the stated
computational benchmarks, baselines, ablations and cost analysis. Existing labels
from published experimental datasets may be reused for offline retrospective evaluation.

- The complete pipeline supports one fixed-length, fully generated linear protein
  binder and one fixed linear protein target. It requires explicit target intervals
  and hotspots, and one candidate per distinct seed. Broader schema options do not
  imply support in this complete workflow.
- A brief may be incomplete; compilation requires resolved, supported constraints.
  Identifiers and sequence-file paths are context, not automatic structure lookup.
- The live loop selects candidates for complex evaluation and stops within its
  allowance. Feedback-driven redesign or automatic constraint changes are not implemented.
- `adaptive` reveals cached predictions from existing candidate pools. It does not
  launch GPU predictions or regenerate candidates. Query counts are not measured GPU savings.
- The main gallery case and the adaptive case use different candidate pools. The CPU
  fixture has fictional scores and labels. Every page identifies its source mode.
- Structure/monomer/interface metrics are diagnostics, not binding probabilities.
  Claims stay within the chosen computational or retrospective protocol; new designs
  are not described as experimentally validated binders. A stable LLM selection
  advantage over the recorded strong baselines has not been demonstrated.
- A doctor/preflight pass does not prove host loading, GPU availability or successful
  model execution. See the exact [validation record](RELEASE_VERIFICATION.md).
- GPU setup is separate and uses large upstream assets. First-release full inference
  targets the documented Linux/NVIDIA configuration. Other platforms are unverified.
- Viewer output is static and offline. WebGL is required for 3D; metrics/downloads
  remain available without it. There is no live web job submission service.
- Replays preserve saved evidence. New GPU/library combinations and host model calls
  need not reproduce identical bytes or decisions. Original run records retain their
  historical schema identifiers and software metadata.
- `alpd` is the current CLI; `interaction-design` remains a compatibility alias for
  existing scripts. The Python import namespace stays `interaction_design`.
- The Python dependency lock includes a pinned Git source. The initial distribution
  is a GitHub source/plugin release, with wheels as convenience assets; PyPI publication
  requires a separate dependency-distribution review.

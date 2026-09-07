# Offline protein structure viewer

ALPD uses a pinned **3Dmol.js 2.5.5** browser bundle to display saved PDB and
mmCIF structures. Each page embeds the library, styles, candidate metrics and
structure text. Open the HTML directly in a browser with JavaScript and WebGL
enabled; no server, CDN, model process or Internet connection is required.

## New pipeline exports

```bash
interaction-design pipeline export /absolute/path/to/completed-pipeline \
  --output /absolute/path/to/new-result-bundle
```

Open `new-result-bundle/index.html`. The Markdown report links to this page and
the export manifest includes its checksum. The page can also be copied or sent
as one file. It contains the exported candidate structures and sequences.

## Existing result bundles

Create a standalone page from a completed `screening_export` result bundle:

```bash
interaction-design viewer export /absolute/path/to/existing-result-bundle \
  --output /absolute/path/to/new-viewer.html
```

The command verifies the original manifest and checksums before rendering. The
output must be a new file **outside** the original bundle so its frozen inventory
stays intact. No saved session, prediction or report is rewritten. This also works
with old exports that predate the viewer, without resuming their pipelines.

## Controls and interpretation

- Select a candidate, then its generated complex, monomer prediction, evaluated
  complex prediction, or the task's original reference structure.
- Use cartoon, stick or line representations. Each chain gets a separate color
  and a visibility checkbox. Drag to rotate, scroll to zoom, or reset the view.
- Click an atom to display its chain, residue name, residue number and atom name.
  Save the current view as PNG or download the original selected structure.
- The side panel shows saved pre-evaluation and complex metrics. Missing metrics
  are displayed as missing; unevaluated complexes have no fabricated scores or
  selectable prediction. Metrics describe the candidate and do not change when
  another representation or reference structure is displayed.

Chain and residue identifiers belong to the **currently displayed file**.
Generated, monomer and complex structures can be cropped or renumbered; the
viewer does not infer target/binder roles from alphabetical chain order. This
first version does not transfer input hotspots across those numbering systems,
align or overlay structures, edit design constraints, or generate surfaces.
PDB B factors are not automatically interpreted as prediction confidence.
The default parser displays the first coordinate model, rather than an ensemble
or crystallographic biological assembly.

All results retain `acceptance: null` and `binding_validated: false`. Viewing a
structure does not establish experimental binding.

If WebGL is unavailable, the page explains how to enable it and preserves the
metrics and structure download controls. If coordinates cannot be parsed, the
page offers the original file and lets you switch structures. Very large pools
produce larger HTML files because coordinates are embedded; the current viewer
is intended for result review, and does not implement streaming or compression.

## Dependencies and provenance

The pinned upstream library, BSD-3-Clause license and source URLs/checksums are
in `src/interaction_design/structure_viewer/vendor/`. They are packaged in the
Python wheel. Export checks their hashes and embeds the full notices in the page.
The upstream JavaScript bytes are kept unchanged; the generated HTML omits only
an optional source-map URL comment. The page blocks network connections through
its content security policy. Original structure bytes and their SHA-256 hashes
are included in the embedded JSON.

API reference: [3Dmol.js GLViewer](https://3dmol.org/doc/GLViewer.html).
Upstream release: [3Dmol.js 2.5.5](https://github.com/3dmol/3Dmol.js/releases/tag/2.5.5).

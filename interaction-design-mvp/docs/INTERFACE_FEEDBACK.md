# Protein interface feedback

`complex feedback` converts completed ESMFold2 predictions into structured geometric
observations for candidate inspection and a future decision policy. It uses saved
coordinates on the CPU; no generation, folding, model loading or download occurs.

```bash
# Run from interaction-design-mvp/. Multiple jobs must contain disjoint candidates
# from the same generation and ESMFold2 protocol.
.venv/bin/alpd complex feedback <completed-job-1> <completed-job-2> \
  --artifacts artifacts/interface-feedback

# Optionally verify geometry against the exact cached experimental control.
.venv/bin/alpd complex feedback <completed-job-1> <completed-job-2> \
  --control-structure .cache/controls/4ZQK.cif
```

## Geometry protocol

`config/interface-feedback.protocol.json` fixes the distance conventions and control
checksum. Each analysis copies this protocol and its implementation into a new output
directory. The current distances follow the protein contact/clash conventions in
[DockQ's constants](https://github.com/wallnerlab/DockQ/blob/master/src/DockQ/constants.py).
This implementation does not calculate DockQ or its native-contact recovery score.

| Observation | Definition |
| --- | --- |
| Contact residue pairs | Unique binder/target residue pairs with at least one heavy-atom distance strictly below 5 Å |
| Clash atom pairs | All cross-chain heavy-atom pairs strictly below 2 Å |
| Clash residue pairs | Unique residue pairs containing at least one such clash |
| Requested hotspot contact | A mapped target residue has a binder heavy atom within 5 Å |
| Hotspot coverage | Contacted requested hotspots / requested hotspots; `null` if none were specified |
| Generated contact retention | Contact pairs shared by the generated and folded structures / generated contact pairs; `null` if the generated interface has no contacts |
| Contact-pair Jaccard | Shared contact pairs / their union; `null` for an empty union |

Contacts and clashes can describe the same atom/residue pair; they are not mutually
exclusive classes. Calculations use the first model and first alternate location,
canonical protein residues, and resolved C/N/O/S atoms. Hydrogen, deuterium and hetero
atoms, including water, are excluded. Duplicate atom identities, non-finite coordinates,
unsupported residues/elements and residues without exactly one C-alpha atom are rejected.
Missing side-chain atoms are not reconstructed or checked for completeness, so counts
describe the atoms supplied. A zero clash count is not a complete physical-quality check;
the distance heuristic is not a radii-based MolProbity clashscore or an energy function.

## Map hotspots before comparing structures

The original reference is read using author chain/residue IDs. Fixed target segments are
concatenated in the task's declared order, and their sequence must exactly equal the folded
target sequence. Missing residues, insertion codes, repeated source identities, sequence
differences and unmapped or duplicate hotspots fail the analysis rather than guessing a
mapping. Output positions are one-based sequence indices, independent of CIF residue IDs.

For the PD-L1 development task, reference chain B20–133 becomes target sequence positions
1–114. Thus B64/68/126/128 map to positions **45/49/107/109**, respectively. Generated
chains follow molecule order; ESMFold2 uses binder A and target B. Both geometries use
the same sequence-position mapping. A hotspot is a specified residue, not a complete
epitope definition; contacting all supplied hotspots would not prove epitope specificity.

## Output and integrity

Each invocation creates `artifacts/interface-feedback/<analysis-id>/` containing:

- `request.json`, `protocol.json`, `status.json`: inputs, frozen rules, outcome and analysis time.
- `observations.json`: confidence, generated/predicted geometry, residue mapping, original
  and retained contact pairs, RMSDs, input hashes and analysis software versions.
- `candidates.csv`, `report.md`: all selected candidates, sorted by generation seed.
- `feedback.py`, `interface.py`, `manifest.json`: implementation snapshot and output checksums.
- `control-4ZQK.cif`, when requested: exact experimental control coordinates.

Generation manifests, requests, completion receipts, output checksums and semantic model
outputs are validated before producing observations. The original target reference must
also match its recorded checksum. Batches with a different generation/model protocol or
overlapping candidate IDs are rejected. Subsets are allowed and counted explicitly;
this command does not imply coverage of an entire generation. Completed model reports
and earlier analyses are left unchanged. Failures retain their request, error, protocol,
implementation and manifest in a separate analysis directory.

`flags` currently reports `NO_TARGET_CONTACT`, `NO_REQUESTED_HOTSPOT_CONTACT`,
`PARTIAL_REQUESTED_HOTSPOT_CONTACT` and `INTERCHAIN_HEAVY_ATOM_CLASH`. They describe
observations, not proven failure causes or automatic regeneration instructions.
`acceptance` remains `null` and `binding_validated` is `false`; no biological pass threshold
has been calibrated and no AF3 threshold is transferred. These records are an input for
future Agent and non-LLM policies, not an implemented Agent loop.

## Geometry controls and development evidence

The optional control is the exact public [4ZQK PD-1/PD-L1 experimental
structure](https://www.rcsb.org/structure/4ZQK), with author chains B/A. The protocol pins
the downloaded CIF's SHA-256. It checks native contacts, removal of contacts after a
1000 Å binder translation, detection of a forced C-alpha overlap, and invariance of
contact/clash counts under a rigid transform of the whole complex. An unexpected result
fails the analysis. Translated structures are synthetic geometry controls, not measured
non-binders. These controls test the calculation; they do not test ESMFold2 predictions
against experimental complexes or calibrate its confidence. The 4ZQK construct differs
from the development target, so requested hotspots are not transferred to this control.

All 16 saved PD-L1 candidates have been analyzed, with no further GPU inference. See
[the complete development evidence](../../docs/evidence/INTERFACE_FEEDBACK_PDL1_16.md).
Further quality/Agent comparisons require predefined screening criteria, independent
targets and suitable predictor controls.

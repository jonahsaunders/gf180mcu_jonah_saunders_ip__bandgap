TRIM-INCLUSIVE BANDGAP LAYOUT — CORE CANDIDATE, NOT TAPEOUT QUALIFIED

This directory implements Bandgap_Core_Res, the DUT in TB10_RESISTOR_TRIM.
The core contains all 272 device units, seven trim controls, separate analog
and digital grounds, 1 kOhm/square poly resistors, and eleven MIM capacitors.
It is an IP block for the Chipalooza harness, not a standalone padframe.

Current release
---------------
release/Bandgap_Core_Res.gds is the routed and filled core.
release/verification.json binds the DRC/LVS evidence to its file hashes.
release/filled_drc.lyrdb and release/filled.lvsdb.gz open in KLayout.
release/reference.spice is the schematic-derived LVS reference. Zero-volt
current probes become net aliases; transistor multiplicities become units.
report.html explains the research, floorplan, results, and open work.
figures/index.html presents the paper-style figure gallery and full captions.
The PSRR revision separates physical routing improvements from extraction
corrections. Raw Magic RC and capacitance-conserving RCC results are both
retained; the latter is an approximation, not a foundry signoff extraction.

STATUS: the complete, unedited GF180MCU-D KLayout DRC deck reports zero
violations and LVS matches. This is NOT a claim that every uncoded foundry
recommendation, analog specification, or Chipalooza integration requirement
has passed. Read the open items in report.html before using this layout.

Reproduction
------------
Use this exact container image:
hpretl/iic-osic-tools@sha256:5d6adf1f437cd0f2f8f8614488ec3c247ba8c768f4663a25d5e997b30ccb13b0

Linux command, from the repository root:

docker run --rm --network none --mount type=bind,source="$PWD",target=/design \
  --entrypoint /bin/bash \
  hpretl/iic-osic-tools@sha256:5d6adf1f437cd0f2f8f8614488ec3c247ba8c768f4663a25d5e997b30ccb13b0 \
  -lc 'bash /design/layout/build.sh build'

Replace the last word with:
  verify   rerun full DRC/LVS on layout/build/filled_candidate.gds
  extract  prepare the extraction view and independently audit its topology
  screen   schematic/C/raw-RC/conserved-RCC comparison, trim and startup
  pvt      360 fixed-code DC points across process, supply, and temperature
  all      build, verify, extract, nominal screen, and fixed-code DC screen

Run extract after build, and before screen or pvt. Results go to layout/build/
and are ignored by Git. BANDGAP_LAYOUT_BUILD can select another build folder.
The tools fail when DRC or LVS fails; a tool's zero exit code alone is not
accepted as verification. GDS timestamps may differ between rebuilds.

Extraction now requires Magic >= 8.3.679. For the exact tested version, run:

docker run --rm --user root --mount type=bind,source="$PWD",target=/design \
  --entrypoint /bin/bash \
  hpretl/iic-osic-tools@sha256:5d6adf1f437cd0f2f8f8614488ec3c247ba8c768f4663a25d5e997b30ccb13b0 \
  -lc 'bash /design/layout/build_magic.sh'

This preparation requires network access. It
installs compiler dependencies only in that disposable container and builds
official Magic commit bed8d4ff84333767aa923d469d339cd05c56a00a (8.3.685) under
layout/build/toolchain. The extraction stage discovers that installation.
Alternatively set BANDGAP_MAGIC to its absolute executable path. Keep the
same repository mount path when building and running the installed tool.
No PDK deck or model is patched by this procedure.

Run scripts/diagnose_psrr.py after screen to reproduce the AC ablations and
the zero-resistance capacitance-conservation check. Optional --sweep and
--pairs generate diagnostic counterfactuals, never deliverable layout models.

Toolchain
---------
GF180MCU-D PDK revision f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7
Primitive generation: bundled Magic 8.3.678. Extraction: official Magic 8.3.685.
KLayout 0.30.9; ngspice 46; Xschem 3.4.8RC.
Five metals, 11K top metal, MIM option B / 2 fF per square micron, poly_res=1k.
The lower-case 1k parameter is required by this LVS deck.

Source guide
------------
scripts/render_paper_figures.py  regenerate nine figures from release and planning inputs
scripts/figure_style.py      shared typography and PDF/SVG/600-dpi PNG export
scripts/layout_flow.py       explicit orchestration and verification gates
scripts/layout_paths.py      build and PDK paths
scripts/audit_circuit.py     literal numeric SPICE parser; no eval
scripts/generate_primitives.py  PDK geometry generation, one cell per geometry
scripts/place_layout.py      common-centroid unit arrays and placement
scripts/route_physical.py    three-layer signal routing and substrate isolation
scripts/lift_sensitive_routes.py  move the long vgn2 route to M5
scripts/strengthen_power.py  2 um M5 supplies and four-via landing arrays
scripts/add_density_fill.py  deterministic COMP and metal fill with keepouts
scripts/prepare_pex_view.py   geometry-identical PNP/nwell extraction hierarchy
scripts/extract_parasitics.tcl  visible Magic extraction commands
scripts/audit_pex_topology.py   collapse interconnect R and create LVS audit input
scripts/conserve_pex_capacitance.py  preserve the C-only net-pair matrix in RCC
scripts/diagnose_psrr.py      repeatable AC sensitivity and conservation checks
scripts/screen_extracted.py  nominal post-layout screens, with input fingerprints
scripts/screen_power_sequence.py  both rails start/restart from zero together
scripts/screen_pvt_dc.py      limited fixed-code cross-corner DC screen
scripts/draw_layout.py       figures from actual GDS and placement coordinates
scripts/plot_screens.py      plots from saved nominal and cross-corner results

The scripts are design-specific and intentionally readable. There are no
encoded payloads or generated Python execution. Reviewers can inspect each
stage independently. Changing circuit geometry may require changing the
placement, access-point rules, or primitive recognition, then rebuilding.

Extraction limitations
----------------------
The PNP cell is imported from foundry GDS. Magic misidentifies that flat cell
and the well resistor, so an extraction-only hierarchy restores their verified
primitive models. A layer XOR checks that geometry is unchanged. A separate
KLayout LVS audit compares both the raw device extraction and the RC network
with interconnect resistances collapsed to the actual GDS.

Magic extresist falsely joins AVSS and DVSS when ground resistance is enabled.
The screening RC/RCC models therefore explicitly exclude resistance on both
grounds. It includes signal/supply resistance, capacitance and coupling above
0.1 fF. This is not complete distributed RC sign-off. The pre-existing project
MIM charge-form compatibility model remains in use. Resistor tee splitting is
disabled so the extracted primitive topology can be independently verified.
Magic 8.3.685 corrects the old double-counted sidewall coefficient. A separate
extresist issue remains: its rnode capacitances are derived from coupling
capacitors, and the RC export also keeps the original couplings. Collapsing
all wire resistances in that raw RC model therefore fails to reproduce the
C-only response. The raw export is preserved as *_rc_simulation.spice.

The *_rcc_simulation.spice approximation keeps raw RC devices and resistors
and rescales parasitic capacitors by original net pair to conserve the C-only
matrix. Relative positions along each wire remain approximate. The release
includes per-pair totals, source hashes, and an AC check that zeroing all wire
resistances recovers C-only PSRR at 481 frequencies for both supplies. This
check establishes internal consistency, not independent field-solver accuracy.
Source: https://iic-jku.github.io/klayout-pex-website/doc/doc.html

Known integration and analog work
--------------------------------
Updated planning target: approximately 1270 x 410 um. On 20 October 2026 the
designer supplied an organizer announcement describing this draft user area.
planning_envelope.json preserves its wording and provisional status. It
supersedes the earlier inferred 537.15 x 273 um envelope and wrapper scaling
discrepancy for floorplanning purposes.

The current 614.0 x 307.6 um core fits geometrically and occupies 36.3% of
the draft area. Centering it would leave 328 um on each side and 51.2 um
above/below. These are geometric margins, not verified integration clearances.
Final wrapper geometry, slot, pin locations and wiring remain pending.
The initial-floorplan figure is historical; harness-area-fit is the current
planning view. Neither changes the actual GDS.

Compaction to the old smaller envelope is no longer a priority. Keep the
analog core compact, shorten sensitive amplifier/C1 connections, and use
spare area selectively for matching dummies, isolation, supply routing and
harness access. Spreading the circuit across the full width would lengthen
sensitive nets; any relocation needs extraction and analog requalification.

Exact geometric centroids and equal device orientation are implemented for
the named matching groups. Edge dummies, matched interconnect resistance/
capacitance, complete shielding, and a quantitative EM/IR review remain open.
The power straps do not replace those checks. The corrected nominal PSRR
screen exceeds the README planning threshold of 45 dB, but does not meet the
original testbench's 60 dB criterion; the schematic itself also misses 60 dB.
Full corner startup, stability, noise, mismatch, calibrated trim,
pad/multiplexer loading, and all device-voltage checks remain to be qualified.

Three source dimensions were snapped to the manufacturing grid: N3 W 0.646
to 0.650 um, R3 L 20.193 to 20.190 um, R4 L 232.532 to 232.530 um. The same
changes were applied to all three core schematic variants and their ten
schematic smoke tests were rerun. This is separate from post-layout checking.

Foundry-derived geometry/models retain LICENSE-GF180.txt. No PDK source or
verification rule was edited or weakened to obtain a passing report.

Paper figure reproduction (no circuit simulation)
------------------------------------------------
From the repository root, in the same container environment:

  python layout/scripts/render_paper_figures.py

Use --out DIRECTORY to export elsewhere. The script verifies release hashes
and the uncompressed hashes of saved samples in figure-data/ before rendering.
All archived simulation samples are preserved verbatim with gzip compression.
No curve fitting, smoothing, or numerical adjustment is applied. The GDS,
netlists, DRC/LVS evidence, and PSRR results are unchanged by this restyling.

Each figure has PDF, SVG, and 600-dpi PNG versions. Plots, labels and box
diagrams are vector; dense GDS polygons are rasterized at 600 dpi inside the
vector exports. Master width is 7.1 inches with 7–9 point labels. Use dense
composites at that width or repanel them before reducing to a narrow column.
captions.txt records conditions and limitations for reuse with the artwork.
The style follows https://www.isscc.org/figures readability guidance, without
claiming compliance with an ISSCC manuscript template or submission rules.

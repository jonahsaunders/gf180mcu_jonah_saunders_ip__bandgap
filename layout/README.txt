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
  /design/layout/build.sh build

Replace the last word with:
  verify   rerun full DRC/LVS on layout/build/filled_candidate.gds
  extract  prepare the extraction view and independently audit its topology
  screen   nominal schematic/C/RC comparison, all trim codes, startup/restart
  pvt      360 fixed-code DC points across process, supply, and temperature
  all      build, verify, extract, nominal screen, and fixed-code DC screen

Run extract after build, and before screen or pvt. Results go to layout/build/
and are ignored by Git. BANDGAP_LAYOUT_BUILD can select another build folder.
The tools fail when DRC or LVS fails; a tool's zero exit code alone is not
accepted as verification. GDS timestamps may differ between rebuilds.

Toolchain
---------
GF180MCU-D PDK revision f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7
Magic 8.3.678; KLayout 0.30.9; ngspice 46; Xschem 3.4.8RC.
Five metals, 11K top metal, MIM option B / 2 fF per square micron, poly_res=1k.
The lower-case 1k parameter is required by this LVS deck.

Source guide
------------
scripts/layout_flow.py       explicit orchestration and verification gates
scripts/layout_paths.py      build and PDK paths
scripts/audit_circuit.py     literal numeric SPICE parser; no eval
scripts/generate_primitives.py  PDK geometry generation, one cell per geometry
scripts/place_layout.py      common-centroid unit arrays and placement
scripts/route_physical.py    three-layer signal routing and substrate isolation
scripts/strengthen_power.py  2 um M5 supplies and four-via landing arrays
scripts/add_density_fill.py  deterministic COMP and metal fill with keepouts
scripts/prepare_pex_view.py   geometry-identical PNP/nwell extraction hierarchy
scripts/extract_parasitics.tcl  visible Magic extraction commands
scripts/audit_pex_topology.py   collapse interconnect R and create LVS audit input
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
The screening RC model therefore explicitly excludes resistance on both
grounds. It includes signal/supply resistance, capacitance and coupling above
0.1 fF. This is not complete distributed RC sign-off. The pre-existing project
MIM charge-form compatibility model remains in use. Resistor tee splitting is
disabled so the extracted primitive topology can be independently verified.
Magic 8.3.678 also predates the sidewall-capacitance correction identified in
the KLayout-PEX documentation (8.3.679 or newer is required for its wrapper).
These capacitance/RC screens need revalidation with a corrected extractor.
Source: https://iic-jku.github.io/klayout-pex-website/doc/doc.html

Known integration and analog work
--------------------------------
No slot has been assigned publicly or supplied by the designer. The public
wrapper script says 537.15 x 273 um, but the checked-in wrapper scales to about
619.785 x 315 um with this pinned Magic PDK. The present core does not fit the
smaller dimensions. No slot number, pad assignment, or final wrapper wiring
is invented here. The initial floorplan was a concept, not a fit guarantee.

Exact geometric centroids and equal device orientation are implemented for
the named matching groups. Edge dummies, matched interconnect resistance/
capacitance, complete shielding, and a quantitative EM/IR review remain open.
The power straps do not replace those checks. Post-layout PSRR misses existing
targets. Full corner startup, stability, noise, mismatch, calibrated trim,
pad/multiplexer loading, and all device-voltage checks remain to be qualified.

Three source dimensions were snapped to the manufacturing grid: N3 W 0.646
to 0.650 um, R3 L 20.193 to 20.190 um, R4 L 232.532 to 232.530 um. The same
changes were applied to all three core schematic variants and their ten
schematic smoke tests were rerun. This is separate from post-layout checking.

Foundry-derived geometry/models retain LICENSE-GF180.txt. No PDK source or
verification rule was edited or weakened to obtain a passing report.

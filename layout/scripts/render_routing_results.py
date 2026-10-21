"""Render compact-routing comparisons from archived, independently checked data."""

import json
from pathlib import Path

import numpy as np

from render_matching_results import PAIRS, draw_currents, ratio, selected

LAYOUT = Path(__file__).resolve().parents[1]
RELEASE = LAYOUT / "release"
OUT = LAYOUT / "reviews/2026-10-21-compact-routing"


def read(path):
    return json.loads(path.read_text())


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    verification = read(RELEASE / "verification.json")
    assert verification["drc_violations"] == 0 and verification["lvs_match"]
    old = read(RELEASE / "previous/mirror_bias_summary.json")
    new = read(RELEASE / "mirror_bias/summary.json")
    old_precision = read(RELEASE / "previous/precision_layout_audit.json")
    precision = read(RELEASE / "precision_layout_audit.json")
    geometry = read(RELEASE / "mirror_geometry_audit.json")
    assert len(geometry["matched_source_buses"]) == 3
    assert all(row["verified_in_gds"] for row in geometry["matched_source_buses"])
    draw_currents(old, new, OUT, "Routing revision")
    rows = []
    table = "| Current ratio | Before, 3.3 V | After, 3.3 V | Before, 5 V | After, 5 V |\n| :--- | ---: | ---: | ---: | ---: |\n"
    for pair in PAIRS:
        row = {"pair": list(pair)}
        values = []
        for supply in [3.3, 5.0]:
            for label, records in [("previous", old), ("revised", new)]:
                value = ratio(selected(records, "rcc", supply), pair)
                row[f"{label}_{supply:g}V_percent"] = value
                values.append(f"{value:+.5f}%")
            row[f"schematic_{supply:g}V_percent"] = ratio(
                selected(new, "schematic", supply), pair
            )
        table += f"| {' / '.join(pair)} | " + " | ".join(values) + " |\n"
        rows.append(row)
    ac_rows = []
    ac_table = "| AVDD | Previous GDS, ground R omitted | Previous GDS, ground metal R included | Revised GDS, ground metal R included | Physical change |\n| :--- | ---: | ---: | ---: | ---: |\n"
    published = read(RELEASE / "previous/published_psrr_summary.json")
    for supply in [3.3, 5.0]:
        before = np.loadtxt(RELEASE / f"psrr/rcc_{supply:g}_baseline.dat", skiprows=1)
        after = np.loadtxt(RELEASE / f"psrr/rcc_{supply:g}_revised.dat", skiprows=1)
        assert before.shape == after.shape == (481, 2)
        assert np.array_equal(before[:, 0], after[:, 0])
        initial = next(
            r["psrr_min_dB"]
            for r in published
            if r["name"] == "rcc" and r["supply"] == supply
        )
        first, last = float(before[:, 1].min()), float(after[:, 1].min())
        ac_rows.append(
            {
                "supply_V": supply,
                "published_without_ground_R_dB": initial,
                "previous_min_dB": first,
                "revised_min_dB": last,
                "change_dB": last - first,
                "frequency_points": 481,
            }
        )
        ac_table += f"| {supply:g} V | {initial:.2f} dB | {first:.2f} dB | {last:.2f} dB | {last-first:+.2f} dB |\n"
    resistance_table = "| Source group | Before: mean individual path R | After: mean individual path R |\n| :--- | ---: | ---: |\n"
    for rail, names in [
        ("avdd", ["PLOAD1", "PLOAD2"]),
        ("avss", ["MSINK1", "MSINK2", "VBNSINK"]),
    ]:
        values = []
        for name in names:
            values = [
                audit["source_resistance"]["rails"][rail][name][
                    "mean_individual_source_to_port_ohm"
                ]
                for audit in [old_precision, precision]
            ]
            resistance_table += f"| {name} | {values[0]:.3f} Ω | {values[1]:.3f} Ω |\n"
    result = {
        "gds_sha256": verification["sha256"]["Bandgap_Core_Res.gds"],
        "mirror_ratios": rows,
        "psrr": ac_rows,
    }
    (OUT / "comparison.json").write_text(json.dumps(result, indent=2) + "\n")
    r1 = next(row for row in precision["devices"] if row["source"] == "R1")
    max_probe_error = (
        max(abs(row["vref_difference_from_saved_screen_V"]) for row in new) * 1e6
    )
    text = f"""# Compact routing and ground-return revision

**21 October 2026 · Trim-inclusive TB10 core · 686.0 × 399.6 µm**

Device placement, circuit dimensions, native dummy symbols and the two-row capacitor bank remain unchanged. This revision improves interconnect inside the existing footprint. The complete unedited GF180MCU-D DRC deck reports **0 violations**, core LVS matches all **533 units**, and both extraction-topology LVS audits pass. [Release manifest](../../release/verification.json) · [Independent rebuild](../../release/rebuild_equivalence.json).

## Implemented changes

- Equal-length source escapes, 2 µm M3 row buses and 2 µm M4 trunks now serve PLOAD1/PLOAD2 and MSINK1/MSINK2, as well as the existing M1/M2/M5 bus. The independent GDS audit verifies all three buses and their vias. Functional device sizes and multiplicities are unchanged.
- Separate 2 µm M5 AVSS and DVSS reinforcement uses redundant 2 × 2 via arrays. Inaccessible optional landings keep their existing lower-metal connections. Analog and digital returns remain electrically separate within the core.
- Unrelated routes are excluded from the PNP-array region and the PTAT resistor R1. R1 has **{len(r1['unrelated_routes_over_device'])} unrelated-net crossings over its poly body**, compared with six in the previous layout. Existing MOS matching exclusions and full dummy perimeters remain in force. The long vgn2 route retains its upper-metal treatment; an experimental xf2 promotion was not adopted.
- The precision-device audit confirms the Q1/Q2 emitter centroid separation is **0 µm**, with identical unit emitter areas. R1 has eight contacts at each end; each trim/fixed poly resistor has fourteen at each end. This does not establish identical stress, temperature or parasitic surroundings for the entire trim ladder.

![Current physical layout](../../figures/physical-layout.png)

[Floorplan](../../figures/implemented-floorplan.pdf) · [Matching audit](../../release/mirror_geometry_audit.json) · [Precision audit](../../release/precision_layout_audit.json).

These choices follow the foundry's [MP.1–MP.13 matching guidelines](https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_10_6_2.html), including consistent unit geometry, nearby placement, dummy surroundings and similar node interconnect. The foundry explicitly includes [BJTs and resistors](https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_10_6_1.html) in matching guidance. These rules are not all encoded in DRC; the geometry and electrical audits are separate evidence.

## Nominal current matching

TT, 25 °C, trim 72, DVDD = 3.3 V, ideal external supply/ground connections and no added output load. Values are **100 × (|Ia| / |Ib| − 1)**. Both layouts use the same extraction method including ground metal resistance. Independent series-drain probes reproduce unprobed VREF within **{max_probe_error:.3f} µV**. These are deterministic ratios, not random-mismatch yield.

{table}

![Mirror current comparison](mirror-current-ratios.png)

[Vector PDF](mirror-current-ratios.pdf) · [All currents and biases](../../release/mirror_bias/summary.json).

The source-resistance audit solves each extracted rail network with a 1 A test current at one source terminal and the supply port fixed at zero. Other terminals are open. The values below are averages of the resulting individual driving-point resistances; they are neither summed operating drops nor equivalent resistance of all sources in parallel.

{resistance_table}

Equal average source resistance does not imply equal complete drain/gate/body parasitics. Bias-dependent PRMIR/PREF and M18/M16 errors remain visible in the table and schematic comparison. No schematic feedback or device resizing was used to conceal them.

The gains are not uniform: the M18/M16 error increases from 3.9943% to 4.0230% at 3.3 V and from 9.6344% to 9.6867% at 5 V. Its unequal circuit bias remains a larger limitation than the small interconnect-induced change.

## PSRR and the extraction correction

Minimum PSRR is sampled at **481 common frequencies from 1 Hz to 1 MHz**. The unchanged previous GDS at commit `9b2f539` was re-extracted before comparing physical changes.

{ac_table}

![Controlled PSRR comparison](../../figures/psrr-before-after.png)

The stated gain is in the worst sampled PSRR, not at every frequency. For example, the low-frequency 3.3 V response decreases toward the schematic response; the complete curves show this tradeoff. The protected-routing choice differs from the unprotected ground-strap trial by only −0.005/−0.016 dB in the two minima. [Compound routing trials](../../release/routing_experiments.json).

The old published model omitted AVSS/DVSS resistance because unrestricted Magic resistance extraction joined their well/substrate networks. The new flow skips distributed `space`, `pwell` and `nwell` resistance individually while retaining metal/contact/via resistance on both grounds. Each skip is a separate command in the tested Magic 8.3.685 implementation. [Magic extraction reference](https://opencircuitdesign.com/magic/commandref/extresist.html) · [Visible extraction commands](../../scripts/extract_parasitics.tcl).

Both GDS versions pass independent topology LVS with this method, including an audit with interconnect resistors collapsed. The capacitance-conserving RCC adapter preserves the capacitance-only net-pair totals; zeroing wire resistance recovers the capacitance-only AC result within 0.001 dB. These are consistency checks, not independent field-solver validation. The resistance of wells/substrate remains lumped, and distributed coupling placement remains approximate. No foundry model or verification deck was weakened.

[Baseline provenance and raw extraction](../../release/previous/provenance.json) · [Capacitance check](../../release/psrr/conservation_ac_check.json) · [All AC diagnostics](../../release/psrr/summary.json) · [Numerical comparison](comparison.json).

## Qualification and reproduction

The native-symbol schematic is unchanged and remains the full LVS reference, including all 261 dummy units. The original pre-layout chart and simulation information remain in the main README. Previous published simulations are retained as history, with their original GDS and extraction assumptions identified.

All ten nominal DC/AC cases and 360 fixed-code corner DC points complete. Additional analog-only and simultaneous-rail transient attempts exceeded the 300 s per-case limit; startup/restart is not qualified on this revision. Their input decks and partial logs are retained, and old startup values are not presented as new results. [Attempt status](../../release/incomplete-transients/status.json).

Neither the schematic nor layout reaches the existing 60 dB PSRR testbench criterion. Statistical mismatch/trim yield, independent PEX accuracy, distributed well/substrate resistance, complete branch balance, thermal/stress effects, EM/IR, stability, noise, loading and final harness integration remain open. Refer to the current release manifest for the exact completed screens; do not apply historical startup numbers to a new GDS.

Use the pinned environment in [layout reproduction](../../README.txt). Build and extract, then run the nominal screens, `screen_mirror_bias.py --build <build> --reference-ac <build>/psrr_diagnostics`, `audit_mirror_layout.py`, and `audit_precision_layout.py`. Generate the unprobed reference first with `diagnose_psrr.py --build <build> --output <build>/psrr_diagnostics --supply-coupling`. `render_routing_results.py` regenerates this report from the archived release.
"""
    (OUT / "README.md").write_text(text, encoding="utf-8")
    print("Rendered compact routing review:", OUT)


if __name__ == "__main__":
    main()

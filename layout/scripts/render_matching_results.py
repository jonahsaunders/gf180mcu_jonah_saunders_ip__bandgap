"""Render the implemented mirror review from archived physical and DC evidence."""

import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

from figure_style import WIDTH, BLUE, GRAY, INK, configure, export, plot_axes

LAYOUT = Path(__file__).resolve().parents[1]
RELEASE = LAYOUT / "release"
OUT = LAYOUT / "reviews/2026-10-21-mirrors-implemented"
PAIRS = [
    ("M1", "M2"),
    ("M5", "M2"),
    ("PLOAD2", "PLOAD1"),
    ("MSINK2", "MSINK1"),
    ("MFOLD2", "MFOLD1"),
    ("PRMIR", "PREF"),
    ("M18", "M16"),
]


def read(path):
    return json.loads(path.read_text())


def ratio(record, pair):
    a, b = pair
    return 100 * (
        record["devices"][a]["current_uA"] / record["devices"][b]["current_uA"] - 1
    )


def selected(records, mode, supply):
    return next(r for r in records if r["mode"] == mode and r["AVDD_V"] == supply)


def draw_currents(old, new):
    configure()
    fig, axes = plt.subplots(
        2, 2, figsize=(WIDTH, 5.2), gridspec_kw={"height_ratios": [1.35, 1]}
    )
    fig.subplots_adjust(
        left=0.11, right=0.98, bottom=0.10, top=0.83, hspace=0.65, wspace=0.32
    )
    series = [
        (new, "schematic", "Schematic", INK),
        (old, "rcc", "Previous layout", GRAY),
        (new, "rcc", "Matching revision", BLUE),
    ]
    for col, supply in enumerate([3.3, 5.0]):
        for row, pairs in enumerate([PAIRS[:5], PAIRS[5:]]):
            ax = axes[row, col]
            positions = np.arange(len(pairs))
            for index, (records, mode, label, color) in enumerate(series):
                record = selected(records, mode, supply)
                values = [
                    ratio(record, pair) * (1e4 if row == 0 else 1) for pair in pairs
                ]
                ax.bar(
                    positions + (index - 1) * 0.24,
                    values,
                    width=0.22,
                    color=color,
                    label=label,
                )
            ax.axhline(0, color=INK, lw=0.5)
            ax.set_xticks(positions, [f"{a}/\n{b}" for a, b in pairs], fontsize=7)
            plot_axes(
                ax,
                "",
                "Ratio error (ppm)" if row == 0 else "Ratio error (%)",
                f"AVDD = {supply:g} V" if row == 0 else "Bias-dependent ratios",
            )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=3,
        fontsize=8,
    )
    fig.text(
        0.5,
        0.925,
        "Nominal DC • TT • 25 °C • trim 72 • DVDD = 3.3 V",
        ha="center",
        fontsize=8,
    )
    export(fig, OUT, "mirror-current-ratios")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    evidence = read(RELEASE / "verification.json")
    geometry = read(RELEASE / "mirror_geometry_audit.json")
    old = read(RELEASE / "previous/mirror_bias_summary.json")
    new = read(RELEASE / "mirror_bias/summary.json")
    assert evidence["drc_violations"] == 0 and evidence["lvs_match"]
    assert len(geometry["matched_groups"]) == 13
    assert not any(
        g["foreign_routes_over_channels"] for g in geometry["matched_groups"]
    )
    assert all(
        g["dummy_verification"]["complete_perimeter"]
        for g in geometry["matched_groups"]
    )
    rows = []
    for pair in PAIRS:
        row = {"pair": list(pair)}
        for supply in [3.3, 5.0]:
            for label, records, mode in [
                ("schematic", new, "schematic"),
                ("previous", old, "rcc"),
                ("revised", new, "rcc"),
            ]:
                row[f"{label}_{supply:g}V_percent"] = ratio(
                    selected(records, mode, supply), pair
                )
        rows.append(row)
    psrr = read(RELEASE / "psrr/summary.json")
    comparison = []
    for supply in [3.3, 5.0]:
        before = np.loadtxt(RELEASE / f"psrr/rcc_{supply:g}_baseline.dat", skiprows=1)
        after = np.loadtxt(RELEASE / f"psrr/rcc_{supply:g}_revised.dat", skiprows=1)
        assert np.array_equal(before[:, 0], after[:, 0])
        schematic = next(
            r for r in psrr if r["name"] == "schematic" and r["supply"] == supply
        )
        comparison.append(
            {
                "supply_V": supply,
                "previous_min_dB": float(before[:, 1].min()),
                "revised_min_dB": float(after[:, 1].min()),
                "change_dB": float(after[:, 1].min() - before[:, 1].min()),
                "schematic_min_dB": schematic["psrr_min_dB"],
                "frequency_points": len(after),
            }
        )
    (OUT / "comparison.json").write_text(
        json.dumps(
            {
                "gds_sha256": evidence["sha256"]["Bandgap_Core_Res.gds"],
                "mirror_ratios": rows,
                "psrr": comparison,
            },
            indent=2,
        )
        + "\n"
    )
    draw_currents(old, new)
    table = "| Branch ratio | Schematic 3.3 V | Previous 3.3 V | Revised 3.3 V | Schematic 5 V | Previous 5 V | Revised 5 V |\n| :--- | ---: | ---: | ---: | ---: | ---: | ---: |\n"
    for row in rows:
        values = [
            row[f"{mode}_{supply:g}V_percent"]
            for supply in [3.3, 5.0]
            for mode in ["schematic", "previous", "revised"]
        ]
        table += (
            "| "
            + " / ".join(row["pair"])
            + " | "
            + " | ".join(f"{value:+.4f}%" for value in values)
            + " |\n"
        )
    psrr_table = "| AVDD | Previous RCC | Revised RCC | Change | Schematic |\n| :--- | ---: | ---: | ---: | ---: |\n"
    for row in comparison:
        psrr_table += f"| {row['supply_V']:g} V | {row['previous_min_dB']:.2f} dB | {row['revised_min_dB']:.2f} dB | {row['change_dB']:+.2f} dB | {row['schematic_min_dB']:.2f} dB |\n"
    positions = "| Related devices | Previous separation | Revised separation |\n| :--- | ---: | ---: |\n"
    diagnostic_table = "| Diagnostic model | Minimum at 3.3 V | Minimum at 5 V |\n| :--- | ---: | ---: |\n"
    for name, label in [
        ("schematic", "Schematic"),
        ("magic_lvs", "Extracted devices, no wiring parasitics"),
        ("c", "Extracted devices + capacitance"),
        ("rcc", "Conserved RCC (delivered screen)"),
        ("rcc_drop_avdd_vgn2", "Diagnostic only: remove AVDD–vgn2 coupling"),
        ("rcc_drop_avdd_xf2", "Diagnostic only: remove AVDD–xf2 coupling"),
    ]:
        values = [
            next(
                r["psrr_min_dB"]
                for r in psrr
                if r["name"] == name and r["supply"] == supply
            )
            for supply in [3.3, 5.0]
        ]
        diagnostic_table += f"| {label} | {values[0]:.2f} dB | {values[1]:.2f} dB |\n"
    previous_geometry = read(
        LAYOUT / "reviews/2026-10-21-mirrors/mirror_geometry_audit.json"
    )
    distances = {
        tuple(r["names"]): r["centroid_separation_um"]
        for r in previous_geometry["additional_pair_distances"]
    }
    for row in geometry["additional_pair_distances"]:
        key = tuple(row["names"])
        positions += f"| {' / '.join(key)} | {distances[key]:.2f} µm | {row['centroid_separation_um']:.2f} µm |\n"
    bounds = evidence["bounds_um"]
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    max_probe_error = (
        max(abs(r["vref_difference_from_saved_screen_V"]) for r in new) * 1e6
    )
    text = f"""# Implemented mirror-layout revision

**21 October 2026 · Trim-inclusive TB10 core · DRC clean and LVS matched**

This revision implements the physical improvements from the [preceding review](../2026-10-21-mirrors/README.md). The core measures **{width:.1f} × {height:.1f} µm** and contains **272 unchanged functional device units plus 261 explicit tied-off dummy MOS units**. The earlier compact layout is retained in Git at `f249c5c`; that same GDS was re-extracted with the current extraction anchors for the controlled before-layout baseline below. The original pre-layout temperature-trim study remains in the main README.

## Physical changes and verification

1. **Protected matching arrays.** All 13 selected MOS arrays have complete dummy perimeters, identical unit orientation, and quiet terminal ties: D/G/S/B to AVDD for PMOS or AVSS for NMOS. Dummies are present in the visible schematic netlist and included in the unedited LVS comparison. No dummy-device filtering is used.
2. **Balanced geometry.** The M1/M2/M5 cyclic pattern and the M11/M12/M13 pattern have equal centroids and equal second spatial moments within each group. The M3/M4 pair uses ABBA. Single-unit MFOLD1/MFOLD2 and M16/M18 are adjacent with common dummy surroundings; they are not described as common-centroid arrays. The odd-count input/bias pairs retain equal centroids, but their second moments are not equal.
3. **Local drain and source connections.** M1/M2/M5 have equal local M2/M3/M4 drain-route lengths, metal areas and via counts. The independent connectivity audit compares the actual GDS to that local routing plan with zero polygon XOR. Equal-length source escapes connect to 2 µm M3 row buses and a shared 2 µm M4 trunk, reducing differential supply drops. The source-bus geometry and all 18 connections are independently checked in GDS. This does not establish equal complete branch resistance or capacitance outside the array.
4. **Proximity and current paths.** Related bias devices are clustered, and the core mirrors, PNPs and PTAT resistor sit beside the trim ladder. Two-micrometer M5 reinforcement is added to VREF, PTAT and selected trim-current routes. Some optional M5 landings remain inaccessible; their verified lower-metal connections remain in place. These routes are not a Kelvin implementation or completed EM/IR signoff.
5. **Manufacturing and independent checks.** All 13 arrays have zero unrelated-net overlap over their channels and no metal-fill overlap. The complete unedited GF180MCU-D DRC deck reports **0 violations**, including density checks; LVS matches all **533 units**. Both extraction-topology LVS audits pass. A fresh source rebuild has zero GDS polygon/text differences.

![Implemented floorplan](../../figures/implemented-floorplan.png)

*Actual saved placement. [Matching arrays](../../figures/matched-arrays.pdf) · [Annotated GDS](../../figures/physical-layout.pdf) · [Geometry audit](../../release/mirror_geometry_audit.json) · [Rebuild comparison](../../release/rebuild_equivalence.json).*

{positions}

These decisions follow [GF180 MP.1–MP.13](https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_10_6_2.html), [Cadence current-mirror layout guidance](https://community.cadence.com/cadence_blogs_8/b/cic/posts/current-mirrors-in-analog-layout), and [Lan et al., MWSCAS 1999](https://class.ece.iastate.edu/vlsi2/docs/Papers%20Done/1999-08-MWSCAS-ML.pdf). GF180 lists matching guidance among its [uncoded rules](https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_16.html); DRC alone is not matching qualification. Dummy poly and metal fill follow the published [poly-fill](https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_13_2.html) and [metal-fill](https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_13_3.html) guidance.

## Nominal mirror matching

Typical process, 25 °C, trim 72, DVDD = 3.3 V. Each entry is **100 × (|Ia| / |Ib| − 1)**. Four new diagnostic DC runs use ideal series drain probes; their VREF values reproduce the independent nominal screens within **{max_probe_error:.3f} µV**. These are deterministic current ratios, not Monte Carlo mismatch results.

{table}

![Nominal branch current ratios before and after the matching revision](mirror-current-ratios.png)

*Upper panels use ppm; lower panels use percent. [Vector PDF](mirror-current-ratios.pdf) · [Per-unit currents and terminal biases](../../release/mirror_bias/summary.json).*

The larger M18/M16 and PRMIR/PREF errors already exist in the schematic. Their unequal drain biases and finite output resistance are consistent with those errors. Improved placement does not remove this circuit-level limitation.

The main M1/M2 and M5/M2 ratios improve at both supplies. This is not an improvement in every nominal ratio: PLOAD2/PLOAD1 changes from +0.0063% to −0.0099% at 3.3 V and from +0.0097% to −0.0117% at 5 V; the folded-pair error also increases in magnitude at 3.3 V. These small deterministic tradeoffs are retained in the table, alongside the improved physical matching environment. Statistical mismatch still requires a separate study.

## PSRR comparison

Minimum sampled PSRR over 1 Hz–1 MHz, 481 common frequency points. Both physical layouts use corrected Magic 8.3.685 extraction and the same capacitance-conserving RCC method. Temporary extraction-only ports anchor internal PNP/well-resistor nodes so extresist retains their connections; only the public 12-pin interface appears in simulation. Both independent topology LVS audits pass. The old GDS is unchanged; its rerun values differ slightly from the earlier published numbers because both layouts now use this same anchoring procedure. Raw exports and before-layout provenance are retained.

{psrr_table}

The following model comparisons isolate contributions without treating them as additive physical gains. Coupling-removal rows are diagnostic counterfactuals, not achieved layout results.

{diagnostic_table}

![PSRR before and after the matching revision](../../figures/psrr-before-after.png)

The RCC zero-wire-resistance response is checked against the capacitance-only response to within 0.001 dB over the full frequency grid. [AC conservation check](../../release/psrr/conservation_ac_check.json) · [Comparison numbers](comparison.json) · [All diagnostic AC metrics](../../release/psrr/summary.json).

Early trial placement increased the reference offset because of resistive voltage drops in longer VREF, trim and PTAT routes. Diagnostic zero-resistance counterfactuals identified those paths. Moving the compensation capacitor and startup detector closer to the amplifier reduced the sensitive vgn2 route. The final compact floorplan restores a two-row output-capacitor bank and places the precision core beside the trim ladder. The 1270 × 410 µm area is a maximum planning envelope, not a target to fill. Supply-coupling ablations identify remaining sensitive nodes; those counterfactual circuits are not delivered extraction models.

## Limits and reproduction

The RCC model omits AVSS/DVSS resistance and approximates distributed coupling placement. Statistical mismatch/trim yield, thermal and stress gradients, complete branch RC balance, EM/IR, stability, noise, loading, and final harness integration remain open. The 360-point fixed-code DC screen and nominal power-cycle tests are screening evidence, not full electrical qualification. The approximate 1270 × 410 µm area does not supply final boundary clearances or pin assignments.

Use the pinned container and [build instructions](../../README.txt). Run `build`, `extract`, `screen`, and `pvt`, then `audit_mirror_layout.py --release <build> --out <build>/mirror_review`, `screen_mirror_bias.py --build <build>`, and `diagnose_psrr.py --build <build> --output <build>/psrr_diagnostics --supply-coupling`. For archived release inputs, omit `--build` from the current-bias screen; results default to the ignored build directory. `render_matching_results.py` regenerates this review and its figure from archived evidence.

GDS SHA-256: `{evidence['sha256']['Bandgap_Core_Res.gds']}`. [Release manifest](../../release/verification.json) binds physical checks and source/artifact hashes. [Functional-equivalence check](../../release/functional_equivalence.json) confirms unchanged functional geometry, connectivity and multiplicity.
"""
    (OUT / "README.md").write_text(text, encoding="utf-8")
    print("Rendered implementation review:", OUT)


if __name__ == "__main__":
    main()

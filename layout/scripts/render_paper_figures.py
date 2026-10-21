"""Regenerate all publication figures from the archived release, without EDA runs.

Usage: python layout/scripts/render_paper_figures.py [--out DIRECTORY]
Requires Matplotlib, NumPy and KLayout, all available in the pinned container.
"""

import argparse
import gzip
import hashlib
import html
import json
from pathlib import Path

from draw_layout import draw_all, draw_harness_fit
from plot_screens import plot_nominal, plot_corners, plot_psrr

LAYOUT = Path(__file__).resolve().parents[1]
CAPTIONS = [
    (
        "harness-area-fit",
        "Fit within the updated draft user area",
        "Approximately 1270 × 410 µm, from an organizer announcement supplied by the designer on 20 October 2026. "
        "Dimensions, occupied fraction and centered margins are calculated from the released GDS. "
        "These are geometric margins, not verified routing or boundary clearances. "
        "The centered position is illustrative; no GDS placement, slot, pin assignment or wrapper connection has changed. "
        "This draft target supersedes the earlier inferred smaller envelope for planning. Keep the analog core compact "
        "and use spare area selectively for matching, isolation, supply routing and harness access.",
    ),
    (
        "psrr-before-after",
        "Supply rejection and routing improvement",
        "Simulated PSRR at TT, 25 °C, trim 72, DVDD = 3.3 V, ideal supplies/grounds, and no external load. "
        "The upper plots compare schematic, the previous layout, and the routing revision using the same ground-inclusive conserved-RCC approximation. "
        "The lower plots expand the minima; their vertical scales differ. Minima are sampled over 1 Hz–1 MHz. "
        "Annotated changes compare the two physical layouts with the same corrected extraction method. "
        "The full curves still differ from the schematic. Ground metal resistance is included; well/substrate resistance remains lumped and coupling placement is approximate.",
    ),
    (
        "physical-layout",
        "Routed core layout",
        "Rendering of the actual routed and filled GDS, not a silicon micrograph. GF180MCU-D, five metals, MIM option B, "
        "1 kΩ/□ poly. Sixteen numbered device-group labels and eleven capacitor labels "
        "locate the input pair, mirrors, cascodes, sinks, PNPs, bias and startup devices. Numbers 1–14 match the placement figure. "
        "The dashed outline identifies the trim-logic deep n-well region. Labels overlay the actual geometry. "
        "Layout polygons are rasterized at 600 dpi inside the vector exports; dimensions and labels remain vector. "
        "The matching revision reports zero DRC violations and an LVS match. "
        "Shuttle integration and full analog qualification remain open.",
    ),
    (
        "initial-floorplan",
        "Initial floorplan concept",
        "Historical planning allocations, retained as a box diagram. The earlier envelope inferred from the wrapper script was "
        "537.15 × 273 µm. This concept is not the implemented placement or a fit guarantee. "
        "The implemented core exceeds this historical envelope, but fits geometrically within the updated approximately 1270 × 410 µm draft user area. "
        "Routing symmetry, shielding and dummy placement were planning intentions, not claims of completed verification.",
    ),
    (
        "implemented-floorplan",
        "Implemented device placement",
        "Saved placement coordinates and device-group bounds, with actual GDS dimensions. Numbered groups identify matching sets; "
        "other device groups remain drawn. Blue: analog; amber: trim resistors; violet: digital trim circuitry; green: MIM capacitors. "
        "The dashed outline marks the trim-logic deep n-well region. Routing is omitted for clarity. "
        "The core mirrors, PNPs and PTAT resistor are clustered beside the trim network. Explicit tied-off MOS dummies surround every selected array.",
    ),
    (
        "matched-arrays",
        "Matched unit locations",
        "Unit placement origins from the saved placement, with local coordinates and equal horizontal/vertical scale in each panel. "
        "Symbols identify source devices; black crosses show arithmetic centroids of their unit origins. Overlapping centroids share a cross. "
        "Q1 lies within the Q2 array; Q3 is separate. The drawing does not establish matched parasitics or statistical mismatch. "
        "Gray squares show tied-off dummy units. The M1/M2/M5 local drain routes have equal length, metal area and via count; global parasitics are not claimed equal.",
    ),
    (
        "post-layout-screens",
        "Nominal post-layout simulation",
        "Conserved-RCC approximation at TT, DVDD = 3.3 V. Temperature sweep uses trim 72; trim transfer includes all 128 codes "
        "at 25 °C. PSRR uses trim 72 at 25 °C; the lower-right plot shows analog supply current versus temperature. "
        "No added external output load. Signal/supply/ground metal resistance and parasitic capacitance are included; well/substrate resistance remains lumped. "
        "The project MIM compatibility model remains in use. Curves connect saved samples without smoothing.",
    ),
    (
        "process-corner-screen",
        "Fixed-code process and temperature screen",
        "All 360 saved DC samples: five MOS × three BJT × three resistor corners, two supplies, and four temperatures. "
        "Trim remains 72; there is no per-corner calibration. Columns separate MOS corners; rows separate AVDD. "
        "Each temperature contains nine BJT/resistor combinations. Open markers show samples and vertical strokes show their range; "
        "coincident samples overlap. Temperatures are plotted exactly, without horizontal jitter. "
        "All saved DC points completed; recovered solver fallback warnings are retained in the released log and summary. "
        "Convergence does not establish specification compliance.",
    ),
]


def verify_inputs():
    """Bind every figure to the published release and verbatim archived samples."""
    manifest = json.loads((LAYOUT / "release/verification.json").read_text())
    for name, expected in manifest["sha256"].items():
        actual = hashlib.sha256((LAYOUT / "release" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Release input hash mismatch: {name}")
    archive = json.loads((LAYOUT / "figure-data/provenance.json").read_text())
    if archive["gds_sha256"] != manifest["sha256"]["Bandgap_Core_Res.gds"]:
        raise ValueError("Archived figure data belongs to a different layout release")
    for name, expected in archive["uncompressed_sha256"].items():
        path = LAYOUT / "figure-data" / name
        contents = (
            gzip.decompress(path.read_bytes())
            if path.suffix == ".gz"
            else path.read_bytes()
        )
        if hashlib.sha256(contents).hexdigest() != expected:
            raise ValueError(f"Archived sample hash mismatch: {name}")


def write_gallery(out):
    figures = []
    for number, (stem, title, caption) in enumerate(CAPTIONS, 1):
        links = " · ".join(
            f'<a href="{stem}.{ext}">{ext.upper()}</a>' for ext in ["pdf", "svg", "png"]
        )
        figures.append(
            f"<figure><h2>{number}. {html.escape(title)}</h2>"
            f'<img src="{stem}.png" alt="{html.escape(title)}">'
            f"<figcaption>{html.escape(caption)}</figcaption><p>{links}</p></figure>"
        )
    page = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Bandgap paper figures</title>
<style>body{font:16px/1.55 Arial,sans-serif;color:#17191c;max-width:950px;margin:36px auto;padding:0 24px}
h1{font-size:28px}h2{font-size:20px}a{color:#0072b2}figure{margin:40px 0;border-top:1px solid #bbb;padding-top:16px}
img{width:100%;height:auto}figcaption{font-size:14px;color:#333}p{margin:12px 0}
@media print{body{margin:0;max-width:none}figure{break-inside:avoid}figure p{display:none}}</style>
<h1>Trim-inclusive bandgap — paper figures</h1>
<p>GF180MCU-D · simulated results, actual layout geometry and updated draft user area · 8 figures</p>
<p>Artwork uses a 7.1-inch master width, 7–9 pt labels, restrained colors and distinguishable line styles.
PDF and SVG exports preserve vector plots and text; PNG copies are 600 dpi. The GDS polygons are rasterized
inside vector files to keep them manageable. Full-size masters are intended for a wide paper figure;
do not shrink dense composites into a single narrow column without repaneling.</p>
<p>Presentation follows the readability principles in the
<a href="https://www.isscc.org/figures">ISSCC figure guidance</a>.
This is figure artwork, not an ISSCC submission or a claim of submission-template compliance.</p>
<p>All data, device geometry and verification results are preserved. RCC extraction remains approximate;
the core is not tapeout-qualified. Reproduction: scripts/render_paper_figures.py in the repository.</p>
"""
    (out / "index.html").write_text(
        page + "\n".join(figures) + "\n</html>\n", encoding="utf-8"
    )
    (out / "captions.txt").write_text(
        "\n\n".join(
            f"{i}. {title}\n{caption}"
            for i, (_, title, caption) in enumerate(CAPTIONS, 1)
        )
        + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=LAYOUT / "figures")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    verify_inputs()
    release = LAYOUT / "release"
    data = LAYOUT / "figure-data/postlayout"
    planning = json.loads((LAYOUT / "planning_envelope.json").read_text())
    evidence = json.loads((release / "verification.json").read_text())
    draw_harness_fit(evidence["bounds_um"], planning, args.out)
    draw_all(release / "Bandgap_Core_Res.gds", release / "placement.json", args.out)
    plot_psrr(release / "psrr", args.out)
    plot_nominal(data, args.out)
    plot_corners(release / "pvt_dc_screen.json", args.out)
    write_gallery(args.out)
    print(f"{len(CAPTIONS)} figures exported as PDF, SVG and 600-dpi PNG: {args.out}")


if __name__ == "__main__":
    main()

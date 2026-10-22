"""Export the complete trim-inclusive Xschem sheet and two detail views.

Run after selecting GF180MCU-D and verifying the schematic representation:
    python docs/scripts/render_schematics.py

All figures use the saved Xschem geometry. No transistors are redrawn and no
temporary schematic arrangement or hidden code block is substituted.
"""

from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "docs/images"
SOURCE = ROOT / "Bandgap_Core_Res.sch"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_text(parent, x, y, value, size, color="#172533", **attributes):
    element = ET.SubElement(
        parent,
        f"{{{NS}}}text",
        {
            "x": str(x),
            "y": str(y),
            "font-size": str(size),
            "fill": color,
            "font-family": "Arial, Helvetica, sans-serif",
            **attributes,
        },
    )
    element.text = value


def export_xschem(window):
    """Export saved source geometry and property-linked symbol annotations."""
    with tempfile.TemporaryDirectory(prefix="bandgap-schematic-") as directory:
        temporary = Path(directory)
        raw_svg = temporary / "sheet.svg"
        commands = temporary / "export.tcl"
        commands.write_text(
            "set dark_colorscheme 0\nset svg_font_name {Arial}\n"
            "xschem build_colors\n"
            f"xschem print svg {{{raw_svg}}} 0 0 {' '.join(map(str, window))}\nexit\n"
        )
        subprocess.run(
            [
                "xschem",
                "-x",
                "-r",
                "-l",
                str(temporary / "xschem.log"),
                "--script",
                str(commands),
                str(SOURCE),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        drawing = raw_svg.read_text()
    colors = {
        "#0099cc": "#225c86",
        "#222222": "#172533",
        "#229900": "#172533",
        "#bb2200": "#225c86",
        "#ff0000": "#546370",
        "#888800": "#546370",
        "#666600": "#546370",
    }
    for original, ink in colors.items():
        drawing = drawing.replace(original, ink)
    drawing = re.sub(r"stroke-width: [\d.]+;", "stroke-width: 1.5;", drawing)
    raw = ET.fromstring(drawing)
    # Native unfilled schematic frames use the same layer as transistor ink.
    # Give only those large frames a light outline in the publication figure.
    for rect in raw.iter(f"{{{NS}}}rect"):
        if rect.get("class") == "l4" and float(rect.get("width", 0)) > 400:
            rect.set("style", "fill:none;stroke:#ccd5dc;stroke-width:1.5")
    return raw


def render_sheet(name, number, window, title, subtitle, footer):
    raw = export_xschem(window)
    width, height = map(float, (raw.get("width"), raw.get("height")))
    margin = width * 0.026
    header = width * 0.055
    total_height = height + header + width * 0.03
    figure = ET.Element(
        f"{{{NS}}}svg",
        {
            "width": str(round(width * 0.6)),
            "height": str(round(total_height * 0.6)),
            "viewBox": f"0 0 {width:g} {total_height:g}",
            "role": "img",
            "aria-labelledby": "title description",
        },
    )
    ET.SubElement(figure, f"{{{NS}}}title", {"id": "title"}).text = title
    ET.SubElement(figure, f"{{{NS}}}desc", {"id": "description"}).text = (
        "Direct Xschem export from Bandgap_Core_Res.sch. " + subtitle
    )
    ET.SubElement(
        figure,
        f"{{{NS}}}rect",
        {
            "width": f"{width:g}",
            "height": f"{total_height:g}",
            "fill": "white",
        },
    )
    add_text(
        figure, margin, width * 0.023, title, width * 0.014, **{"font-weight": "bold"}
    )
    add_text(figure, margin, width * 0.039, subtitle, width * 0.009, "#546370")
    add_text(
        figure,
        width - margin,
        width * 0.023,
        f"{number} / 2" if number is not None else "COMPLETE SHEET",
        width * 0.012,
        **{"text-anchor": "end"},
    )
    ET.SubElement(
        figure,
        f"{{{NS}}}path",
        {
            "d": f"M{margin:g} {width * 0.049:g} H{width-margin:g}",
            "stroke": "#bec9d1",
        },
    )
    group = ET.SubElement(
        figure, f"{{{NS}}}g", {"transform": f"translate(0 {header:g})"}
    )
    group.extend(list(raw))
    add_text(figure, margin, total_height - width * 0.012, footer, width * 0.0085)
    ET.indent(figure, space="  ")
    comment = (
        f"<!-- Direct Xschem export: Bandgap_Core_Res.sch; SHA-256: {sha256(SOURCE)}. "
        "GF180 symbol geometry: Copyright 2022 GlobalFoundries PDK Authors; "
        "Apache-2.0; see ../../LICENSE-GF180.txt. -->\n"
    )
    (IMAGES / name).write_text(
        comment + ET.tostring(figure, encoding="unicode") + "\n", encoding="utf-8"
    )
    return " ".join(raw.itertext())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evidence", type=Path, default=ROOT / "layout/release/schematic-symbols"
    )
    evidence = parser.parse_args().evidence
    verified = json.loads((evidence / "verification.json").read_text())
    assert verified["lvs_match"] and verified["no_devices_filtered"]
    assert (
        sha256(SOURCE) == verified["schematic_sha256"]
    ), "Schematic differs from checked source"
    assert (
        sha256(ROOT / "layout/release/Bandgap_Core_Res.gds") == verified["gds_sha256"]
    ), "Released layout differs from checked GDS"
    for name, expected in verified["sha256"].items():
        assert sha256(evidence / name) == expected, name
    inventory = json.loads((evidence / "circuit.json").read_text())
    banks = [row for row in inventory["devices"] if row["name"].startswith("DUM_")]
    assert len(banks) == 13 and sum(row["m"] for row in banks) == 261
    assert "code_shown.sym" not in SOURCE.read_text()
    assert (
        len(
            re.findall(
                r"C \{symbols/[pn]fet_05v0.sym\} [^\n]+\{name=DUM_",
                SOURCE.read_text(),
            )
        )
        == 13
    )

    complete_name = "bandgap-core-resistor-trim-complete.svg"
    functional_name = "bandgap-core-resistor-trim.svg"
    dummy_name = "bandgap-core-resistor-trim-dummies.svg"
    complete_labels = render_sheet(
        complete_name,
        None,
        (-150, -900, 6060, 1800),
        "TRIM-INCLUSIVE BANDGAP · COMPLETE XSCHEM SHEET",
        "Functional circuit, seven-bit trim and 13 native dummy transistor banks",
        "One saved schematic: Bandgap_Core_Res.sch, instantiated by TB10_RESISTOR_TRIM. All 261 dummy units are retained in LVS.",
    )
    functional_labels = render_sheet(
        functional_name,
        1,
        (-150, -900, 3750, 1800),
        "TRIM-INCLUSIVE BANDGAP REFERENCE",
        "GF180MCU-D · Functional circuit and seven-bit trim",
        "Direct Xschem export. The native dummy-symbol section of the same schematic is shown on sheet 2.",
    )
    dummy_labels = render_sheet(
        dummy_name,
        2,
        (4000, -670, 6060, 1180),
        "TRIM-INCLUSIVE BANDGAP · NATIVE DUMMY SYMBOLS",
        "13 native PDK symbols / 261 units · Explicit D/G/S/B supply ties",
        "Direct Xschem export of the saved transistor symbols and wires; full core LVS passes with every dummy unit retained.",
    )
    assert "ppolyf_u_1k" in functional_labels and "ppolyf_u_2k" not in functional_labels
    assert all(f"b{bit}" in functional_labels for bit in range(7))
    assert all("X" + bank["name"] in dummy_labels for bank in banks)
    assert "ppolyf_u_1k" in complete_labels and "ppolyf_u_2k" not in complete_labels
    assert all(f"b{bit}" in complete_labels for bit in range(7))
    assert all("X" + bank["name"] in complete_labels for bank in banks)
    manifest = {
        "source": "Bandgap_Core_Res.sch",
        "source_sha256": sha256(SOURCE),
        "presentation": "One complete-sheet Xschem export and two detail crops of the same saved native-symbol schematic. No display-only arrangement or redrawn transistor symbols.",
        "verification": "layout/release/schematic-symbols/verification.json",
        "native_dummy_symbols": 13,
        "dummy_units": 261,
        "dummy_banks": [
            {key: bank[key] for key in ["name", "model", "nodes", "w", "l", "m"]}
            for bank in banks
        ],
        "figure_sha256": {
            name: sha256(IMAGES / name)
            for name in [complete_name, functional_name, dummy_name]
        },
    }
    (IMAGES / "trim-schematic-provenance.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(
        "Exported the complete Xschem sheet and both detail views "
        "from the LVS-checked source."
    )


if __name__ == "__main__":
    main()

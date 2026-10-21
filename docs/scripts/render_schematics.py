"""Draw two complementary views of the released trim-inclusive schematic.

Run in the GF180 Xschem environment:
    python docs/scripts/render_schematics.py

The first sheet is a fresh Xschem export. Only the dummy code block is omitted
from a temporary display copy; its devices appear graphically on sheet two.
The authoritative schematic and all physical-release files remain untouched.
"""

from dataclasses import asdict, dataclass
from pathlib import Path
import hashlib
import importlib.util
import json
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
IMAGES = ROOT / "docs/images"
SOURCE = ROOT / "Bandgap_Core_Res.sch"
RELEASE = ROOT / "layout/release"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


@dataclass(frozen=True)
class Dummy:
    name: str
    nodes: tuple[str, ...]
    model: str
    width_um: str
    length_um: str
    count: int
    matches: tuple[str, ...]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_dummies(source):
    """Check the drawn bank inventory against source, release, and placement."""
    spec = importlib.util.spec_from_file_location(
        "matching_constraints", ROOT / "layout/scripts/matching_constraints.py"
    )
    constraints = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(constraints)
    groups = {
        "X" + constraints.dummy_name(names): (names, constraints.dummy_count(rows))
        for names, rows in constraints.ARRAYS
    }
    pattern = re.compile(
        r"^(XDUM_\w+) ((?:\w+ ){4})([pn]fet_05v0) " r"W=([\d.]+)u L=([\d.]+)u m=(\d+)$",
        re.M,
    )
    released_netlist = (RELEASE / "mirror_bias/schematic_source.spice").read_text()
    records = pattern.findall(source)
    assert records == pattern.findall(
        released_netlist
    ), "Source / release dummy mismatch"
    assert len(records) == len(groups) == 13
    banks = []
    for name, node_text, model, width, length, count_text in records:
        names, expected_count = groups[name]
        nodes = tuple(node_text.split())
        rail = "avdd" if model.startswith("p") else "avss"
        assert nodes == (rail,) * 4, (name, nodes)
        assert int(count_text) == expected_count, name
        banks.append(Dummy(name, nodes, model, width, length, int(count_text), names))
    assert sum(bank.count for bank in banks) == 261
    return banks


def add_text(parent, x, y, value, size=24, color="#172533", **attributes):
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


def reflow_trim_buffers(source):
    """Move the seven isolated inverter drawings into two rows in a display copy.

    Every component property and wire label is retained. Both endpoints of
    every moved wire must belong to the same original inverter rectangle.
    Netlists before and after this coordinate-only change are compared below.
    """
    old_centers = [3435, 3665, 3900, 4120, 4340, 4550, 4772.5]
    moves = [
        (2590 + (i % 4) * 290 - center, 1150 + (i // 4) * 400)
        for i, center in enumerate(old_centers)
    ]

    def region(x, y):
        if -20 <= y <= 195:
            for index, center in enumerate(old_centers):
                if center - 80 <= x <= center + 110:
                    return index
        return None

    def component(match):
        prefix, x, y = match.groups()
        index = region(float(x), float(y))
        if index is None:
            return match.group(0)
        dx, dy = moves[index]
        return f"{prefix}{float(x) + dx:g} {float(y) + dy:g}"

    def wire(match):
        x1, y1, x2, y2 = map(float, match.groups())
        first, second = region(x1, y1), region(x2, y2)
        assert first == second, (
            "Wire crosses an inverter display boundary",
            match.group(0),
        )
        if first is None:
            return match.group(0)
        dx, dy = moves[first]
        return f"N {x1 + dx:g} {y1 + dy:g} {x2 + dx:g} {y2 + dy:g}"

    result = re.sub(r"(?m)^(C \{[^}]+\} )([\d.-]+) ([\d.-]+)", component, source)
    result = re.sub(r"(?m)^N ([\d.-]+) ([\d.-]+) ([\d.-]+) ([\d.-]+)", wire, result)
    return result + "T {Trim input buffers: b0 - b6} 2550 1090 0 0 0.4 0.4 {}\n"


def display_netlist(schematic, temporary):
    subprocess.run(
        [
            "xschem",
            "-x",
            "-r",
            "-q",
            "-n",
            "-s",
            "-o",
            str(temporary),
            "-N",
            "display.spice",
            str(schematic),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    netlist = (temporary / "display.spice").read_text()
    assert "XM51 " in netlist and "ppolyf_u_1k" in netlist
    return [
        line.strip()
        for line in netlist.splitlines()
        if line and not line.startswith("*")
    ]


def render_functional(source):
    """Export every functional device and wire with Xschem's own symbol renderer."""
    block = re.search(
        r'(?ms)^C \{code_shown\.sym\} [^\n]*name=LAYOUT_MATCHING_DUMMIES.*?^"\}\s*\Z',
        source,
    )
    assert block, "Expected exactly one terminal dummy code block"
    display_source = source[: block.start()].rstrip() + "\n"
    assert "XDUM_" not in display_source
    with tempfile.TemporaryDirectory(prefix="bandgap-schematic-") as directory:
        temporary = Path(directory)
        schematic = temporary / "functional-display.sch"
        raw_svg = temporary / "functional.svg"
        commands = temporary / "export.tcl"
        schematic.write_text(display_source)
        before_reflow = display_netlist(schematic, temporary)
        schematic.write_text(reflow_trim_buffers(display_source))
        assert (
            display_netlist(schematic, temporary) == before_reflow
        ), "Display reflow changed connectivity"
        commands.write_text(
            "set dark_colorscheme 0\nset svg_font_name {Arial}\n"
            "xschem build_colors\n"
            f"xschem print svg {{{raw_svg}}} 0 0 -150 -900 3750 1800\nexit\n"
        )
        result = subprocess.run(
            [
                "xschem",
                "-x",
                "-r",
                "-l",
                str(temporary / "xschem.log"),
                "--script",
                str(commands),
                str(schematic),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=90,
            check=True,
        )
        if not raw_svg.exists():
            raise RuntimeError(result.stdout + result.stderr)
        drawing = raw_svg.read_text()

    # Use restrained, high-contrast ink while retaining every exported shape.
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
    label_text = " ".join(raw.itertext())
    assert "ppolyf_u_1k" in label_text and "ppolyf_u_2k" not in label_text
    assert all(f"b{bit}" in label_text for bit in range(7))
    assert "LAYOUT_MATCHING_DUMMIES" not in label_text

    figure = ET.Element(
        f"{{{NS}}}svg",
        {
            "width": "2340",
            "height": "1824",
            "viewBox": "0 0 3900 3040",
            "role": "img",
            "aria-labelledby": "title description",
        },
    )
    ET.SubElement(figure, f"{{{NS}}}title", {"id": "title"}).text = (
        "Trim-inclusive bandgap schematic — sheet 1 of 2"
    )
    ET.SubElement(figure, f"{{{NS}}}desc", {"id": "description"}).text = (
        "Functional circuit exported from the current Bandgap_Core_Res.sch. "
        "The 13 dummy banks from the same source are drawn separately on sheet 2."
    )
    ET.SubElement(
        figure,
        f"{{{NS}}}rect",
        {
            "width": "3900",
            "height": "3040",
            "fill": "white",
        },
    )
    add_text(
        figure,
        100,
        92,
        "TRIM-INCLUSIVE BANDGAP REFERENCE",
        62,
        **{"font-weight": "bold"},
    )
    add_text(
        figure,
        100,
        156,
        "GF180MCU-D  •  Functional circuit and seven-bit trim",
        36,
        "#546370",
    )
    add_text(figure, 3800, 92, "1 / 2", 48, **{"text-anchor": "end"})
    ET.SubElement(
        figure,
        f"{{{NS}}}path",
        {
            "d": "M100 195 H3800",
            "stroke": "#bec9d1",
            "stroke-width": "2",
        },
    )
    drawing_group = ET.SubElement(
        figure, f"{{{NS}}}g", {"transform": "translate(0 215)"}
    )
    drawing_group.extend(list(raw))
    add_text(
        figure,
        100,
        2968,
        "Dummy devices continue on sheet 2: 13 banks / 261 units, tied to AVDD or AVSS.",
        35,
    )
    add_text(
        figure,
        100,
        3018,
        "Xschem export with trim buffers reflowed for readability; connectivity checked against the original arrangement.",
        27,
        "#546370",
    )
    write_svg(IMAGES / "bandgap-core-resistor-trim.svg", figure)


def symbol(parent, pmos):
    """Four-terminal GF180 symbol geometry; terminal positions follow the PDK.

    Copyright 2022 GlobalFoundries PDK Authors, Apache-2.0.
    See LICENSE-GF180.txt. Only geometry is reproduced; no model is substituted.
    """
    group = ET.SubElement(parent, f"{{{NS}}}g", {"transform": "translate(73 141)"})
    common = "M7.5 -22.5 V22.5 M20 -30 V-17.5 M20 17.5 V30 M-2.5 -15 V15"
    shape = (
        " M7.5 17.5 H20 M12.5 -17.5 H20 M-20 0 H-12.5"
        if pmos
        else " M-20 0 H-2.5 M7.5 -17.5 H20 M7.5 17.5 H15"
    )
    ET.SubElement(
        group,
        f"{{{NS}}}path",
        {
            "d": common + shape,
            "fill": "none",
            "stroke": "#172533",
            "stroke-width": "1.8",
        },
    )
    if pmos:
        ET.SubElement(
            group,
            f"{{{NS}}}circle",
            {
                "cx": "-7.5",
                "cy": "0",
                "r": "5",
                "fill": "white",
                "stroke": "#172533",
                "stroke-width": "1.8",
            },
        )
    arrow = "12.5,-20 7.5,-17.5 12.5,-15" if pmos else "15,15 20,17.5 15,20"
    ET.SubElement(group, f"{{{NS}}}polygon", {"points": arrow, "fill": "#172533"})
    ET.SubElement(
        group,
        f"{{{NS}}}path",
        {
            "d": "M7.5 0 H20",
            "stroke": "#172533",
            "stroke-width": "1.8",
        },
    )
    for x, y, label in [
        (26, -21, "S" if pmos else "D"),
        (26, 27, "D" if pmos else "S"),
        (26, -5, "B"),
        (-19, -10, "G"),
    ]:
        add_text(group, x, y, label, 11, "#546370")


def draw_bank(parent, bank, x, y):
    card = ET.SubElement(parent, f"{{{NS}}}g", {"transform": f"translate({x} {y})"})
    ET.SubElement(
        card,
        f"{{{NS}}}rect",
        {
            "width": "359",
            "height": "220",
            "fill": "white",
            "stroke": "#ccd5dc",
            "stroke-width": "1",
        },
    )
    add_text(card, 16, 29, bank.name, 20, **{"font-weight": "bold"})
    add_text(card, 16, 53, "Matches " + " / ".join(bank.matches), 15, "#546370")
    rail = bank.nodes[0].upper()
    add_text(
        card,
        133,
        84,
        rail,
        15,
        "#225c86",
        **{"text-anchor": "middle", "font-weight": "bold"},
    )

    # D and S meet the top/bottom tie bars; B meets the right bus; G meets the
    # top bar via the left return. All four physical terminals share one net.
    ET.SubElement(
        card,
        f"{{{NS}}}path",
        {
            "d": "M93 111 V98 H133 V184 H93 V171 M93 141 H133 M53 141 H33 V98 H93",
            "fill": "none",
            "stroke": "#225c86",
            "stroke-width": "1.8",
        },
    )
    for cx, cy in [(93, 98), (133, 141)]:
        ET.SubElement(
            card,
            f"{{{NS}}}circle",
            {
                "cx": str(cx),
                "cy": str(cy),
                "r": "3.2",
                "fill": "#225c86",
            },
        )
    symbol(card, bank.model.startswith("p"))
    add_text(card, 164, 118, f"m = {bank.count}", 24, **{"font-weight": "bold"})
    add_text(card, 164, 148, f"W = {bank.width_um} µm", 18)
    add_text(card, 164, 175, f"L = {bank.length_um} µm", 18)
    add_text(card, 16, 207, f"D = G = S = B = {rail}", 14, "#546370")


def render_dummies(banks):
    figure = ET.Element(
        f"{{{NS}}}svg",
        {
            "width": "1592",
            "height": "1300",
            "viewBox": "0 0 1592 1300",
            "role": "img",
            "aria-labelledby": "title description",
        },
    )
    ET.SubElement(figure, f"{{{NS}}}title", {"id": "title"}).text = (
        "Trim-inclusive bandgap: dummy devices — sheet 2 of 2"
    )
    ET.SubElement(figure, f"{{{NS}}}desc", {"id": "description"}).text = (
        "All 13 netlisted dummy banks. 197 PMOS units have drain, gate, source and body "
        "tied to AVDD; 64 NMOS units have all four terminals tied to AVSS. "
        "Each symbol represents m identical devices. Sizes are per device."
    )
    ET.SubElement(
        figure, f"{{{NS}}}rect", {"width": "1592", "height": "1300", "fill": "white"}
    )
    add_text(
        figure,
        48,
        63,
        "TRIM-INCLUSIVE BANDGAP · DUMMY DEVICES",
        32,
        **{"font-weight": "bold"},
    )
    add_text(
        figure,
        48,
        101,
        "13 banks  /  261 units  ·  Same source and nets as sheet 1",
        21,
        "#546370",
    )
    add_text(figure, 1544, 63, "2 / 2", 27, **{"text-anchor": "end"})
    ET.SubElement(figure, f"{{{NS}}}path", {"d": "M48 128 H1544", "stroke": "#bec9d1"})
    for kind, title_y, top_y, title in [
        ("p", 174, 196, "PMOS · pfet_05v0 · 197 units · All terminals tied to AVDD"),
        ("n", 698, 720, "NMOS · nfet_05v0 · 64 units · All terminals tied to AVSS"),
    ]:
        add_text(figure, 48, title_y, title, 23, **{"font-weight": "bold"})
        selected = [bank for bank in banks if bank.model.startswith(kind)]
        for index, bank in enumerate(selected):
            draw_bank(figure, bank, 48 + (index % 4) * 379, top_y + (index // 4) * 240)

    # Use the unused lower-right area for a short schematic reading key.
    add_text(figure, 448, 1003, "READING THIS SHEET", 22, **{"font-weight": "bold"})
    for y, line in [
        (
            1040,
            "m is the number of identical physical units; W and L are per-unit dimensions.",
        ),
        (
            1074,
            "The four-terminal connections are explicit: no dummy gate or diffusion floats.",
        ),
        (
            1108,
            "“Matches” identifies the functional array that the dummy bank surrounds.",
        ),
        (
            1142,
            "The panel arrangement is for readability; it does not depict physical placement.",
        ),
    ]:
        add_text(figure, 448, y, line, 20, "#546370")
    ET.SubElement(figure, f"{{{NS}}}path", {"d": "M48 1214 H1544", "stroke": "#bec9d1"})
    add_text(
        figure,
        48,
        1248,
        "Graphical companion to LAYOUT_MATCHING_DUMMIES in Bandgap_Core_Res.sch; verified against the released netlist.",
        19,
    )
    add_text(
        figure,
        48,
        1278,
        "GF180 symbol geometry: © 2022 GlobalFoundries PDK Authors · Apache-2.0 · See LICENSE-GF180.txt",
        16,
        "#546370",
    )
    write_svg(IMAGES / "bandgap-core-resistor-trim-dummies.svg", figure)


def write_svg(path, root):
    ET.indent(root, space="  ")
    comment = (
        f"<!-- Source: Bandgap_Core_Res.sch; SHA-256: {sha256(SOURCE)}. "
        "GF180 symbol geometry: Copyright 2022 GlobalFoundries PDK Authors; "
        "Apache-2.0; see ../../LICENSE-GF180.txt. -->\n"
    )
    path.write_text(
        comment + ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8"
    )


def main():
    source = SOURCE.read_text()
    before = sha256(SOURCE)
    banks = load_dummies(source)
    render_functional(source)
    render_dummies(banks)
    assert sha256(SOURCE) == before
    manifest = {
        "source": "Bandgap_Core_Res.sch",
        "source_sha256": before,
        "reference_netlist": "layout/release/mirror_bias/schematic_source.spice",
        "reference_netlist_sha256": sha256(
            RELEASE / "mirror_bias/schematic_source.spice"
        ),
        "presentation": "Sheet 1: functional Xschem export with trim buffers reflowed; before/after display netlists agree. Sheet 2: graphical view of the source's dummy code block.",
        "source_modified": False,
        "dummy_banks": [asdict(bank) for bank in banks],
        "dummy_units": sum(bank.count for bank in banks),
        "figure_sha256": {
            name: sha256(IMAGES / name)
            for name in [
                "bandgap-core-resistor-trim.svg",
                "bandgap-core-resistor-trim-dummies.svg",
            ]
        },
    }
    (IMAGES / "trim-schematic-provenance.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    print(
        "Exported 2 schematic sheets; verified all 13 dummy banks / 261 units against source and release."
    )


if __name__ == "__main__":
    main()

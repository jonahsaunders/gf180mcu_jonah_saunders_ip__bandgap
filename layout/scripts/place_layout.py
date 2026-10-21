"""Construct a physical placement candidate from independently generated PDK devices.

This stage deliberately does not claim routed connectivity or sign-off.
Device dimensions and net names come from the generated schematic inventory.
"""

from collections import defaultdict
from pathlib import Path
import json
import math
import klayout.db as k

from layout_paths import BUILD_DIR as ROOT
from matching_constraints import ARRAYS, CAP_X, CAP_Y, dummy_name, padded_pattern

DATA = json.loads((ROOT / "primitive_manifest.json").read_text())
LY = k.Layout()
LY.dbu = 0.001
TOP = LY.create_cell("Bandgap_Core_Res_placement")
META = {}
DEVICES = {d["name"]: d for d in DATA["devices"]}
PLACED = []
ASSIGNED = set()


def box(cell, layer, bounds):
    cell.shapes(LY.layer(*layer)).insert(k.DBox(*bounds))


def load_primitive(device):
    src = k.Layout()
    if device["model"].startswith("pnp"):
        src.read("/foss/pdks/gf180mcuD/libs.ref/gf180mcu_fd_pr/gds/pnp_05p00x05p00.gds")
        source = src.cell("pnp_05p00x05p00_0")
    else:
        src.read(str(ROOT / "primitives" / f'{device["cell"]}.gds'))
        source = src.top_cell()
    assert not source.dbbox().empty(), device["cell"]
    cell = LY.create_cell(device["cell"])
    for index in src.layer_indexes():
        dest = LY.layer(src.get_info(index))
        for poly in k.Region(source.begin_shapes_rec(index)).merged().each():
            cell.shapes(dest).insert(poly.to_dtype(src.dbu))
    m1 = list(k.Region(cell.begin_shapes_rec(LY.layer(34, 0))).merged().each())
    if device["model"] == "nwell":
        implant = LY.layer(31, 0)
        expanded = k.Region(cell.begin_shapes_rec(implant)).sized(140)
        cell.shapes(implant).clear()
        cell.shapes(implant).insert(expanded)
        # Magic's nwell view omits the LVS resistor marker. Its drawn length
        # is rounded to 10 nm by the centred generator.
        length = round(device["l"] / 0.01) * 0.01
        box(
            cell, (110, 5), (-device["w"] / 2, -length / 2, device["w"] / 2, length / 2)
        )
    if device["model"].startswith("cap_"):
        # The foundry LVS deck requires both capacitor identification layers.
        marker = k.Region(cell.begin_shapes_rec(LY.layer(117, 5)))
        cell.shapes(LY.layer(117, 10)).insert(marker)
    m1.sort(key=lambda p: p.bbox().area(), reverse=True)
    model = device["model"]
    pins = []
    if "fet_" in model:
        ring = m1[0].bbox().to_dtype(LY.dbu)
        ends = sorted(
            [
                p.bbox().to_dtype(LY.dbu)
                for p in m1[1:]
                if abs(p.bbox().center().x * LY.dbu) > 0.01
            ],
            key=lambda b: b.center().x,
        )
        gate = max(
            [
                p.bbox().to_dtype(LY.dbu)
                for p in m1[1:]
                if abs(p.bbox().center().x * LY.dbu) < 0.01
            ],
            key=lambda b: b.center().y,
        )
        gate_y = math.ceil((gate.top + 0.3) / 0.005) * 0.005
        box(cell, (34, 0), (-0.2, gate.top, 0.2, gate_y + 0.2))
        pins = [
            (ends[1].center().x, ends[1].center().y),
            (0, gate_y),
            (ends[0].center().x, ends[0].center().y),
            (0, ring.bottom + 0.115),
        ]
    elif model.startswith("pnp"):
        # Foundry cell: emitter center, inner N+ base ring, outer P+ collector ring.
        rings = sorted(
            [p.bbox().to_dtype(LY.dbu) for p in m1],
            key=lambda b: b.width(),
            reverse=True,
        )
        pins = [(0, rings[0].bottom + 0.18), (0, rings[1].bottom + 0.18), (0, 0)]
    elif model.startswith("cap_"):
        metal5 = list(k.Region(cell.begin_shapes_rec(LY.layer(81, 0))).merged().each())
        metal5.sort(key=lambda p: p.area(), reverse=True)
        a = metal5[0].bbox().to_dtype(LY.dbu)
        b = metal5[1].bbox().to_dtype(LY.dbu)
        # Keep all lower-via transitions outside the MIM bottom plate.
        x = cell.dbbox().right + 2
        y = cell.dbbox().top + 2
        box(cell, (81, 0), (-0.3, a.top - 0.2, 0.3, y + 0.3))
        box(cell, (81, 0), (b.left, -0.3, x + 0.3, 0.3))
        pins = [(0, y), (x, 0)]
    else:
        ring = m1[0].bbox().to_dtype(LY.dbu)
        ends = sorted(
            [p.bbox().to_dtype(LY.dbu) for p in m1[1:]], key=lambda b: b.center().y
        )
        pins = [
            (0, ends[-1].center().y),
            (0, ends[0].center().y),
            (0, ring.bottom + 0.115),
        ]
    for x, y in pins:
        layers = (
            [(81, 0), (46, 0), (42, 0), (36, 0)]
            if model.startswith("cap_")
            else [(34, 0), (36, 0)]
        )
        vias = [(41, 0), (40, 0), (38, 0)] if model.startswith("cap_") else [(35, 0)]
        for layer in layers:
            radius = 0.6 if model.startswith("cap_") and layer == (46, 0) else 0.2
            box(cell, layer, (x - radius, y - radius, x + radius, y + radius))
        for layer in vias:
            box(cell, layer, (x - 0.13, y - 0.13, x + 0.13, y + 0.13))
    bounds = cell.dbbox()
    META[device["cell"]] = dict(cell=cell, pins=pins, bounds=bounds)


for primitive in DATA["primitives"]:
    load_primitive(primitive)


def place(name, x, y, rotation=0, instance=None):
    d = DEVICES[name]
    p = META[d["cell"]]
    transform = k.DCplxTrans(1, rotation, False, x, y)
    TOP.insert(k.DCellInstArray(p["cell"].cell_index(), transform))
    pins = [transform * k.DPoint(px, py) for px, py in p["pins"]]
    bounds = p["bounds"].transformed(transform)
    PLACED.append(
        dict(
            name=instance or name,
            source=name,
            model=d["model"],
            nodes=d["nodes"],
            x=x,
            y=y,
            rotation=rotation,
            bounds=[bounds.left, bounds.bottom, bounds.right, bounds.top],
            pins=[[pt.x, pt.y] for pt in pins],
            dummy=name.startswith("DUM_"),
        )
    )
    if name.startswith("DUM_"):
        # Short D/G/S/B locally in M1. The single routed body access then ties
        # every dummy terminal to its explicit schematic rail.
        anchor = pins[3]
        for pin in pins[:3]:
            bend = k.DPoint(anchor.x, pin.y)
            path = k.DPath([pin, bend, anchor], 0.4, 0.2, 0.2)
            TOP.shapes(LY.layer(34, 0)).insert(path)


def assemble(names, pattern, pitch=None, matching=False):
    """Return an array of identical oriented units, with exact original multiplicity."""
    p = META[DEVICES[names[0]]["cell"]]
    width, height = p["bounds"].width(), p["bounds"].height()
    px, py = pitch or (width + 1.6, height + 1.6)
    active_names = list(names)
    if matching:
        names = [*names, dummy_name(names)]
        pattern = [
            [len(names) - 1 if c == "." else ord(c) - ord("A") for c in row]
            for row in padded_pattern(pattern)
        ]
    count = defaultdict(int)
    units = []
    for row, labels in enumerate(pattern):
        for col, label in enumerate(labels):
            if label is None:
                continue
            name = names[label]
            units.append(
                (
                    name,
                    col * px - p["bounds"].left,
                    row * py - p["bounds"].bottom,
                    f"{name}_{count[name]}",
                )
            )
            count[name] += 1
    for name in names:
        assert count[name] == DEVICES[name]["m"], (
            name,
            count[name],
            DEVICES[name]["m"],
        )
        ASSIGNED.add(name)
    return dict(
        names=active_names,
        matching=matching,
        dummy_source=names[-1] if matching else None,
        units=units,
        width=max(len(row) for row in pattern) * px - 1.6,
        height=len(pattern) * py - 1.6,
    )


groups = [assemble(names, pattern, matching=True) for names, pattern in ARRAYS]

# Capacitors occupy their own area with no active devices beneath them.
for idx in range(10):
    d = DEVICES[f"C{idx+2}"]
    p = META[d["cell"]]
    place(
        d["name"],
        CAP_X + (idx % 5) * 77 - p["bounds"].left,
        CAP_Y + (idx // 5) * 77 - p["bounds"].bottom,
    )
    ASSIGNED.add(d["name"])

# Q1 centered in eight identical Q2 cells. Q3 uses the same orientation nearby.
pnps = []
for row in range(3):
    for col in range(3):
        name = "Q1" if (row, col) == (1, 1) else "Q2"
        pnps.append((name, col * 10 + 5, row * 10 + 5, f"{name}_{row}_{col}"))
pnps.append(("Q3", 35, 15, "Q3"))
groups.append(dict(names=["Q1", "Q2", "Q3"], units=pnps, width=40, height=30))
ASSIGNED.update(["Q1", "Q2", "Q3"])

# Every remaining device remains explicit, with full original multiplicity.
for d in DATA["devices"]:
    if d["name"] in ASSIGNED:
        continue
    p = META[d["cell"]]
    w, h = p["bounds"].width(), p["bounds"].height()
    if d["name"] == "R4":
        groups.append(
            dict(
                names=["R4"], units=[("R4", h / 2, w / 2, "R4", 90)], width=h, height=w
            )
        )
    elif d["m"] == 1:
        groups.append(
            dict(
                names=[d["name"]],
                units=[(d["name"], w / 2, h / 2, d["name"])],
                width=w,
                height=h,
            )
        )
    else:
        cols = min(d["m"], 6)
        rows = math.ceil(d["m"] / cols)
        pattern = [
            [0 if r * cols + c < d["m"] else None for c in range(cols)]
            for r in range(rows)
        ]
        groups.append(assemble([d["name"]], pattern))


def pack(rectangles, items, margin=1.5):
    """Maximal-rectangle packing, with no rotations of matched device arrays."""
    free = list(rectangles)
    results = []
    failed = []
    for item in sorted(items, key=lambda g: max(g["width"], g["height"]), reverse=True):
        w, h = item["width"] + 2 * margin, item["height"] + 2 * margin
        choices = [
            (min(fw - w, fh - h), fw * fh - w * h, i)
            for i, (x, y, fw, fh) in enumerate(free)
            if w <= fw and h <= fh
        ]
        if not choices:
            failed.append(item)
            continue
        _, _, i = min(choices)
        x, y, _, _ = free[i]
        used = (x, y, w, h)
        new = []
        for fx, fy, fw, fh in free:
            if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
                new.append((fx, fy, fw, fh))
                continue
            if x > fx:
                new.append((fx, fy, x - fx, fh))
            if x + w < fx + fw:
                new.append((x + w, fy, fx + fw - x - w, fh))
            if y > fy:
                new.append((fx, fy, fw, y - fy))
            if y + h < fy + fh:
                new.append((fx, y + h, fw, fy + fh - y - h))
        free = [
            r
            for j, r in enumerate(new)
            if not any(
                j != k
                and r[0] >= s[0]
                and r[1] >= s[1]
                and r[0] + r[2] <= s[0] + s[2]
                and r[1] + r[3] <= s[1] + s[3]
                for k, s in enumerate(new)
            )
        ]
        results.append((item, x + margin, y + margin))
    return results, failed


def cluster(label, selectors, width, height):
    selected = [g for g in groups if any(n in g["names"] for n in selectors)]
    assert len(selected) == len(selectors), (label, selectors)
    result, failed = pack([(0, 0, width, height)], selected, margin=3)
    assert not failed, (label, [g["names"] for g in failed])
    children, units = [], []
    right = upper = 0
    for group, x, y in result:
        groups.remove(group)
        children.append((group, x, y))
        units.extend(
            (name, ux + x, uy + y, instance, *rest)
            for name, ux, uy, instance, *rest in group["units"]
        )
        right = max(right, x + group["width"])
        upper = max(upper, y + group["height"])
    groups.append(
        dict(
            names=[n for g in selected for n in g["names"]],
            units=units,
            width=right + 3,
            height=upper + 3,
            children=children,
            cluster=label,
        )
    )


cluster("nmos_bias", ["N1", "NTAIL", "N3", "N2"], 65, 55)

# Keep the current-carrying bandgap/trim paths compact. In particular, the
# input sense devices, R1, and PNPs must not be separated by size-only packing.
fixed_core = []
for selector, x, y in [
    ("M11", 8, 3),
    ("M1", 71, 8),
    ("M3", 123, 8),
    ("M3C", 123, 74),
    ("Q1", 83, 124),
    ("R1", 133, 101),
    ("M14", 70, 124),
    ("M16", 176, 8),
    ("M15", 176, 50),
    ("MINP", 285, 8),
    ("PCAS1", 399, 8),
    ("MFOLD1", 503, 8),
    ("MFOLD1C", 549, 8),
    ("C1", 499, 122),
    ("PLOAD1", 569, 8),
    ("MSINK1", 569, 116),
    ("VBNSINK", 399, 123),
    ("MTAIL", 399, 181),
    ("MSU_SENSE", 499, 191),
    ("MPSINK", 180, 178),
    ("MPFOLD", 180, 303),
    ("PREF", 8, 316),
    ("VBPCASLOW", 188, 73),
    ("VBPCASHIGHER", 188, 124),
    ("SUPPDET", 270, 8),
    ("R4", 285, 225),
    ("RB_P", 213, 8),
]:
    group = next(g for g in groups if selector in g["names"])
    groups.remove(group)
    fixed_core.append((group, x, y))


digital = [
    g
    for g in groups
    if all(
        n.startswith("M") and n[1:].isdigit() and int(n[1:]) >= 24 for n in g["names"]
    )
]
resistors = [
    g
    for g in groups
    if g["names"][0].startswith("RB")
    and g["names"][0][2:].isdigit()
    or g["names"][0].startswith("RFIXED")
]
analog = [g for g in groups if g not in digital and g not in resistors]
layout_groups = []
layout_clusters = []


def record_group(group, x, y, category):
    record = dict(
        category=category,
        names=group["names"],
        x=x,
        y=y,
        width=group["width"],
        height=group["height"],
        matching=group.get("matching", False),
        dummy_source=group.get("dummy_source"),
    )
    if "children" in group:
        layout_clusters.append(dict(**record, cluster=group["cluster"]))
        for child, cx, cy in group["children"]:
            record_group(child, x + cx, y + cy, category)
    else:
        layout_groups.append(record)


failed = []
for group, x, y in fixed_core:
    for name, ux, uy, instance, *rest in group["units"]:
        place(name, x + ux, y + uy, rest[0] if rest else 0, instance)
    record_group(group, x, y, "analog")
for category, rects, items in [
    ("digital", [(8, 165, 50, 137)], digital),
    ("resistors", [(70, 165, 150, 137)], resistors),
]:
    result, bad = pack(rects, items)
    for group, x, y in result:
        for unit in group["units"]:
            name, ux, uy, inst = unit[:4]
            place(name, x + ux, y + uy, unit[4] if len(unit) > 4 else 0, inst)
        record_group(group, x, y, category)
    failed.extend(bad)

# Pack the remaining bias/startup devices into actual free regions instead of
# reserving another wide analog strip. Matched arrays retain their orientation.
free = [(8, 8, 672, 390)]
occupied = [(g["x"], g["y"], g["width"], g["height"]) for g in layout_groups]
occupied.append((0, 153, 68, 161))  # Complete digital DNW and substrate guard ring.
occupied += [
    (
        d["bounds"][0],
        d["bounds"][1],
        d["bounds"][2] - d["bounds"][0],
        d["bounds"][3] - d["bounds"][1],
    )
    for d in PLACED
    if d["source"].startswith("C") and d["source"] != "C1"
]
for x, y, w, h in occupied:
    x, y, w, h = x - 1.5, y - 1.5, w + 3, h + 3
    remaining = []
    for fx, fy, fw, fh in free:
        if x >= fx + fw or x + w <= fx or y >= fy + fh or y + h <= fy:
            remaining.append((fx, fy, fw, fh))
            continue
        if x > fx:
            remaining.append((fx, fy, x - fx, fh))
        if x + w < fx + fw:
            remaining.append((x + w, fy, fx + fw - x - w, fh))
        if y > fy:
            remaining.append((fx, fy, fw, y - fy))
        if y + h < fy + fh:
            remaining.append((fx, y + h, fw, fy + fh - y - h))
    free = remaining
result, bad = pack(free, analog)
for group, x, y in result:
    for name, ux, uy, instance, *rest in group["units"]:
        place(name, x + ux, y + uy, rest[0] if rest else 0, instance)
    record_group(group, x, y, "analog")
failed.extend(bad)
assert not failed, [g["names"] for g in failed]

for i, first in enumerate(PLACED):
    ax, ay, ar, at = first["bounds"]
    for second in PLACED[i + 1 :]:
        bx, by, br, bt = second["bounds"]
        assert ar <= bx or br <= ax or at <= by or bt <= ay, (
            first["name"],
            second["name"],
            "overlapping primitive bounds",
        )

LY.write(str(ROOT / "placement_candidate.gds"))
(ROOT / "placement.json").write_text(
    json.dumps(
        dict(
            placed=PLACED,
            groups=layout_groups,
            clusters=layout_clusters,
            unplaced=[g["names"] for g in failed],
        ),
        indent=2,
    )
    + "\n"
)
print("Placed", len(PLACED), "of", sum(d["m"] for d in DATA["devices"]), "devices")
print("Unplaced groups:", [g["names"] for g in failed])
print("Extents:", TOP.dbbox())

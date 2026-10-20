"""Construct a physical placement candidate from independently generated PDK devices.

This stage deliberately does not claim routed connectivity or sign-off.
Device dimensions and net names come from the generated schematic inventory.
"""

from collections import defaultdict
from itertools import combinations
from pathlib import Path
import json
import math
import klayout.db as k

from layout_paths import BUILD_DIR as ROOT

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
            box(cell, layer, (x - 0.2, y - 0.2, x + 0.2, y + 0.2))
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
        )
    )


def assemble(names, pattern, pitch=None):
    """Return an array of identical oriented units, with exact original multiplicity."""
    p = META[DEVICES[names[0]]["cell"]]
    width, height = p["bounds"].width(), p["bounds"].height()
    px, py = pitch or (width + 1.6, height + 1.6)
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
                    col * px + width / 2,
                    row * py + height / 2,
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
        names=names,
        units=units,
        width=max(len(row) for row in pattern) * px - 1.6,
        height=len(pattern) * py - 1.6,
    )


def centroid_pattern(groups, count):
    if groups == 2 and count == 5:
        # Five units of each device, identical orientation, exact common x centroid.
        # The center space is reserved for a later, explicitly netlisted dummy.
        return [[1, 0, 1, 0, 0, None, 1, 1, 0, 0, 1]]
    if groups == 2 and count == 6:
        return [[0, 1, 1, 0, 0, 1], [1, 0, 0, 1, 1, 0]]
    if groups == 3 and count == 6:
        return [[0, 1, 2, 2, 1, 0]] * 3
    if groups == 3 and count == 5:
        points = list(range(15))
        good = [
            set(c)
            for c in combinations(points, 5)
            if sum(i % 5 for i in c) == 10 and sum(i // 5 for i in c) == 5
        ]
        for a in good:
            for b in good:
                if not a & b:
                    c = set(points) - a - b
                    return [
                        [0 if i in a else 1 if i in b else 2 for i in range(j, j + 5)]
                        for j in (0, 5, 10)
                    ]
    raise ValueError((groups, count))


groups = []
for names in [
    ("M1", "M2", "M5"),
    ("M11", "M12", "M13"),
    ("MINP", "MINN"),
    ("PCAS1", "PCAS2"),
    ("PLOAD1", "PLOAD2"),
    ("MSINK1", "MSINK2"),
    ("PREF", "PRMIR"),
]:
    groups.append(assemble(names, centroid_pattern(len(names), DEVICES[names[0]]["m"])))

# Capacitors occupy their own area with no active devices beneath them.
for idx in range(10):
    d = DEVICES[f"C{idx+2}"]
    p = META[d["cell"]]
    place(
        d["name"],
        230 + idx % 5 * 77 - p["bounds"].left,
        145 + idx // 5 * 77 - p["bounds"].bottom,
    )
    ASSIGNED.add(d["name"])
p = META[DEVICES["C1"]["cell"]]
place("C1", 535 - p["bounds"].left, 25 - p["bounds"].bottom)
ASSIGNED.add("C1")

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
failed = []
for category, rects, items in [
    ("digital", [(8, 165, 50, 137)], digital),
    ("resistors", [(70, 165, 150, 137)], resistors),
    ("analog", [(8, 8, 212, 150), (230, 8, 295, 130)], analog),
]:
    result, bad = pack(rects, items)
    for group, x, y in result:
        for unit in group["units"]:
            name, ux, uy, inst = unit[:4]
            place(name, x + ux, y + uy, unit[4] if len(unit) > 4 else 0, inst)
        layout_groups.append(
            dict(
                category=category,
                names=group["names"],
                x=x,
                y=y,
                width=group["width"],
                height=group["height"],
            )
        )
    failed.extend(bad)

LY.write(str(ROOT / "placement_candidate.gds"))
(ROOT / "placement.json").write_text(
    json.dumps(
        dict(
            placed=PLACED, groups=layout_groups, unplaced=[g["names"] for g in failed]
        ),
        indent=2,
    )
    + "\n"
)
print("Placed", len(PLACED), "of", sum(d["m"] for d in DATA["devices"]), "devices")
print("Unplaced groups:", [g["names"] for g in failed])
print("Extents:", TOP.dbbox())

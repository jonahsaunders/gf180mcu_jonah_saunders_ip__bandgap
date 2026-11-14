"""Build the Chipalooza harness slot-4 layout around the released bandgap core.

The released core GDS is placed unmodified (mirrored about the x axis so that
the vref port faces the dedicated analog pad pin s4_an[0]) and connected to the
slot4_wrapper pins with wide, planned traces. Nothing here is maze routed:
every added rectangle is declared below, then checked against the flattened
core and wrapper geometry before the GDS is written.

    python3 layout/scripts/build_slot4.py            # rebuild gds/slot_4.gds.gz
    python3 layout/scripts/build_slot4.py --check    # rebuild and compare only

Coordinates are microns in the slot frame, whose origin is the lower-left
corner of slot4_wrapper (the harness places slot_4 by that origin, unmirrored).
"""

from pathlib import Path
import argparse
import gzip
import hashlib
import json
import sys
import tempfile

import klayout.db as k

REPOSITORY = Path(__file__).resolve().parents[2]
CORE_GDS = REPOSITORY / "layout/release/Bandgap_Core_Res.gds"
WRAPPER_GDS = REPOSITORY / "layout/harness/slot4_wrapper.gds"
OUTPUT_GDS = REPOSITORY / "gds/slot_4.gds.gz"
SUMMARY = REPOSITORY / "layout/slot4/slot_4_summary.json"
CORE_NETLIST = REPOSITORY / "layout/release/reference.spice"
SLOT_NETLIST = REPOSITORY / "layout/slot4/slot_4.spice"

TOP = "slot_4"
CORE = "Bandgap_Core_Res"
DBU = 0.001
GRID = 5  # nm

# GF180MCU-D 5LM layer numbers (datatype 0 drawing, 10 label/pin, 4 dummy fill).
COMP, POLY = 22, 30
M1, VIA1, M2, VIA2, M3, VIA3, M4, VIA4, M5 = 34, 35, 36, 38, 42, 40, 46, 41, 81
FUSETOP = 75
STACK = [M1, VIA1, M2, VIA2, M3, VIA3, M4, VIA4, M5]
METALS = [M1, M2, M3, M4, M5]
LOWER = [M1, M2, M3, M4]
VIA_BETWEEN = {VIA1: (M1, M2), VIA2: (M2, M3), VIA3: (M3, M4), VIA4: (M4, M5)}

# Core placement: y' = CORE_Y - y (mirror about the x axis), x' = CORE_X + x.
# This puts the core flush against the pad side of the slot with its vref port
# at (1261.0, 51.25), inside the y span of the s4_an[0] pin (29.695..74.015).
CORE_X, CORE_Y = 578.6, 401.65

# Harness pin used for every core port.
PIN_MAP = {
    "avdd": "vdd_5v0",
    "avss": "vss_5v0",
    "dvdd": "vdd_3v3",
    "dvss": "vss_3v3",
    "vref": "s4_an[0]",
    **{f"b{i}": f"dig_in[{i}]" for i in range(7)},
}

# Clearance demanded between added metal and any circuit metal it is not meant
# to touch. The foundry minimums are 0.23..0.30 um (M1..M4) and 0.46 um (M5).
CLEARANCE = {M1: 0.50, M2: 0.50, M3: 0.50, M4: 0.50, M5: 0.90}
MIN_SPACE = {M1: 0.23, M2: 0.28, M3: 0.28, M4: 0.28, M5: 0.46}
MIN_WIDTH = {M1: 0.23, M2: 0.28, M3: 0.28, M4: 0.28, M5: 0.44}
# Only here, where seven 0.29 um pin lines leave the pin row on its 0.58 um
# pitch, is added metal allowed down to the foundry minimum spacing.
PIN_ESCAPE = (0.0, 150.0, 30.0, 165.0)
VIA_SIZE = 0.26
VIA_MARGIN = 0.60  # cluster centre to metal edge: 0.21 um enclosure of the cuts
VIA_PITCH = 1.6
# Cut clusters sit on one fixed lattice per via level, shifted between levels
# so that no cluster is stacked on the one below it.
VIA_ORIGIN = {
    VIA1: (0.8, 0.05),
    VIA2: (0.0, 0.05),
    VIA3: (0.0, 0.85),
    VIA4: (0.8, 0.85),
}
MAX_STRIPE = 30.0  # MSLOT.1: widest metal allowed without slots, um

# Dummy fill (datatype 4), same drawing rules as layout/scripts/add_density_fill.py.
FILL_REGION = (3.0, 3.0, 574.0, 409.7)  # left of the core, inside the border
FILL_TO_CIRCUIT = 2.005  # DMn.3: dummy metal to circuit metal on the same layer
FILL_TO_ADJACENT = 1.005  # design-manual DM.4-DM.7, not coded in the deck
POLY_FILL_REGION = (3.0, 3.0, 548.0, 409.7)  # 30 um from the core (DPF.8/DPF.11)
POLY_FILL_TARGET = 0.155  # die density aimed for; PL.8 needs 14 %
DENSITY_MINIMUM = {
    COMP: 0.25,
    POLY: 0.14,
    M1: 0.30,
    M2: 0.30,
    M3: 0.30,
    M4: 0.30,
    M5: 0.30,
}
DENSITY_MARGIN = 0.01


def nm(value):
    result = round(value * 1000)
    if result % GRID:
        raise ValueError(f"{value} um is off the {GRID} nm grid")
    return result


def box(x0, y0, x1, y1):
    if not (x1 > x0 and y1 > y0):
        raise ValueError(f"empty box {(x0, y0, x1, y1)}")
    return k.Box(nm(x0), nm(y0), nm(x1), nm(y1))


class Plan:
    """Rectangles added at slot level, kept per net and layer."""

    def __init__(self):
        self.shapes = {}  # net -> layer -> [Box]
        self.notes = {}  # net -> [description]
        self.dense = []  # windows where every via lattice point is used
        self.fill = {}  # layer -> Region of dummy fill (datatype 4)

    def add(self, net, layer, x0, y0, x1, y1):
        self.shapes.setdefault(net, {}).setdefault(layer, []).append(
            box(x0, y0, x1, y1)
        )

    def stack(self, net, layers, x0, y0, x1, y1):
        for layer in layers:
            self.add(net, layer, x0, y0, x1, y1)

    def note(self, net, text):
        self.notes.setdefault(net, []).append(text)

    def dense_vias(self, x0, y0, x1, y1):
        self.dense.append(box(x0, y0, x1, y1))

    def place_vias(self):
        """Join every pair of adjacent metals of a net wherever they overlap.

        2x2 clusters of 0.26 um cuts (0.26 um apart) are dropped on a fixed
        1.6 um lattice, so clusters are 0.82 um apart and never form the 4x4
        array that needs wider via spacing. Inside the declared dense windows
        every lattice point is used; elsewhere one point in eight, which is
        still a cluster every 6.4 um along every 3.2 um of strap width.
        """
        pitch, margin = nm(VIA_PITCH), nm(VIA_MARGIN)
        counts = {}
        for net in sorted(self.shapes):
            for via, (below, above) in VIA_BETWEEN.items():
                common = (
                    self.region(below, net).merged() & self.region(above, net).merged()
                )
                allowed = common.sized(-margin)
                if allowed.is_empty():
                    continue
                origin_x, origin_y = (nm(v) for v in VIA_ORIGIN[via])
                points = set()
                for piece in allowed.decompose_trapezoids_to_region().each():
                    area = piece.bbox()
                    for i in range(
                        -((origin_x - area.left) // pitch),
                        (area.right - origin_x) // pitch + 1,
                    ):
                        for j in range(
                            -((origin_y - area.bottom) // pitch),
                            (area.top - origin_y) // pitch + 1,
                        ):
                            points.add((i, j))
                for i, j in sorted(points):
                    centre = k.Point(origin_x + i * pitch, origin_y + j * pitch)
                    if not (i % 4 == 0 and j % 2 == 0) and not any(
                        window.contains(centre) for window in self.dense
                    ):
                        continue
                    for dx in (-390, 130):
                        for dy in (-390, 130):
                            self.shapes[net].setdefault(via, []).append(
                                k.Box(
                                    centre.x + dx,
                                    centre.y + dy,
                                    centre.x + dx + nm(VIA_SIZE),
                                    centre.y + dy + nm(VIA_SIZE),
                                )
                            )
                    counts[via] = counts.get(via, 0) + 1
        return counts

    def region(self, layer, net=None):
        result = k.Region()
        for name, layers in self.shapes.items():
            if net is None or name == net:
                for item in layers.get(layer, []):
                    result.insert(item)
        return result


def wrapper_pins(wrapper_layout):
    """Pin rectangles (pin-purpose shapes) keyed by their label text."""
    cell = wrapper_layout.top_cell()
    pins = {}
    for info_layer in wrapper_layout.layer_indexes():
        info = wrapper_layout.get_info(info_layer)
        if info.datatype != 10:
            continue
        boxes = [s.bbox() for s in cell.shapes(info_layer).each() if not s.is_text()]
        for shape in cell.shapes(info_layer).each():
            if not shape.is_text():
                continue
            point = shape.text.trans.disp
            hits = [b for b in boxes if b.contains(k.Point(point.x, point.y))]
            if len(hits) != 1:
                raise RuntimeError(f"pin {shape.text.string}: {len(hits)} pin boxes")
            pins[shape.text.string] = (info.layer, hits[0])
    return pins


def drawn_pin(wrapper_layout, layer, pin_box):
    """The drawn metal rectangle of a pin (it may be larger than the pin box)."""
    cell = wrapper_layout.top_cell()
    index = wrapper_layout.find_layer(layer, 0)
    hits = [
        s.bbox()
        for s in cell.shapes(index).each()
        if s.bbox().contains(pin_box.center()) and s.bbox().overlaps(pin_box)
    ]
    if len(hits) != 1:
        raise RuntimeError(f"expected one drawn shape under pin at {pin_box}")
    return hits[0]


def um(value):
    return value / 1000.0


def plan_routes(pins, wrapper_layout):
    """Declare every slot-level trace. Returns the Plan."""
    plan = Plan()

    def pin_um(name, layer):
        pin_layer, pin_box = pins[name]
        if pin_layer != layer:
            raise RuntimeError(f"pin {name} is on layer {pin_layer}, expected {layer}")
        return um(pin_box.left), um(pin_box.bottom), um(pin_box.right), um(pin_box.top)

    # Columns shared by the crossings in front of the core's left edge
    # (the core's own metal starts at x = 579.2).
    west0, west1 = 520.0, 536.4  # M5 landing plates and via fields, west side
    coll0, coll1 = 538.0, 568.0  # AVDD collector, M5
    east0, east1 = 569.6, 578.6  # via fields between collector and core
    core_edge_strap = 581.2  # right edge of the core's dvdd/dvss edge straps
    strap0 = 3.0  # lower metals start just outside the pin row

    # ---- vref -> s4_an[0] -------------------------------------------------
    # The core's vref strap ends on the core's right edge at y 50.25..52.25.
    # 0.6 um outside the core it opens into a plate as tall as the pin and is
    # brought down to the pin's metal 3 through two full via fields. Metal 3
    # starts 2.05 um from the core, whose metal-3 dummy fill reaches its edge.
    px0, py0, px1, py1 = pin_um("s4_an[0]", M3)
    plate0, plate1 = 1262.6, 1269.5
    plan.add("vref", M5, 1261.0, 50.25, plate0, 52.25)
    plan.add("vref", M4, 1261.0, 50.65, plate0, 51.85)
    plan.stack("vref", (M5, M4), plate0, py0, plate1, py1)
    plan.add("vref", M3, 1264.05, py0, px1, py1)
    plan.dense_vias(plate0, py0, px1, py1)
    plan.note(
        "vref",
        f"core port to pin metal: {px0 - 1262.0:.2f} um; plate "
        f"{plate1 - plate0:.1f} x {py1 - py0:.2f} um on M5/M4, "
        f"{px1 - 1264.05:.2f} x {py1 - py0:.2f} um on M3",
    )

    # ---- avdd -> vdd_5v0 --------------------------------------------------
    ax0, ay0, _, ay1 = pin_um("vdd_5v0", M5)
    gap = round((ay1 - ay0 - 3 * MAX_STRIPE) / 2, 3)
    for index in range(3):
        y = round(ay0 + index * (MAX_STRIPE + gap), 3)
        plan.add("avdd", M5, ax0, y, coll1, round(y + MAX_STRIPE, 3))
        plan.stack("avdd", LOWER, strap0, y, coll1, round(y + MAX_STRIPE, 3))
    plan.stack("avdd", (M5, M2, M1), coll0, 17.0, coll1, 397.65)
    # Metal 3/4 of the collector step aside where other nets pass beneath it.
    for lower, upper in ((17.0, 95.0), (117.5, 185.8), (242.7, 397.65)):
        plan.stack("avdd", (M4, M3), coll0, lower, coll1, upper)
    # Two taps onto the west rail of the PREF/PMIR supply ring ...
    plan.add("avdd", M5, coll1, 17.0, 596.9, 47.0)
    plan.add("avdd", M5, coll1, 54.0, 596.9, 84.0)
    # ... and one over the unused dummy row of M11/M12/M13 onto the spine.
    plan.add("avdd", M5, coll1, 378.65, 653.9, 397.65)
    plan.note(
        "avdd",
        f"three {MAX_STRIPE:.0f} um stripes from the pin and a {coll1 - coll0:.0f} um "
        "collector, all five metals strapped; two 30 um M5 taps onto the PREF "
        "ring and a 19 um M5 tap onto the M11-M13 spine",
    )

    # ---- avss -> vss_5v0 --------------------------------------------------
    sx0, sy0, _, sy1 = pin_um("vss_5v0", M5)
    top = 410.2  # 0.2 um inside the wrapper's 2.31 um border
    plan.add("avss", M5, sx0, sy0, 3.5, sy1)
    for lower, upper in ((356.5, 382.0), (384.6, top)):
        plan.stack("avss", METALS, strap0, lower, 534.0, upper)
    plan.stack("avss", METALS, 504.0, 356.5, 534.0, top)
    # Channel above the core, then down onto the core's full-length rail.
    plan.add("avss", M5, 504.0, 399.0, 1144.6, top)
    plan.add("avss", M5, 702.1, 395.65, 1144.6, 400.0)
    plan.stack("avss", (M4, M3), 534.0, 400.8, 1144.6, top)
    plan.dense_vias(534.0, 399.0, 1144.6, top)
    plan.note(
        "avss",
        "two 25.5 um stripes from the pin, all five metals strapped; 11.2 um M5 "
        "channel above the core strapped to 9.4 um of M4 and M3; merged with "
        "the core rail over 442.5 um",
    )

    # ---- dvdd -> vdd_3v3 --------------------------------------------------
    dx0, _, _, dy1 = pin_um("vdd_3v3", M5)
    stripe = round(dy1 - MAX_STRIPE, 3)
    plan.add("dvdd", M5, dx0, stripe, west1, dy1)
    plan.stack("dvdd", LOWER, strap0, stripe, west1, dy1)
    plan.stack("dvdd", METALS, west0, stripe, west1, 114.25)
    plan.stack("dvdd", (M4, M3), west0, 98.25, east1, 114.25)
    plan.add("dvdd", M5, east0, 98.25, core_edge_strap, 114.25)
    plan.note(
        "dvdd",
        "30 um stripe, all five metals strapped; 16 um M4+M3 underpass below "
        "the AVDD collector; M5 plate merged with the core's 16 um edge strap",
    )

    # ---- dvss -> vss_3v3 --------------------------------------------------
    gx0, _, _, _ = pin_um("vss_3v3", M5)
    plan.add("dvss", M5, gx0, 118.0, west1, 148.0)
    plan.stack("dvss", LOWER, strap0, 118.0, west1, 148.0)
    plan.stack("dvss", (M5, M2, M1), 506.4, 118.0, west1, 239.45)
    plan.stack("dvss", (M4, M3), 506.4, 118.0, west1, 199.0)
    for lower, upper in ((189.05, 213.0), (215.5, 239.45)):
        plan.add("dvss", M4, west0, lower, east1, upper)
    # Metal 3 doubles the underpass wherever the trim-bit lines do not run.
    for lower, upper in ((189.05, 199.85), (226.0, 239.45)):
        plan.add("dvss", M3, west0, lower, east1, upper)
    plan.add("dvss", M5, east0, 189.05, core_edge_strap, 239.45)
    plan.note(
        "dvss",
        "30 um stripe and riser, all five metals strapped (M3/M4 stop where the "
        "trim bits cross); two 24 um M4 underpasses, M3 doubled outside the "
        "trim-bit band; M5 plate merged with the core's 50.4 um edge strap",
    )

    # ---- b0..b6 -> dig_in[0..6] -------------------------------------------
    # The pins are 0.29 um metal-3 lines on a 0.58 um pitch. Each leaves the
    # pin row by a short staircase onto a 2 um pad, rises on metal 4 and runs
    # the long way on metal 3 and metal 4 in parallel. Just before the core a
    # 23 um metal-5 bridge is the only link to the last 77 um metal-3 stub, so
    # the long wire is not connected to the trim-logic gates until metal 5 is
    # patterned (antenna rules ANT.16_i); the stub passes under the DVSS riser
    # and the AVDD collector into the core's edge pad.
    for index in range(7):
        net = f"b{index}"
        pin_layer, pin_box = pins[PIN_MAP[net]]
        drawn = drawn_pin(wrapper_layout, pin_layer, pin_box)
        wire_lo, wire_hi = um(drawn.bottom), um(drawn.top)
        track = round(221.65 - 3.2 * index, 3)  # y of the core pad for this bit
        x = round(3.8 + 3.2 * index, 3)
        plan.add(net, M3, 0.0, wire_lo, x + 2.0, wire_hi)
        plan.add(net, M3, x, 153.0, x + 2.0, wire_hi)
        plan.add(net, M4, x, 153.0, x + 2.0, track + 1.0)
        plan.stack(net, (M4, M3), x, track - 1.0, 486.0, track + 1.0)
        plan.add(net, M5, 480.0, track - 1.0, 503.0, track + 1.0)
        plan.add(net, M4, 497.0, track - 1.0, 509.0, track + 1.0)
        plan.add(net, M3, 503.2, track - 1.0, 580.4, track + 1.0)
    plan.dense_vias(0.0, 150.0, 30.0, 165.0)
    plan.dense_vias(478.0, 200.0, 512.0, 224.0)
    plan.note(
        "b0..b6",
        "2 um tracks: M3 pad at the pin row, M4 riser, M3+M4 in parallel for the "
        "465 um run, 23 um M5 bridge, M3 for the last 77 um into the core pads; "
        "12 cuts at every layer change",
    )

    # Via fields at the pin ends of the supply straps and across the whole
    # crossing zone in front of the core; sparse stitching in between.
    plan.dense_vias(strap0, 3.0, 15.0, 410.4)
    plan.dense_vias(west0 - 16.0, 3.0, east1, 410.4)
    plan.via_counts = plan.place_vias()
    return plan


def flat_region(layout, cell, layer, datatype):
    index = layout.find_layer(layer, datatype)
    if index is None:
        return k.Region()
    return k.Region(layout.begin_shapes(cell, index))


def core_trans():
    return k.Trans(k.Trans.M0, nm(CORE_X), nm(CORE_Y))


def squares(region_box, size, pitch, origin, stagger, shift=0.0):
    """The staggered square lattice of add_density_fill.py, clipped to a box."""
    x0, y0, x1, y1 = region_box
    result = k.Region()
    row = 0
    while origin[1] + row * pitch <= y1:
        col = 0
        while origin[0] + col * pitch <= x1:
            x = origin[0] + col * pitch + (row % 2) * stagger + shift
            y = origin[1] + row * pitch + (col % 2) * stagger + shift
            if x >= x0 and y >= y0 and x + size <= x1 and y + size <= y1:
                result.insert(
                    box(
                        round(x, 3), round(y, 3), round(x + size, 3), round(y + size, 3)
                    )
                )
            col += 1
        row += 1
    return result


def plan_fill(plan, wrapper_layout):
    """Dummy fill for the otherwise empty area left of the core.

    The core carries its own fill; without more, the slot as a whole would
    fall below the die-level density minimums. Squares keep 2 um from circuit
    metal on their own layer and 1 um from circuit metal one layer up or down.
    """
    wrapper = wrapper_layout.top_cell()

    def circuit(layer):
        return plan.region(layer) + flat_region(wrapper_layout, wrapper, layer, 0)

    # COMP: 5 um squares on a staggered 8 um lattice; nothing else is nearby.
    comp = squares(FILL_REGION, 5.0, 8.0, (2.0, 4.0), 1.6)
    plan.fill[COMP] = comp
    # Poly: 5.6 um squares over dummy COMP, clear of circuit metal 1 and 2.
    blocked = (circuit(M1) + circuit(M2)).sized(nm(FILL_TO_CIRCUIT))
    limit = box(*POLY_FILL_REGION)
    candidates = [
        square.bbox().enlarged(300, 300)
        for square in comp.each()
        if square.bbox().enlarged(300, 300).inside(limit)
    ]
    candidates = [c for c in candidates if (k.Region(c) & blocked).is_empty()]
    plan.poly_candidates = candidates
    plan.fill[POLY] = k.Region()
    # Metal: 2 um squares on a staggered 3.2 um lattice, shifted per level.
    for level, layer in enumerate(METALS):
        obstacle = circuit(layer).sized(nm(FILL_TO_CIRCUIT))
        if level:
            obstacle += circuit(METALS[level - 1]).sized(nm(FILL_TO_ADJACENT))
        if level < len(METALS) - 1:
            obstacle += circuit(METALS[level + 1]).sized(nm(FILL_TO_ADJACENT))
        lattice = squares(FILL_REGION, 2.0, 3.2, (2.0, 4.0), 0.5, 0.5 * level)
        plan.fill[layer] = lattice.not_interacting(obstacle.merged())


def assemble(plan):
    layout = k.Layout()
    layout.dbu = DBU
    layout.read(str(CORE_GDS))
    if abs(layout.dbu - DBU) > 1e-12:
        raise RuntimeError("core GDS database unit is not 1 nm")
    core = layout.cell(CORE)
    if core is None or core.child_cells():
        raise RuntimeError("expected a single flat core cell")

    wrapper_layout = k.Layout()
    wrapper_layout.read(str(WRAPPER_GDS))
    wrapper = wrapper_layout.top_cell()
    if abs(wrapper_layout.dbu - DBU) > 1e-12:
        raise RuntimeError("wrapper GDS database unit is not 1 nm")

    top = layout.create_cell(TOP)
    # The wrapper's boundary, pin metal, pin shapes and labels, verbatim.
    for index in wrapper_layout.layer_indexes():
        info = wrapper_layout.get_info(index)
        target = top.shapes(layout.layer(info.layer, info.datatype))
        for shape in wrapper.shapes(index).each():
            target.insert(shape)
    top.insert(
        k.CellInstArray(core.cell_index(), k.Trans(k.Trans.M0, nm(CORE_X), nm(CORE_Y)))
    )
    for net in sorted(plan.shapes):
        for layer in sorted(plan.shapes[net]):
            target = top.shapes(layout.layer(layer, 0))
            for item in plan.shapes[net][layer]:
                target.insert(item)
    for layer in sorted(plan.fill):
        top.shapes(layout.layer(layer, 4)).insert(plan.fill[layer])
    return layout, wrapper_layout


def densities(layout, plan=None):
    """Drawn plus dummy area of each density-checked layer over the slot outline."""
    top = layout.cell(TOP)
    area = top.bbox().area()
    result = {}
    for layer in DENSITY_MINIMUM:
        region = flat_region(layout, top, layer, 0) + flat_region(layout, top, layer, 4)
        result[layer] = region.merged().area() / area
    return result


def choose_poly_fill(plan, core_poly_area, slot_area):
    """Evenly spaced dummy poly squares, enough for the target die density."""
    square = nm(5.6) ** 2
    required = max(0, -(-int(POLY_FILL_TARGET * slot_area - core_poly_area) // square))
    candidates = plan.poly_candidates
    if len(candidates) < required:
        raise RuntimeError(f"only {len(candidates)} of {required} dummy poly sites")
    chosen = k.Region()
    for index in range(required):
        chosen.insert(candidates[index * len(candidates) // required])
    plan.fill[POLY] = chosen
    # Dummy metal 1 also keeps 1 um from poly, as in the core.
    plan.fill[M1] = plan.fill[M1].not_interacting(chosen.sized(nm(FILL_TO_ADJACENT)))
    return required


def extract_nets(layout, cell_name):
    """Metal-only connectivity (no devices): returns [(labels, bbox)] per net."""
    flat = k.Layout()
    flat.dbu = layout.dbu
    target = flat.create_cell("FLAT")
    target.copy_tree(layout.cell(cell_name))
    target.flatten(True)
    extractor = k.LayoutToNetlist(k.RecursiveShapeIterator(flat, target, []))
    fuse = extractor.make_layer(flat.layer(FUSETOP, 0), "fusetop")
    layers = {}
    for number in STACK:
        layers[number] = extractor.make_layer(flat.layer(number, 0), f"l{number}")
    # A via4 cut under FuseTop contacts the MIM top plate, not metal 4.
    layers[VIA4] = layers[VIA4] - fuse
    for number in STACK:
        extractor.connect(layers[number])
    for lower, upper in zip(STACK, STACK[1:]):
        extractor.connect(layers[lower], layers[upper])
    for metal in METALS:
        labels = extractor.make_text_layer(flat.layer(metal, 10), f"t{metal}")
        extractor.connect(layers[metal], labels)
    extractor.extract_netlist()
    circuit = extractor.netlist().circuit_by_name("FLAT")
    nets = []
    for net in circuit.each_net():
        names = frozenset(part for part in net.name.split(",") if part)
        nets.append(names)
    return nets


def check(layout, wrapper_layout, plan):
    """Geometric and connectivity checks; raises on the first failure."""
    top = layout.cell(TOP)
    core = layout.cell(CORE)
    report = {}

    # 1. The slot stays inside the wrapper outline, and added metal stays out
    #    of the 2.31 um border except where it lands on a pin it connects to.
    outline = wrapper_layout.top_cell().bbox()
    if not top.bbox().inside(outline) and top.bbox() != outline:
        raise RuntimeError(f"slot bbox {top.bbox()} exceeds wrapper {outline}")
    border = nm(2.31)
    interior = k.Region(
        k.Box(
            outline.left + border,
            outline.bottom + border,
            outline.right - border,
            outline.top - border,
        )
    )
    core_box = core.bbox().transformed(core_trans())
    if not k.Region(core_box).inside(interior).count():
        raise RuntimeError("core instance enters the wrapper border")
    pins = wrapper_pins(wrapper_layout)
    for net, layers in plan.shapes.items():
        pin_layer, pin_box = pins[PIN_MAP[net]]
        allowed = k.Region(
            drawn_pin(wrapper_layout, pin_layer, pin_box).enlarged(nm(1.2), 0)
        )
        for layer in layers:
            stray = plan.region(layer, net) - interior - allowed
            if not stray.is_empty():
                raise RuntimeError(f"{net} layer {layer} enters the border: {stray[0]}")

    # 2. Width, spacing and enclosure of everything that was added.
    escape = box(*PIN_ESCAPE)
    place = core_trans()
    wrapper = wrapper_layout.top_cell()
    core_dummy = {}
    circuit_all = {}
    for layer in METALS:
        added_all = plan.region(layer).merged()
        narrow = added_all.width_check(nm(MIN_WIDTH[layer]))
        if narrow.count():
            raise RuntimeError(f"layer {layer}: added metal below minimum width")
        existing = flat_region(layout, core, layer, 0).transformed(place)
        existing += flat_region(wrapper_layout, wrapper, layer, 0)
        existing.merge()
        circuit_all[layer] = (existing + added_all).merged()
        core_dummy[layer] = flat_region(layout, core, layer, 4).transformed(place)
        clearance = nm(CLEARANCE[layer])
        for net in plan.shapes:
            mine = plan.region(layer, net).merged()
            if mine.is_empty():
                continue
            others = k.Region()
            for other in plan.shapes:
                if other != net:
                    others += plan.region(layer, other)
            foreign = existing.not_interacting(mine) + others
            overlap = mine & (foreign + core_dummy[layer])
            if not overlap.is_empty():
                raise RuntimeError(
                    f"{net} layer {layer}: overlaps other metal at {overlap[0]}"
                )
            tight = mine.separation_check(foreign, nm(MIN_SPACE[layer]))
            if tight.count():
                raise RuntimeError(
                    f"{net} layer {layer}: below minimum spacing at {tight[0]}"
                )
            for pair in mine.separation_check(foreign, clearance).each():
                if not pair.bbox().inside(escape):
                    raise RuntimeError(
                        f"{net} layer {layer}: closer than {CLEARANCE[layer]} um to "
                        f"other metal at {pair}"
                    )
            for pair in mine.space_check(clearance).each():
                if not pair.bbox().inside(escape):
                    raise RuntimeError(f"{net} layer {layer}: notch at {pair}")
            near_dummy = mine.separation_check(core_dummy[layer], nm(FILL_TO_CIRCUIT))
            if near_dummy.count():
                raise RuntimeError(
                    f"{net} layer {layer}: within 2 um of core dummy fill at "
                    f"{near_dummy[0]}"
                )
    fuse = flat_region(layout, core, FUSETOP, 0).transformed(place)
    all_cuts = {}
    for via, (below, above) in VIA_BETWEEN.items():
        cuts = plan.region(via)
        if cuts.space_check(nm(VIA_SIZE)).count():
            raise RuntimeError(f"via layer {via}: cuts closer than {VIA_SIZE} um")
        core_cuts = flat_region(layout, core, via, 0).transformed(place)
        all_cuts[via] = core_cuts + cuts
        if cuts.separation_check(core_cuts, nm(1.0)).count():
            raise RuntimeError(f"via layer {via}: added cut within 1 um of a core cut")
        for net in plan.shapes:
            mine = plan.region(via, net)
            for metal in (below, above):
                uncovered = mine.sized(nm(0.2)) - plan.region(metal, net)
                if not uncovered.is_empty():
                    raise RuntimeError(
                        f"{net} via layer {via}: cut not enclosed by its own layer "
                        f"{metal} at {uncovered[0]}"
                    )
        for layer in (via, M4):
            shapes = plan.region(layer)
            if (
                shapes.separation_check(fuse, nm(5.0)).count()
                or not (shapes & fuse).is_empty()
            ):
                raise RuntimeError(
                    f"layer {layer}: added shape within 5 um of a MIM cap"
                )
        report[f"via{via}_cuts"] = cuts.count()

    # MSLOT.1 as the deck computes it: no metal region that holds a square
    # larger than 30 um, after removing 0.2 um around vias above and below.
    half = nm(MAX_STRIPE) // 2

    def opened(region):
        return region.sized(0, -half).sized(-half, 0).sized(0, half).sized(half, 0)

    exempt = (flat_region(layout, core, M4, 0).transformed(place) & fuse).sized(nm(5.0))
    contact = flat_region(layout, core, 33, 0).transformed(place)
    for index, layer in enumerate(METALS):
        punched = all_cuts[STACK[2 * index - 1]] if index else contact
        if index < len(METALS) - 1:
            punched = punched + all_cuts[STACK[2 * index + 1]]
        wide = opened(opened(circuit_all[layer]) - punched.sized(200) - exempt)
        wide = wide.interacting(plan.region(layer))
        if not wide.is_empty():
            raise RuntimeError(
                f"layer {layer}: unslotted metal over 30 um at {wide[0]}"
            )

    # Dummy fill: clear of circuit metal and of the core, and enough of it.
    core_box = k.Region(core.bbox().transformed(place))
    for layer, dummy in plan.fill.items():
        if layer in METALS:
            if (
                dummy.separation_check(circuit_all[layer], nm(2.0)).count()
                or not (dummy & circuit_all[layer]).is_empty()
            ):
                raise RuntimeError(f"layer {layer}: dummy within 2 um of circuit metal")
            every = dummy + core_dummy[layer]
            if every.space_check(nm(0.98)).count():
                raise RuntimeError(f"layer {layer}: dummy squares closer than 0.98 um")
            if dummy.separation_check(fuse, nm(6.0)).count():
                raise RuntimeError(f"layer {layer}: dummy within 6 um of a MIM cap")
        reach = {COMP: 4.1, POLY: 30.0}.get(layer, 2.2)
        if not (dummy & core_box.sized(nm(reach))).is_empty():
            raise RuntimeError(f"layer {layer}: dummy within {reach} um of the core")
    if not (plan.fill[POLY] - plan.fill[COMP].sized(300)).is_empty():
        raise RuntimeError("dummy poly not on dummy COMP")
    achieved = densities(layout)
    for layer, minimum in DENSITY_MINIMUM.items():
        if achieved[layer] < minimum + DENSITY_MARGIN:
            raise RuntimeError(
                f"layer {layer}: density {achieved[layer]:.3f} below "
                f"{minimum + DENSITY_MARGIN:.2f}"
            )
    names = {
        COMP: "comp",
        POLY: "poly2",
        M1: "metal1",
        M2: "metal2",
        M3: "metal3",
        M4: "metal4",
        M5: "metal5",
    }
    report["density_percent"] = {
        names[layer]: round(100 * achieved[layer], 2) for layer in DENSITY_MINIMUM
    }
    report["dummy_squares"] = {
        names[layer]: plan.fill[layer].count() for layer in plan.fill
    }

    # 3. Connectivity. Metal-only extraction of the core, the wrapper and the
    #    finished slot: every core port must merge with exactly its harness pin,
    #    and the net count must drop by exactly the number of connections made
    #    (a short to any unlabeled net would lower it further).
    core_nets = extract_nets(layout, CORE)
    wrapper_nets = extract_nets(wrapper_layout, wrapper_layout.top_cell().name)
    slot_nets = extract_nets(layout, TOP)
    expected_groups = {frozenset((a, b)) for a, b in PIN_MAP.items()}
    labeled = {names for names in slot_nets if names}
    harness_names = set().union(*[names for names in wrapper_nets if names])
    for group in expected_groups:
        if group not in labeled:
            raise RuntimeError(f"connection {sorted(group)} not made exactly")
    for names in labeled:
        if len(names) > 1 and names not in expected_groups:
            raise RuntimeError(f"unexpected short between {sorted(names)}")
    unconnected = sorted(
        next(iter(names))
        for names in labeled
        if len(names) == 1 and next(iter(names)) in harness_names
    )
    if len(slot_nets) != len(core_nets) + len(wrapper_nets) - len(PIN_MAP):
        raise RuntimeError(
            f"net count {len(slot_nets)} != {len(core_nets)} core + "
            f"{len(wrapper_nets)} wrapper - {len(PIN_MAP)} connections"
        )
    report["metal_nets"] = {
        "core": len(core_nets),
        "wrapper": len(wrapper_nets),
        "slot": len(slot_nets),
    }
    report["unconnected_harness_pins"] = unconnected
    return report


def slot_netlist(pins_in_order):
    """The released core netlist with its ports renamed to the harness pins.

    The slot adds wiring only, so its reference netlist is the core's device
    list under the wrapper's port list; unused harness pins stay unconnected.
    """
    lines = [
        "* slot_4: Bandgap_Core_Res wired to the Chipalooza slot4_wrapper pins.",
        "* Generated by layout/scripts/build_slot4.py from "
        "layout/release/reference.spice; do not edit.",
    ]
    header = ".subckt slot_4"
    for name in pins_in_order:
        if len(header) + len(name) + 1 > 78:
            lines.append(header)
            header = "+"
        header += " " + name
    lines.append(header)
    devices = 0
    for line in CORE_NETLIST.read_text().splitlines():
        if not line.strip() or line.startswith("*"):
            continue
        if line.lower().startswith(".subckt"):
            ports = line.split()[2:]
            if sorted(ports) != sorted(PIN_MAP):
                raise RuntimeError(f"core ports changed: {ports}")
            continue
        if line.lower().startswith(".ends"):
            continue
        if line[0] in ".+":
            raise RuntimeError(f"unexpected netlist line: {line}")
        tokens = line.split()
        tokens[1:] = [PIN_MAP.get(token, token) for token in tokens[1:]]
        lines.append(" ".join(tokens))
        devices += 1
    lines.append(".ends slot_4")
    return "\n".join(lines) + "\n", devices


# Port order of slot4_wrapper in the harness netlists.
WRAPPER_PORTS = (
    ["s4_an[0]", "s4_an[1]", "vdd_3v3", "vss_5v0", "vdd_5v0", "vss_3v3"]
    + [f"analog_bus{i}" for i in (3, 2, 1, 0)]
    + ["vbias", "ibias0", "ibias1"]
    + [f"dig_out[{i}]" for i in range(12)]
    + [f"dig_in[{i}]" for i in range(24)]
    + ["reset", "enable", "clk"]
)


def write_gds(layout):
    """Deterministic GDS bytes (no timestamps), hierarchy preserved."""
    options = k.SaveLayoutOptions()
    options.format = "GDS2"
    options.gds2_write_timestamps = False
    options.gds2_libname = TOP
    options.select_all_layers()
    options.clear_cells()
    options.add_cell(layout.cell(TOP).cell_index())
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "slot_4.gds"
        layout.write(str(path), options)
        return path.read_bytes()


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="rebuild in memory and compare with the committed GDS and summary",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT_GDS)
    arguments = parser.parse_args()

    wrapper_layout = k.Layout()
    wrapper_layout.read(str(WRAPPER_GDS))
    pins = wrapper_pins(wrapper_layout)
    plan = plan_routes(pins, wrapper_layout)
    plan_fill(plan, wrapper_layout)
    core_layout = k.Layout()
    core_layout.read(str(CORE_GDS))
    core_cell = core_layout.top_cell()
    core_poly = (
        flat_region(core_layout, core_cell, POLY, 0)
        + flat_region(core_layout, core_cell, POLY, 4)
    ).merged()
    choose_poly_fill(plan, core_poly.area(), wrapper_layout.top_cell().bbox().area())
    layout, wrapper_layout = assemble(plan)
    report = check(layout, wrapper_layout, plan)
    data = write_gds(layout)
    if sorted(WRAPPER_PORTS) != sorted(pins):
        raise RuntimeError("wrapper pin labels differ from the harness port list")
    netlist, devices = slot_netlist(WRAPPER_PORTS)

    top = layout.cell(TOP)
    summary = {
        "top_cell": TOP,
        "slot": 4,
        "outline_um": [
            um(v)
            for v in (
                top.bbox().left,
                top.bbox().bottom,
                top.bbox().right,
                top.bbox().top,
            )
        ],
        "core": {
            "cell": CORE,
            "source": str(CORE_GDS.relative_to(REPOSITORY)),
            "source_sha256": sha256(CORE_GDS.read_bytes()),
            "placement": f"mirror about x axis, origin at ({CORE_X}, {CORE_Y}) um",
        },
        "wrapper": {
            "source": str(WRAPPER_GDS.relative_to(REPOSITORY)),
            "source_sha256": sha256(WRAPPER_GDS.read_bytes()),
        },
        "pin_map": PIN_MAP,
        "reference_netlist": {
            "path": str(SLOT_NETLIST.relative_to(REPOSITORY)),
            "devices": devices,
        },
        "traces": plan.notes,
        "checks": report,
        "gds_sha256_uncompressed": sha256(data),
    }
    text = json.dumps(summary, indent=2) + "\n"

    if arguments.check:
        committed = gzip.decompress(arguments.output.read_bytes())
        if sha256(committed) != summary["gds_sha256_uncompressed"]:
            print("MISMATCH: committed GDS differs from a fresh build", file=sys.stderr)
            return 1
        if SUMMARY.read_text() != text or SLOT_NETLIST.read_text() != netlist:
            print("MISMATCH: committed summary or netlist differs", file=sys.stderr)
            return 1
        print("slot_4 rebuild matches", summary["gds_sha256_uncompressed"])
        return 0

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as stream:
            stream.write(data)
    SUMMARY.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY.write_text(text)
    SLOT_NETLIST.write_text(netlist)
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())

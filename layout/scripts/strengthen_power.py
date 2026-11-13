"""Build the planned supply grid, then add precision straps and short taps.

Stage 1 draws the explicit AVDD/AVSS floorplan from power_plan.py: wide,
straight M5 rails over dummy perimeters and routing channels, short M4 bridges
where a rail passes a signal corridor, and regular via4 arrays wherever a rail
lies over metal4 of its own net. Every rectangle is checked against the
actual routed metal before it is drawn.

Stage 2 keeps the earlier behaviour for everything else: 2 um M5 straps for
the reference and bandgap-core nets, and short 2 um taps from functional
supply terminals to the nearest rail. A supply tap longer than
BANDGAP_SUPPLY_TAP_UM is not drawn; that terminal keeps its lower-metal route.

Connections are obtained from the actual metal connectivity, not device names
in the schematic. Independent foundry DRC and LVS must run after this stage.
"""

from collections import defaultdict
import heapq
import json
import math
import os

import klayout.db as k
import numpy as np
from scipy.ndimage import distance_transform_cdt, label

from layout_paths import BUILD_DIR as ROOT
from matching_constraints import ROUTE_WIDTH, ROUTE_HEIGHT, routing_exclusions
from power_plan import supply_plan

PITCH = 0.8
NX, NY = round(ROUTE_WIDTH / PITCH), round(ROUTE_HEIGHT / PITCH)
METAL_STACK = (34, 35, 36, 38, 42, 40, 46, 41, 81)
LAYER_NUMBER = {"M4": 46, "M5": 81}
# Drawn clearances for planned metal, above the 0.46 um (top metal) and
# 0.30 um (wide metal4) rules.
PLAN_CLEARANCE_UM = {81: 0.6, 46: 0.4}
# MIMTM.1: routing metal4 stays 1.2 um from a capacitor bottom plate.
MIM_PLATE_CLEARANCE_UM = 1.2
# 2 x 2 via4 arrays, one per 2.4 um, 0.5 um inside both metals. Arrays stay
# 0.37 um from other cuts so they never form a V4.2b "4 x 4 or larger" group.
STITCH_PITCH = 2400
STITCH_MARGIN = 500
VIA_GROUP_CLEARANCE = 370
FUSE_KEEPOUT = 1500
SUPPLY_TAP_UM = float(os.environ.get("BANDGAP_SUPPLY_TAP_UM", "40"))

layout = k.Layout()
layout.read(str(ROOT / "routed_candidate.gds"))
top = layout.top_cell()
dbu = layout.dbu
placement = json.loads((ROOT / "placement.json").read_text())
routing = json.loads((ROOT / "routing_status.json").read_text())
aliases = routing["aliases"]
ports = {name: tuple(point) for name, point in routing["ports"].items()}


def canonical(name):
    while name in aliases:
        name = aliases[name]
    return name


def extract_metal():
    """Metal connectivity of the current layout. Capacitor via4 is no short."""
    extractor = k.LayoutToNetlist(k.RecursiveShapeIterator(layout, top, []))
    layers = {
        i: extractor.make_layer(layout.layer(i, 0), f"L{i}") for i in METAL_STACK
    }
    fuse_layer = extractor.make_layer(layout.layer(75, 0), "FuseTop")
    layers[41] = layers[41] - fuse_layer
    extractor.register(layers[41], "Via4OutsideCapacitor")
    for layer in layers.values():
        extractor.connect(layer)
    for a, b in zip(METAL_STACK, METAL_STACK[1:]):
        extractor.connect(layers[a], layers[b])
    extractor.extract_netlist()
    return extractor, layers


def drawn(number):
    return k.Region(top.begin_shapes_rec(layout.layer(number, 0)))


def nm(microns):
    return round(microns / dbu)


def rectangle(bounds):
    return k.Region(k.DBox(*bounds).to_itype(dbu))


def square(x, y, radius):
    return rectangle((x - radius, y - radius, x + radius, y + radius))


def put(layer, region):
    top.shapes(layout.layer(layer, 0)).insert(region)


def boxes(region):
    """Manhattan region as integer rectangles."""
    for polygon in region.each():
        for part in polygon.decompose_trapezoids():
            yield part.bbox()


def grid_mask(region, grow=0.0):
    """Routing-grid points lying inside the region, optionally grown."""
    mask = np.zeros((NY, NX), dtype=bool)
    for bounds in boxes(region):
        b = bounds.to_dtype(dbu)
        x1 = max(0, math.ceil((b.left - grow) / PITCH - 1e-9))
        x2 = min(NX - 1, math.floor((b.right + grow) / PITCH + 1e-9))
        y1 = max(0, math.ceil((b.bottom - grow) / PITCH - 1e-9))
        y2 = min(NY - 1, math.floor((b.top + grow) / PITCH + 1e-9))
        if x1 <= x2 and y1 <= y2:
            mask[y1 : y2 + 1, x1 : x2 + 1] = True
    return mask


extractor, layers = extract_metal()
fuse = drawn(75).merged()
fuse_keepout = fuse.sized(FUSE_KEEPOUT)

# Avoid placing noisy supply metal directly above sensitive amplifier nets.
# The guard distance is measured from the edge of the underlying signal metal.
supply_clearance = float(os.environ.get("BANDGAP_SUPPLY_CLEARANCE_UM", "3"))
sensitive_metal = k.Region()
if supply_clearance:
    sensitive_names = {"vgn2", "xf1", "xf2", "pm1", "pm2", "y1"}
    seen = set()
    for device in placement["placed"]:
        for name, point in zip(device["nodes"], device["pins"]):
            if name not in sensitive_names or name in seen:
                continue
            net = extractor.probe_net(layers[36], k.DPoint(*point))
            assert net is not None, name
            for number in [36, 42, 46, 81]:
                sensitive_metal += extractor.shapes_of_net(net, layers[number], True)
            seen.add(name)
    assert seen == sensitive_names
    sensitive_metal = sensitive_metal.merged()
# Centre-line keepout for a 2 um tap, and edge keepout for a planned rail.
sensitive = sensitive_metal.sized(nm(supply_clearance + 1))
sensitive_edge = sensitive_metal.sized(nm(supply_clearance))


# --------------------------------------------------------------------------
# Stage 1: planned supply rails
# --------------------------------------------------------------------------
plan = supply_plan(placement)
plan_nets = sorted({entry["net"] for entry in plan})
port_net = {name: extractor.probe_net(layers[36], k.DPoint(*ports[name])) for name in ports}
assert all(net is not None for net in port_net.values())
original = {
    name: {
        number: extractor.shapes_of_net(port_net[name], layers[number], True).merged()
        for number in (46, 81)
    }
    for name in plan_nets
}
all_metal = {number: drawn(number).merged() for number in (46, 81)}
planned = {name: {46: k.Region(), 81: k.Region()} for name in plan_nets}
for entry in plan:
    planned[entry["net"]][LAYER_NUMBER[entry["layer"]]] += rectangle(entry["box"])
capacitor_plates = all_metal[46].interacting(fuse)
for entry in plan:
    name, number = entry["net"], LAYER_NUMBER[entry["layer"]]
    region = rectangle(entry["box"])
    foreign = all_metal[number] - original[name][number]
    for other in plan_nets:
        if other != name:
            foreign += planned[other][number]
    clearance = nm(PLAN_CLEARANCE_UM[number])
    assert (region & foreign.sized(clearance)).is_empty(), (
        entry,
        "planned supply metal is too close to another net",
    )
    if number == 81:
        if name in {"avdd", "dvdd"}:
            assert (region & sensitive_edge).is_empty(), (
                entry,
                "supply rail inside the sensitive-net clearance",
            )
        for bounds in routing_exclusions(placement, name, aliases):
            assert (region & rectangle(bounds)).is_empty(), (
                entry,
                "unrelated rail over a matched array",
            )
    else:
        assert (
            region & capacitor_plates.sized(nm(MIM_PLATE_CLEARANCE_UM))
        ).is_empty(), (
            entry,
            "metal4 too close to a capacitor bottom plate",
        )

stitch_records = {}
for name in plan_nets:
    # Close same-net notches below the spacing rule; never toward other nets.
    metal4 = (original[name][46] + planned[name][46]).merged().sized(145).sized(-145)
    metal5 = (original[name][81] + planned[name][81]).merged().sized(235).sized(-235)
    for number, region in ((46, metal4), (81, metal5)):
        foreign = all_metal[number] - original[name][number]
        for other in plan_nets:
            if other != name:
                foreign += planned[other][number]
        # Notch closing may only add metal between shapes of this net.
        added = region - original[name][number] - planned[name][number]
        assert (added & foreign.sized(465 if number == 81 else 285)).is_empty(), (
            name,
            number,
            "notch closing reached another net",
        )
    if not planned[name][46].is_empty():
        put(46, metal4)
    put(81, metal5)

    # Via4 arrays wherever a planned rail lies over metal4 of the same net.
    seats = (planned[name][81].merged() & metal4) - fuse_keepout
    seats = seats.sized(-STITCH_MARGIN)
    occupied = defaultdict(list)
    footprints = k.Region()
    for bounds in boxes(seats):
        columns = list(
            range(
                math.ceil(bounds.left / STITCH_PITCH) * STITCH_PITCH,
                bounds.right + 1,
                STITCH_PITCH,
            )
        ) or [round((bounds.left + bounds.right) / 20) * 10]
        rows = list(
            range(
                math.ceil(bounds.bottom / STITCH_PITCH) * STITCH_PITCH,
                bounds.top + 1,
                STITCH_PITCH,
            )
        ) or [round((bounds.bottom + bounds.top) / 20) * 10]
        for x in columns:
            for y in rows:
                cell = (x // 1200, y // 1200)
                if any(
                    max(abs(x - px), abs(y - py)) < 1150
                    for cx in (cell[0] - 1, cell[0], cell[0] + 1)
                    for cy in (cell[1] - 1, cell[1], cell[1] + 1)
                    for px, py in occupied[(cx, cy)]
                ):
                    continue
                occupied[cell].append((x, y))
                footprints.insert(k.Box(x - 390, y - 390, x + 390, y + 390))
    nearby = drawn(41).interacting(seats.sized(2000))
    footprints = footprints.not_interacting(nearby.sized(VIA_GROUP_CLEARANCE))
    cuts = k.Region()
    for footprint in footprints.each():
        centre = footprint.bbox().center()
        for dx in (-260, 260):
            for dy in (-260, 260):
                cuts.insert(
                    k.Box(
                        centre.x + dx - 130,
                        centre.y + dy - 130,
                        centre.x + dx + 130,
                        centre.y + dy + 130,
                    )
                )
    put(41, cuts)
    stitch_records[name] = {
        "planned_m5_area_um2": planned[name][81].merged().area() * dbu**2,
        "planned_m4_area_um2": planned[name][46].merged().area() * dbu**2,
        "stitch_via4_count": cuts.count(),
    }
    print("Planned", name, stitch_records[name], flush=True)

# Re-extract with the rails in place. The supplies and the reference must
# still be separate nets, and every planned rectangle must be on its own net.
extractor, layers = extract_metal()
port_net = {name: extractor.probe_net(layers[36], k.DPoint(*ports[name])) for name in ports}
clusters = {name: net.cluster_id for name, net in port_net.items()}
assert len(set(clusters.values())) == len(clusters), (clusters, "ports are shorted")
for entry in plan:
    x1, y1, x2, y2 = entry["box"]
    number = LAYER_NUMBER[entry["layer"]]
    net = extractor.probe_net(layers[number], k.DPoint((x1 + x2) / 2, (y1 + y2) / 2))
    assert net is not None and net.cluster_id == clusters[entry["net"]], (
        entry,
        "planned rectangle is not connected to its supply",
    )


# --------------------------------------------------------------------------
# Stage 2: precision straps and short supply taps
# --------------------------------------------------------------------------
def segment(a, b, width=2):
    points = [k.DPoint(a[0] * PITCH, a[1] * PITCH), k.DPoint(b[0] * PITCH, b[1] * PITCH)]
    return k.Region(
        k.DPath(points, width, width / 2, width / 2).to_itype(dbu).polygon()
    )


def find_path(start, tree, blocked, limit=None):
    target = min(tree, key=lambda p: abs(p[0] - start[0]) + abs(p[1] - start[1]))
    queue = [(0, 0, start)]
    cost = {start: 0}
    parents = {}
    while queue:
        _, distance, current = heapq.heappop(queue)
        if distance != cost.get(current):
            continue
        if current in tree:
            path = [current]
            while current != start:
                current = parents[current]
                path.append(current)
            return path
        x, y = current
        for xx, yy in [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]:
            if not (2 <= xx < NX - 2 and 2 <= yy < NY - 2) or blocked[yy, xx]:
                continue
            point = (xx, yy)
            trial = distance + 1
            if limit is not None and trial > limit:
                continue
            if trial < cost.get(point, 1e12):
                cost[point] = trial
                parents[point] = current
                heuristic = abs(xx - target[0]) + abs(yy - target[1])
                heapq.heappush(queue, (trial + heuristic, trial, point))
    return None


summary = []
networks = [("dvdd", ports["dvdd"]), ("vref", ports["vref"])]
for name in ["v1", "v2", "vq3", *[f"rbit{i}top" for i in range(7)]]:
    candidates = [
        (d, point)
        for d in placement["placed"]
        for node, point in zip(d["nodes"], d["pins"])
        if canonical(node) == name
    ]
    device, port = min(
        candidates, key=lambda item: ("fet" in item[0]["model"], item[0]["name"])
    )
    networks.append((name, tuple(port)))
networks.append(("avdd", ports["avdd"]))
if os.environ.get("BANDGAP_GROUND_STRAPS", "1") == "1":
    # Separate analog/digital returns remain separate physical nets.
    networks[-1:-1] = [(name, ports[name]) for name in ("dvss", "avss")]
# Capacitor via4 is checked through the fuse keepout below, not cut by cut.
existing_via4 = drawn(41).not_inside(fuse)
for name, port in networks:
    has_rails = name in stitch_records and name != "vref"
    net = extractor.probe_net(layers[36], k.DPoint(*port))
    own = extractor.shapes_of_net(net, layers[46], True)
    drawn4 = drawn(46).merged()
    foreign4 = (drawn4 - own).sized(285)
    drawn5 = drawn(81).merged()
    own5 = extractor.shapes_of_net(net, layers[81], True)
    # Top metal that was already on a terminal before any rail was drawn.
    terminal5 = original[name][81] if name in original else own5
    # GF180-D top metal needs 0.46 um spacing, unlike the lower metals.
    obstacle5 = (drawn5 - own5).sized(1465)
    if name in {"avdd", "dvdd"}:
        obstacle5 += sensitive
    for bounds in routing_exclusions(placement, name, aliases):
        obstacle5 += rectangle(bounds).sized(1465)
    blocked = grid_mask(obstacle5)
    own4_near = grid_mask(own, 0.05)
    landings = []
    direct_metal5 = set()
    for device in [{"nodes": [name], "pins": [port]}] + placement["placed"]:
        if has_rails and device.get("dummy"):
            # Dummy units are tied by the rails above them and by their
            # existing lower-metal straps; they get no individual tap.
            continue
        for signal, (x, y) in zip(device["nodes"], device["pins"]):
            if canonical(signal) != name:
                continue
            if any(abs(x - px * 0.8) + abs(y - py * 0.8) < 8 for px, py in landings):
                continue
            search_radius = 8 if name in {"avdd", "dvdd"} else 20
            reach = (search_radius + 2) * PITCH
            local_via4 = existing_via4 & square(x, y, reach)
            options = sorted(
                (abs(xx * 0.8 - x) + abs(yy * 0.8 - y), xx, yy)
                for xx in range(
                    round(x / 0.8) - search_radius, round(x / 0.8) + search_radius + 1
                )
                for yy in range(
                    round(y / 0.8) - search_radius, round(y / 0.8) + search_radius + 1
                )
            )
            for _, xx, yy in options:
                if not (2 <= xx < NX - 2 and 2 <= yy < NY - 2) or blocked[yy, xx]:
                    continue
                if not (terminal5 & square(xx * 0.8, yy * 0.8, 0.05)).is_empty():
                    landings.append((xx, yy))
                    direct_metal5.add((xx, yy))
                    break
                # Most candidates miss the sparse own-net route. Test it first.
                if not own4_near[yy, xx]:
                    continue
                if (own & square(xx * 0.8, yy * 0.8, 0.05)).is_empty():
                    continue
                if not (square(xx * 0.8, yy * 0.8, 0.6) & foreign4).is_empty():
                    continue
                if not (square(xx * 0.8, yy * 0.8, 0.39) & fuse_keepout).is_empty():
                    continue
                # Keep the full 2x2 array clear of existing via4 cuts.
                if not (square(xx * 0.8, yy * 0.8, 0.76) & local_via4).is_empty():
                    continue
                landings.append((xx, yy))
                # Reserve cuts immediately: two pin searches on the same net
                # can otherwise choose adjacent landing arrays before either
                # is inserted into the GDS.
                for dx in [-0.26, 0.26]:
                    for dy in [-0.26, 0.26]:
                        existing_via4 += square(xx * 0.8 + dx, yy * 0.8 + dy, 0.13)
                break
    assert landings, name
    # An unreachable island cannot connect to the root through this M5 mask.
    # Reject it directly instead of repeating an exhaustive A* search per pin.
    free = ~blocked
    free[:2, :] = free[-2:, :] = False
    free[:, :2] = free[:, -2:] = False
    components, _ = label(free)
    metal = k.Region()
    if has_rails:
        # A tap may end anywhere a 2 um wire fits inside a rail of this net.
        on_rail = grid_mask(own5.merged().sized(-1000)) & free
        tree = {(int(x), int(y)) for y, x in np.argwhere(on_rail)}
        assert tree, name
        to_rail = distance_transform_cdt(~on_rail, metric="taxicab")
        pending = sorted(landings, key=lambda p: (int(to_rail[p[1], p[0]]), p))
        connected_landings = []
        limit = round(SUPPLY_TAP_UM / PITCH)
    else:
        tree = {landings[0]}
        pending = set(landings[1:])
        connected_landings = [landings[0]]
        limit = None
    reachable = {int(components[y, x]) for x, y in tree} - {0}
    skipped_landings = []
    tap_lengths = []
    while pending:
        if has_rails:
            start = pending.pop(0)
        else:
            start = min(
                pending,
                key=lambda a: min(abs(a[0] - b[0]) + abs(a[1] - b[1]) for b in tree),
            )
            pending.remove(start)
        path = (
            find_path(start, tree, blocked, limit)
            if int(components[start[1], start[0]]) in reachable
            else None
        )
        if path is None:
            # The complete lower-metal network already connects this
            # terminal. Omit only its optional M5 reinforcement when it is
            # enclosed by a clearance corridor or too far from a rail.
            skipped_landings.append(start)
            continue
        for a, b in zip(path, path[1:]):
            metal += segment(a, b)
        tree.update(path)
        connected_landings.append(start)
        tap_lengths.append((len(path) - 1) * PITCH)
    port_landing = landings[0]
    landings = connected_landings
    landing_metal4 = own.dup()
    added_vias = 0
    for x, y in landings:
        direct = (x, y) in direct_metal5
        x, y = x * 0.8, y * 0.8
        metal += square(x, y, 1)
        if direct:
            continue
        landing_metal4 += square(x, y, 0.6)
        for dx in [-0.26, 0.26]:
            for dy in [-0.26, 0.26]:
                put(41, square(x + dx, y + dy, 0.13))
                added_vias += 1
    put(46, landing_metal4.merged().sized(145).sized(-145))
    metal = (metal + own5).merged().sized(235).sized(-235)
    put(81, metal)
    pin_x, pin_y = port_landing
    if name in {"avdd", "avss", "dvdd", "vref"}:
        assert port_landing in landings, (name, "port landing is not strapped")
        assert abs(pin_x * 0.8 - port[0]) + abs(pin_y * 0.8 - port[1]) < 7, name
    if name in routing["ports"]:
        top.shapes(layout.layer(81, 10)).insert(
            k.DText(name, k.DTrans(pin_x * 0.8, pin_y * 0.8))
        )
    record = {
        "net": name,
        "m5_width_um": 2,
        "landing_count": len(landings),
        "via4_count": added_vias,
        "direct_metal5_landings": sum(point in direct_metal5 for point in landings),
        "added_area_um2": (metal - own5).area() * dbu**2,
        "signal_clearance_um": supply_clearance if name in {"avdd", "dvdd"} else 0,
        "unreinforced_landings": len(skipped_landings),
    }
    if has_rails:
        record["longest_tap_um"] = max(tap_lengths, default=0.0)
        record["tap_limit_um"] = SUPPLY_TAP_UM
    if name in stitch_records:
        record.update(stitch_records[name])
    summary.append(record)
    print(summary[-1], flush=True)

# Final guard before the foundry decks: supplies and reference still separate.
extractor, layers = extract_metal()
final = {
    name: extractor.probe_net(layers[36], k.DPoint(*ports[name])).cluster_id
    for name in ports
}
assert len(set(final.values())) == len(final), (final, "ports are shorted")
layout.write(str(ROOT / "power_candidate.gds"))
(ROOT / "power_summary.json").write_text(json.dumps(summary, indent=2) + "\n")
(ROOT / "power_plan.json").write_text(json.dumps(plan, indent=2) + "\n")

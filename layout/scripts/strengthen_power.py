"""Add 2 um M5 supply and precision-current straps with redundant via arrays.

Connections are obtained from the actual metal connectivity, not device names
in the schematic. Independent foundry DRC and LVS must run after this stage.
"""

from collections import defaultdict
from pathlib import Path
import heapq
import json
import math
import os

import klayout.db as k
import numpy as np
from scipy.ndimage import label

from layout_paths import BUILD_DIR as ROOT
from matching_constraints import ROUTE_WIDTH, ROUTE_HEIGHT, routing_exclusions

NX, NY = round(ROUTE_WIDTH / 0.8), round(ROUTE_HEIGHT / 0.8)

layout = k.Layout()
layout.read(str(ROOT / "routed_candidate.gds"))
top = layout.top_cell()
extractor = k.LayoutToNetlist(k.RecursiveShapeIterator(layout, top, []))
layers = {
    i: extractor.make_layer(layout.layer(i, 0), f"L{i}")
    for i in (34, 35, 36, 38, 42, 40, 46, 41, 81)
}
fuse = extractor.make_layer(layout.layer(75, 0), "FuseTop")
layers[41] = layers[41] - fuse
extractor.register(layers[41], "Via4OutsideCapacitor")
for layer in layers.values():
    extractor.connect(layer)
for a, b in [
    (34, 35),
    (35, 36),
    (36, 38),
    (38, 42),
    (42, 40),
    (40, 46),
    (46, 41),
    (41, 81),
]:
    extractor.connect(layers[a], layers[b])
extractor.extract_netlist()
placement = json.loads((ROOT / "placement.json").read_text())
routing = json.loads((ROOT / "routing_status.json").read_text())
aliases = routing["aliases"]


def canonical(name):
    while name in aliases:
        name = aliases[name]
    return name


# Avoid placing noisy supply straps directly above sensitive amplifier nets.
# The guard distance is measured from the edge of the underlying signal metal.
supply_clearance = float(os.environ.get("BANDGAP_SUPPLY_CLEARANCE_UM", "3"))
sensitive = k.Region()
if supply_clearance:
    sensitive_names = {"vgn2", "xf1", "xf2", "pm1", "pm2", "y1"}
    seen = set()
    for device in placement["placed"]:
        for name, point in zip(device["nodes"], device["pins"]):
            if name not in sensitive_names or name in seen:
                continue
            net = extractor.probe_net(layers[36], k.DPoint(*point))
            assert net is not None, name
            for number in [36, 42, 46]:
                sensitive += extractor.shapes_of_net(net, layers[number], True)
            seen.add(name)
    assert seen == sensitive_names
    sensitive = sensitive.merged().sized(round((supply_clearance + 1) / layout.dbu))


def square(x, y, radius):
    return k.Region(
        k.DBox(x - radius, y - radius, x + radius, y + radius).to_itype(layout.dbu)
    )


def put(layer, region):
    top.shapes(layout.layer(layer, 0)).insert(region)


def segment(a, b, width=2):
    points = [k.DPoint(a[0] * 0.8, a[1] * 0.8), k.DPoint(b[0] * 0.8, b[1] * 0.8)]
    return k.Region(
        k.DPath(points, width, width / 2, width / 2).to_itype(layout.dbu).polygon()
    )


def find_path(start, tree, blocked):
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
            if trial < cost.get(point, 1e12):
                cost[point] = trial
                parents[point] = current
                heuristic = abs(xx - target[0]) + abs(yy - target[1])
                heapq.heappush(queue, (trial + heuristic, trial, point))
    return None


summary = []
networks = [("dvdd", (1.6, 302.4)), ("vref", tuple(routing["ports"]["vref"]))]
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
networks.extend(
    [
        ("avdd", (210.4, 309.6)),
    ]
)
if os.environ.get("BANDGAP_GROUND_STRAPS", "1") == "1":
    # Separate analog/digital returns remain separate physical nets. Optional
    # top-metal reinforcement uses the same clearance and connectivity checks.
    networks[-1:-1] = [
        (name, tuple(routing["ports"][name])) for name in ("dvss", "avss")
    ]
for name, port in networks:
    net = extractor.probe_net(layers[36], k.DPoint(*port))
    own = extractor.shapes_of_net(net, layers[46], True)
    drawn4 = k.Region(top.begin_shapes_rec(layout.layer(46, 0))).merged()
    foreign4 = (drawn4 - own).sized(285)
    drawn5 = k.Region(top.begin_shapes_rec(layout.layer(81, 0))).merged()
    own5 = extractor.shapes_of_net(net, layers[81], True)
    # GF180-D top metal needs 0.46 um spacing, unlike the lower metals.
    obstacle5 = (drawn5 - own5).sized(1465)
    if name in {"avdd", "dvdd"}:
        obstacle5 += sensitive
    for bounds in routing_exclusions(placement, name, aliases):
        obstacle5 += k.Region(k.DBox(*bounds).to_itype(layout.dbu)).sized(1465)
    blocked = np.zeros((NY, NX), dtype=bool)
    for poly in obstacle5.each():
        for part in poly.decompose_trapezoids():
            b = part.bbox().to_dtype(layout.dbu)
            x1, x2 = max(0, math.ceil(b.left / 0.8)), min(
                NX - 1, math.floor(b.right / 0.8)
            )
            y1, y2 = max(0, math.ceil(b.bottom / 0.8)), min(
                NY - 1, math.floor(b.top / 0.8)
            )
            blocked[y1 : y2 + 1, x1 : x2 + 1] = True
    landings = []
    direct_metal5 = set()
    existing_via4 = k.Region(top.begin_shapes_rec(layout.layer(41, 0)))
    for device in [{"nodes": [name], "pins": [port]}] + placement["placed"]:
        for signal, (x, y) in zip(device["nodes"], device["pins"]):
            if canonical(signal) != name:
                continue
            if any(abs(x - px * 0.8) + abs(y - py * 0.8) < 8 for px, py in landings):
                continue
            search_radius = 8 if name in {"avdd", "dvdd"} else 20
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
                if not (own5 & square(xx * 0.8, yy * 0.8, 0.05)).is_empty():
                    landings.append((xx, yy))
                    direct_metal5.add((xx, yy))
                    break
                # Most candidates miss the sparse own-net route. Test it first.
                if (own & square(xx * 0.8, yy * 0.8, 0.05)).is_empty():
                    continue
                if not (square(xx * 0.8, yy * 0.8, 0.6) & foreign4).is_empty():
                    continue
                # Keep the full 2x2 array clear of existing via4 cuts.
                if not (square(xx * 0.8, yy * 0.8, 0.655) & existing_via4).is_empty():
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
    root_component = components[landings[0][1], landings[0][0]]
    metal = k.Region()
    tree = {landings[0]}
    pending = set(landings[1:])
    connected_landings = [landings[0]]
    skipped_landings = []
    while pending:
        start = min(
            pending,
            key=lambda a: min(abs(a[0] - b[0]) + abs(a[1] - b[1]) for b in tree),
        )
        pending.remove(start)
        path = (
            find_path(start, tree, blocked)
            if root_component and components[start[1], start[0]] == root_component
            else None
        )
        if path is None:
            # The complete lower-metal supply network already connects this
            # terminal. Omit only its optional M5 reinforcement when a signal
            # clearance corridor encloses it; never cross that corridor.
            skipped_landings.append(start)
            continue
        for a, b in zip(path, path[1:]):
            metal += segment(a, b)
        tree.update(path)
        connected_landings.append(start)
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
    pin_x, pin_y = landings[0]
    if name in {"avdd", "dvdd", "vref"}:
        assert abs(pin_x * 0.8 - port[0]) + abs(pin_y * 0.8 - port[1]) < 7, name
    if name in routing["ports"]:
        top.shapes(layout.layer(81, 10)).insert(
            k.DText(name, k.DTrans(pin_x * 0.8, pin_y * 0.8))
        )
    summary.append(
        {
            "net": name,
            "m5_width_um": 2,
            "landing_count": len(landings),
            "via4_count": added_vias,
            "direct_metal5_landings": sum(point in direct_metal5 for point in landings),
            "added_area_um2": (metal - own5).area() * layout.dbu**2,
            "signal_clearance_um": supply_clearance if name in {"avdd", "dvdd"} else 0,
            "unreinforced_landings": len(skipped_landings),
        }
    )
    print(summary[-1], flush=True)
layout.write(str(ROOT / "power_candidate.gds"))
(ROOT / "power_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

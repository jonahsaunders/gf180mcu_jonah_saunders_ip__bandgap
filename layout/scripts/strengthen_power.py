"""Add 2 um M5 supply straps with redundant via arrays where clearance allows.

Connections are obtained from the actual metal connectivity, not device names
in the schematic. Independent foundry DRC and LVS must run after this stage.
"""

from collections import defaultdict
from pathlib import Path
import heapq
import json
import math

import klayout.db as k
import numpy as np

from layout_paths import BUILD_DIR as ROOT

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
            if not (2 <= xx < 773 and 2 <= yy < 392) or blocked[yy, xx]:
                continue
            point = (xx, yy)
            trial = distance + 1
            if trial < cost.get(point, 1e12):
                cost[point] = trial
                parents[point] = current
                heuristic = abs(xx - target[0]) + abs(yy - target[1])
                heapq.heappush(queue, (trial + heuristic, trial, point))
    raise RuntimeError(f"Cannot connect supply landing {start}")


summary = []
for name, port in [("avdd", (210.4, 309.6)), ("dvdd", (1.6, 302.4))]:
    net = extractor.probe_net(layers[36], k.DPoint(*port))
    own = extractor.shapes_of_net(net, layers[46], True)
    drawn4 = k.Region(top.begin_shapes_rec(layout.layer(46, 0))).merged()
    foreign4 = (drawn4 - own).sized(285)
    drawn5 = k.Region(top.begin_shapes_rec(layout.layer(81, 0))).merged()
    obstacle5 = drawn5.sized(1285)
    blocked = np.zeros((394, 775), dtype=bool)
    for poly in obstacle5.each():
        for part in poly.decompose_trapezoids():
            b = part.bbox().to_dtype(layout.dbu)
            x1, x2 = max(0, math.ceil(b.left / 0.8)), min(
                774, math.floor(b.right / 0.8)
            )
            y1, y2 = max(0, math.ceil(b.bottom / 0.8)), min(
                393, math.floor(b.top / 0.8)
            )
            blocked[y1 : y2 + 1, x1 : x2 + 1] = True
    landings = []
    for device in [{"nodes": [name], "pins": [port]}] + placement["placed"]:
        for signal, (x, y) in zip(device["nodes"], device["pins"]):
            if signal != name:
                continue
            if any(abs(x - px * 0.8) + abs(y - py * 0.8) < 8 for px, py in landings):
                continue
            options = []
            for dx in range(-8, 9):
                for dy in range(-8, 9):
                    xx, yy = round(x / 0.8) + dx, round(y / 0.8) + dy
                    if not (2 <= xx < 773 and 2 <= yy < 392) or blocked[yy, xx]:
                        continue
                    q = square(xx * 0.8, yy * 0.8, 0.6)
                    if not (q & foreign4).is_empty():
                        continue
                    # The existing M4 route must traverse the landing center.
                    if (own & square(xx * 0.8, yy * 0.8, 0.05)).is_empty():
                        continue
                    options.append((abs(xx * 0.8 - x) + abs(yy * 0.8 - y), xx, yy))
            if options:
                _, xx, yy = min(options)
                landings.append((xx, yy))
    assert landings, name
    metal = k.Region()
    tree = {landings[0]}
    pending = set(landings[1:])
    while pending:
        start = min(
            pending,
            key=lambda a: min(abs(a[0] - b[0]) + abs(a[1] - b[1]) for b in tree),
        )
        pending.remove(start)
        path = find_path(start, tree, blocked)
        for a, b in zip(path, path[1:]):
            metal += segment(a, b)
        tree.update(path)
    for x, y in landings:
        x, y = x * 0.8, y * 0.8
        put(46, square(x, y, 0.6))
        metal += square(x, y, 1)
        for dx in [-0.26, 0.26]:
            for dy in [-0.26, 0.26]:
                put(41, square(x + dx, y + dy, 0.13))
    metal = metal.merged().sized(140).sized(-140)
    put(81, metal)
    pin_x, pin_y = landings[0]
    assert abs(pin_x * 0.8 - port[0]) + abs(pin_y * 0.8 - port[1]) < 7, name
    top.shapes(layout.layer(81, 10)).insert(
        k.DText(name, k.DTrans(pin_x * 0.8, pin_y * 0.8))
    )
    summary.append(
        {
            "net": name,
            "m5_width_um": 2,
            "landing_count": len(landings),
            "via4_count": 4 * len(landings),
            "area_um2": metal.area() * layout.dbu**2,
        }
    )
    print(summary[-1], flush=True)
layout.write(str(ROOT / "power_candidate.gds"))
(ROOT / "power_summary.json").write_text(json.dumps(summary, indent=2) + "\n")

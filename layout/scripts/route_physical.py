"""Route the experimental placement with explicit physical keepouts.

The foundry DRC/LVS decks, not this router, determine verification status.
All geometry is kept in micrometres at the interface and integer nanometres
inside KLayout. This file does not change any verification rules.
"""

from collections import defaultdict
from pathlib import Path
import heapq
import json
import math

import klayout.db as k
import numpy as np

from layout_paths import BUILD_DIR as ROOT
from matching_constraints import ROUTE_WIDTH, ROUTE_HEIGHT, routing_exclusions
from matched_routes import core_drain_routes, core_supply_routes

data = json.loads((ROOT / "placement.json").read_text())
manifest = json.loads((ROOT / "primitive_manifest.json").read_text())
devices = {d["name"]: d for d in manifest["devices"]}
assert not data["unplaced"]
layout = k.Layout()
layout.read(str(ROOT / "placement_candidate.gds"))
top = layout.cell("Bandgap_Core_Res_placement")
top.name = "Bandgap_Core_Res"
top.flatten(True)

PITCH = 0.8
NX, NY = round(ROUTE_WIDTH / PITCH), round(ROUTE_HEIGHT / PITCH)
METALS = [(36, 0), (42, 0), (46, 0)]
VIAS = [(38, 0), (40, 0)]
aliases = {}


def root(name):
    while name in aliases:
        name = aliases[name]
    return name


for probe in manifest["probes"]:
    first, second = map(root, probe["nodes"])
    aliases[first] = second


def rect(bounds):
    return k.Region(k.DBox(*bounds).to_itype(layout.dbu))


def pad(point, radius=0.2):
    x, y = point
    return rect((x - radius, y - radius, x + radius, y + radius))


def insert(layer, region):
    top.shapes(layout.layer(*layer)).insert(region)


def wire(first, second):
    if first == second:
        return pad(first)
    path = k.DPath([k.DPoint(*first), k.DPoint(*second)], 0.4, 0.2, 0.2)
    return k.Region(path.to_itype(layout.dbu).polygon())


terminals = defaultdict(list)
geometry = defaultdict(lambda: [k.Region() for _ in METALS])
via_geometry = [k.Region(), k.Region()]
existing_grid_vias = {}
channels = (
    k.Region(top.begin_shapes_rec(layout.layer(22, 0)))
    & k.Region(top.begin_shapes_rec(layout.layer(30, 0)))
).merged()
for d in data["placed"]:
    for pin_index, (name, point) in enumerate(zip(d["nodes"], d["pins"])):
        name = root(name)
        if not d.get("dummy") or pin_index == 3:
            terminals[name].append(tuple(point))
        geometry[name][0] += pad(point)
        if d["model"].startswith("cap_"):
            for layer in (1, 2):
                geometry[name][layer] += pad(point, 0.6 if layer == 2 else 0.2)
            for layer in (0, 1):
                via_geometry[layer] += pad(point, 0.13)
    if d.get("dummy"):
        # Regular quiet-rail straps over the dummy channels, repeated at every
        # perimeter site. These are connected conductors, not floating fill.
        name = root(d["nodes"][3])
        body, gate = tuple(d["pins"][3]), tuple(d["pins"][1])
        channel = channels & rect(d["bounds"])
        assert channel.count() == 1, d["name"]
        strap = (channel + wire(body, gate)).merged()
        upper_coverage = d["source"] != "DUM_M1"
        top_coverage = d["source"] in {"DUM_PREF", "DUM_MPSINK", "DUM_MPFOLD"}
        for level in ((0, 1, 2) if upper_coverage else (0, 1)):
            geometry[name][level] += strap
            insert(METALS[level], strap)
        # All four dummy terminals are the same rail. A solid local M1 tie
        # avoids narrow notches between the gate/drain/source landing shapes.
        left, bottom, right, upper = d["bounds"]
        insert((34, 0), rect((left + 0.3, bottom + 0.3, right - 0.3, upper - 0.3)))
        if top_coverage:
            insert((81, 0), (channel + wire(body, gate).sized(100)).merged())
        for level in ((0, 1) if upper_coverage else (0,)):
            via_geometry[level] += pad(body, 0.13)
            insert(VIAS[level], pad(body, 0.13))
        if top_coverage:
            insert((41, 0), pad(body, 0.13))
            insert((81, 0), pad(body, 0.3))
        for layer in ([(42, 0), (46, 0)] if upper_coverage else [(42, 0)]):
            insert(layer, pad(body, 0.6))
        geometry[name][1] += pad(body, 0.6)
        if upper_coverage:
            geometry[name][2] += pad(body, 0.6)

# Grounded comb shields beneath the capacitors keep substrate pickup away
# from their quiet bottom plates. The comb fingers remain below 30 um wide.
# All M3/M4 transitions stay at the original access point outside FuseTop.
m4_original = k.Region(top.begin_shapes_rec(layout.layer(46, 0)))
for d in data["placed"]:
    if not d["model"].startswith("cap_"):
        continue
    candidates = list(m4_original.interacting(rect(d["bounds"])).each())
    plate = max(candidates, key=lambda p: p.area()).bbox().to_dtype(layout.dbu)
    left, bottom, right, upper = (
        plate.left + 0.5,
        plate.bottom + 0.5,
        plate.right - 0.5,
        plate.top - 0.5,
    )
    shield = rect((left, bottom, right, bottom + 1.5))
    x = left
    while x < right:
        edge = min(x + 18, right)
        if edge - x >= 0.4:
            shield += rect((x, bottom, edge, upper))
        x += 20
    bx, by = d["pins"][1]
    shield += rect((left, by - 0.6, bx + 0.2, by + 0.6))
    insert((34, 0), shield)
    for level in (0, 1):
        insert(METALS[level], shield)
        geometry["avss"][level] += shield
    insert((35, 0), pad((bx, by), 0.13))

# The digital nFET wells lie in a common deep well. Give the P+ taps enough
# LVPWELL overlap to satisfy the low-overlap implant rule.
insert((12, 0), rect((5, 162, 61, 306)))
for d in data["placed"]:
    name = d["source"]
    if (
        name.startswith("M")
        and name[1:].isdigit()
        and int(name[1:]) >= 24
        and d["model"].startswith("nfet")
    ):
        x1, y1, x2, y2 = d["bounds"]
        insert((204, 0), rect((x1 - 0.7, y1 - 0.7, x2 + 0.7, y2 + 0.7)))
pw_layer = layout.layer(204, 0)
pwell = k.Region(top.begin_shapes_rec(pw_layer)).sized(440).sized(-440)
top.shapes(pw_layer).clear()
top.shapes(pw_layer).insert(pwell)

# A contacted substrate ring encloses the DNW without crossing its edge.
outer = rect((1.9, 158.9, 64.1, 309.1))
inner = rect((2.5, 159.5, 63.5, 308.5))
ring = outer - inner
insert((22, 0), ring)
insert((31, 0), ring.sized(160))
insert((34, 0), ring)
for x in np.arange(2.2, 63.81, 0.6):
    for y in (159.2, 308.8):
        insert((33, 0), pad((x, y), 0.11))
for y in np.arange(159.8, 308.21, 0.6):
    for x in (2.2, 63.8):
        insert((33, 0), pad((x, y), 0.11))
ring_pin = (63.8, 159.2)
insert((35, 0), pad(ring_pin, 0.13))
insert((36, 0), pad(ring_pin))
geometry["avss"][0] += pad(ring_pin)
terminals["avss"].append(ring_pin)

ports = {
    "avdd": (210.4, 309.6),
    "avss": (210.4, 3.2),
    "dvdd": (1.6, 302.4),
    "dvss": (1.6, 163.2),
    "vref": (
        math.ceil((max(d["bounds"][2] for d in data["placed"]) + 4) / PITCH) * PITCH,
        350.4,
    ),
}
ports.update({f"b{i}": (1.6, 180 + 3.2 * i) for i in range(7)})
for name, point in ports.items():
    terminals[name].append(point)
    geometry[name][0] += pad(point)
    insert(METALS[0], pad(point))
    top.shapes(layout.layer(36, 10)).insert(
        k.DText(name, k.DTrans(float(point[0]), float(point[1])))
    )

fixed = [k.Region() for _ in METALS]
# Reserve actual bottom plates; stack access sits outside the plates.
fixed[2] = k.Region(top.begin_shapes_rec(layout.layer(46, 0)))
for name in geometry:
    fixed[2] -= geometry[name][2]
fixed[2] = fixed[2].sized(920)

# Place the verified balanced local drain network before routing other nets.
local_records = []
local_exclusions = {}
for plan in core_drain_routes(data, layout.dbu, PITCH):
    name = root(plan["net"])
    local_exclusions[name] = rect(plan["reserved_bounds"])
    for level, region in enumerate(plan["metal"]):
        foreign = fixed[level].dup()
        for other, shapes in geometry.items():
            if other != name:
                foreign += shapes[level]
        assert (region & foreign.sized(285)).is_empty(), (
            name,
            level,
            "local drain clearance",
        )
        geometry[name][level] += region
        insert(METALS[level], region)
    for level, cuts in enumerate(plan["vias"]):
        assert (cuts & via_geometry[level].sized(265)).is_empty(), (
            name,
            level,
            "local via clearance",
        )
        via_geometry[level] += cuts
        insert(VIAS[level], cuts)
    unit_drains = set(plan["unit_drains"])
    terminals[name] = [p for p in terminals[name] if p not in unit_drains] + [
        plan["anchor"]
    ]
    local_records.append(
        {
            "source": plan["source"],
            "net": name,
            "anchor_um": plan["anchor"],
            "reserved_bounds_um": plan["reserved_bounds"],
            "wire_width_um": 0.4,
            "M2_M3_M4_length_um": plan["lengths_um"],
            "M2_M3_M4_area_um2": [r.area() * layout.dbu**2 for r in plan["metal"]],
            "V2_V3_counts": [r.count() for r in plan["vias"]],
        }
    )
(ROOT / "matched_local_routes.json").write_text(
    json.dumps(local_records, indent=2) + "\n"
)

supply_plan = core_supply_routes(data, layout.dbu, PITCH)
for level, region in enumerate(supply_plan["metal"]):
    foreign = fixed[level].dup()
    for other, shapes in geometry.items():
        if other != "avdd":
            foreign += shapes[level]
    assert (region & foreign.sized(285)).is_empty(), (level, "source bus clearance")
    geometry["avdd"][level] += region
    insert(METALS[level], region)
for level, cuts in enumerate(supply_plan["vias"]):
    assert (cuts & via_geometry[level].sized(265)).is_empty(), (
        level,
        "source bus vias",
    )
    via_geometry[level] += cuts
    insert(VIAS[level], cuts)
source_points = set(supply_plan["unit_sources"])
terminals["avdd"] = [p for p in terminals["avdd"] if p not in source_points] + [
    supply_plan["anchor"]
]

names = sorted(terminals)
grid_terms = defaultdict(list)
access_failures = []


def foreign_regions(name):
    result = [region.dup() for region in fixed]
    if name in local_exclusions:
        for level in range(len(result)):
            result[level] += local_exclusions[name]
    for bounds in routing_exclusions(data, name, aliases):
        for level in range(len(result)):
            result[level] += rect(bounds)
    for other, layers in geometry.items():
        if other != name:
            for level, region in enumerate(layers):
                result[level] += region
    return [region.merged().sized(285) for region in result]


def acceptable(candidate, obstacle):
    return (candidate & obstacle).is_empty()


# Route short terminal escapes first. Candidate routes are checked against
# existing physical rectangles, including other pins, before insertion.
for name in sorted(names, key=lambda n: n in ("avdd", "avss", "dvdd", "dvss")):
    obstacles = foreign_regions(name)
    for point in dict.fromkeys(terminals[name]):
        x, y = point
        gx, gy = round(x / PITCH), round(y / PITCH)
        choices = []
        for dy in range(-3, 4):
            for dx in range(-3, 4):
                xx, yy = gx + dx, gy + dy
                if 1 <= xx < NX - 1 and 1 <= yy < NY - 1:
                    px, py = xx * PITCH, yy * PITCH
                    choices.append((abs(x - px) + abs(y - py), xx, yy))
        found = False
        for _, xx, yy in sorted(choices):
            target = (xx * PITCH, yy * PITCH)
            for level in (0, 1):
                # Existing capacitor stacks already connect M2 and M3.
                existing_via = not (pad(point, 0.001) & via_geometry[0]).is_empty()
                if level == 1 and not existing_via:
                    cut = pad(point, 0.13)
                    if not acceptable(cut, via_geometry[0].sized(265)):
                        continue
                    if not acceptable(pad(point), obstacles[1]):
                        continue
                for bend in ((target[0], y), (x, target[1])):
                    candidate = (wire(point, bend) + wire(bend, target)).merged()
                    if not acceptable(candidate, obstacles[level]):
                        continue
                    if level == 0:
                        if not acceptable(pad(target), obstacles[1]):
                            continue
                        if not acceptable(
                            pad(target, 0.13), via_geometry[0].sized(265)
                        ):
                            continue
                    geometry[name][level] += candidate
                    insert(METALS[level], candidate)
                    if level == 1 and not existing_via:
                        via_geometry[0] += pad(point, 0.13)
                        insert(VIAS[0], pad(point, 0.13))
                        insert(METALS[1], pad(point))
                    if level == 0:
                        via_geometry[0] += pad(target, 0.13)
                        geometry[name][1] += pad(target)
                        insert(VIAS[0], pad(target, 0.13))
                        insert(METALS[1], pad(target))
                        existing_grid_vias[(0, yy, xx)] = name
                    route_level = 1
                    if acceptable(pad(target), obstacles[2]) and acceptable(
                        pad(target, 0.13), via_geometry[1].sized(265)
                    ):
                        via_geometry[1] += pad(target, 0.13)
                        geometry[name][2] += pad(target)
                        insert(VIAS[1], pad(target, 0.13))
                        insert(METALS[2], pad(target))
                        existing_grid_vias[(1, yy, xx)] = name
                        route_level = 2
                    grid_terms[name].append((route_level, yy, xx))
                    found = True
                    break
                if found:
                    break
            if found:
                break
        if not found:
            access_failures.append((name, point))
    print("Access", name, "failures", len(access_failures), flush=True)

assert not access_failures, access_failures


def raster(region, amount=485):
    """Conservative rectangle rasterization including wire half-width and gap."""
    mask = np.zeros((NY, NX), dtype=bool)
    for polygon in region.merged().each():
        # All initial geometry is Manhattan. Box decomposition avoids reserving
        # the empty interior of a long L-shaped wire's bounding rectangle.
        for part in polygon.decompose_trapezoids():
            b = part.bbox().enlarged(amount).to_dtype(layout.dbu)
            x1, x2 = max(0, math.ceil(b.left / PITCH)), min(
                NX - 1, math.floor(b.right / PITCH)
            )
            y1, y2 = max(0, math.ceil(b.bottom / PITCH)), min(
                NY - 1, math.floor(b.top / PITCH)
            )
            if x1 <= x2 and y1 <= y2:
                mask[y1 : y2 + 1, x1 : x2 + 1] = True
    return mask


masks = {name: np.array([raster(r) for r in geometry[name]]) for name in names}
fixed_mask = np.array([raster(r) for r in fixed])
totals = sum(mask.astype(np.uint16) for mask in masks.values())
fixed_via_mask = np.array([raster(r, 395) for r in via_geometry])
capacitor_via_keepout = raster(k.Region(top.begin_shapes_rec(layout.layer(75, 0))), 135)
fixed_via_mask |= capacitor_via_keepout


def code(node):
    level, y, x = node
    return (level * NY + y) * NX + x


def decode(value):
    level, value = divmod(value, NX * NY)
    y, x = divmod(value, NX)
    return level, y, x


def find_path(start, target, tree, blocked, name):
    tl, ty, tx = target
    first = code(start)

    def estimate(level, y, x):
        return abs(y - ty) + abs(x - tx) + 3 * abs(level - tl)

    queue = [(estimate(*start), 0, first)]
    costs, parents = {first: 0}, {}
    while queue:
        _, cost, current = heapq.heappop(queue)
        if cost != costs.get(current):
            continue
        if current in tree:
            result = [decode(current)]
            while current != first:
                current = parents[current]
                result.append(decode(current))
            return result
        level, y, x = decode(current)
        for nl, yy, xx, delta in (
            (level, y - 1, x, 1),
            (level, y + 1, x, 1),
            (level, y, x - 1, 1),
            (level, y, x + 1, 1),
            (level - 1, y, x, 4),
            (level + 1, y, x, 4),
        ):
            if not (0 <= nl < 3 and 1 <= yy < NY - 1 and 1 <= xx < NX - 1):
                continue
            if blocked[nl, yy, xx]:
                continue
            cut_key = (min(nl, level), yy, xx)
            if (
                nl != level
                and fixed_via_mask[cut_key]
                and existing_grid_vias.get(cut_key) != name
            ):
                continue
            nxt = code((nl, yy, xx))
            new = cost + delta
            if new < costs.get(nxt, 1e12):
                costs[nxt] = new
                parents[nxt] = current
                heapq.heappush(queue, (new + estimate(nl, yy, xx), new, nxt))
        if len(costs) > 900000:
            break
    return None


def draw_path(path, name):
    for first, second in zip(path, path[1:]):
        level, y, x = first
        other, yy, xx = second
        if level == other:
            region = wire((x * PITCH, y * PITCH), (xx * PITCH, yy * PITCH))
            insert(METALS[level], region)
            geometry[name][level] += region
        else:
            point = (x * PITCH, y * PITCH)
            insert(VIAS[min(level, other)], pad(point, 0.13))
            for layer in (level, other):
                insert(METALS[layer], pad(point))
                geometry[name][layer] += pad(point)


failed = []
lengths = {}
order = sorted(
    names,
    key=lambda n: (
        n in ("avdd", "avss", "dvdd", "dvss"),
        n not in ("v1", "v2", "vgn1", "vgn2"),
        -len(grid_terms[n]),
    ),
)
for name in order:
    blocked = (totals - masks[name]) > 0
    blocked |= fixed_mask
    exclusion = k.Region()
    for bounds in routing_exclusions(data, name, aliases):
        exclusion += rect(bounds)
    blocked |= raster(exclusion)
    if name in local_exclusions:
        blocked |= raster(local_exclusions[name])
    todo = list(dict.fromkeys(grid_terms[name]))
    tree = {code(todo.pop(0))}
    length = 0
    while todo:
        xyz = np.array([decode(v) for v in tree])
        candidates = []
        for idx, point in enumerate(todo):
            delta = np.abs(xyz - np.array(point))
            distances = delta[:, 1:].sum(axis=1) + 3 * delta[:, 0]
            nearest = int(np.argmin(distances))
            candidates.append((distances[nearest], idx, tuple(xyz[nearest])))
        _, idx, target = min(candidates)
        start = todo.pop(idx)
        path = find_path(start, target, tree, blocked, name)
        if path is None:
            failed.append({"net": name, "terminal": start})
            continue
        draw_path(path, name)
        tree.update(code(node) for node in path)
        length += PITCH * sum(a[0] == b[0] for a, b in zip(path, path[1:]))
    totals -= masks[name]
    masks[name] = np.array([raster(r) for r in geometry[name]])
    totals += masks[name]
    lengths[name] = length
    print(
        "Route",
        name,
        len(grid_terms[name]),
        round(length, 1),
        "um; failures",
        len(failed),
        flush=True,
    )
    layout.write(str(ROOT / "routed_candidate.gds"))

# Close sub-rule notches between same-net segments. Foreign-net spacing was
# reserved above; the independent DRC and metal audit check the final union.
for level, layer in enumerate(METALS[:2]):
    index = layout.layer(*layer)
    merged = k.Region()
    for name in names:
        region = geometry[name][level].merged().sized(140).sized(-140)
        foreign = k.Region()
        for other in names:
            if other != name:
                foreign += geometry[other][level]
        forbidden = foreign.merged().sized(285)
        for bounds in routing_exclusions(data, name, aliases):
            forbidden += rect(bounds)
        for attempt in range(3):
            errors = region.width_check(280) + region.space_check(280)
            if errors.is_empty():
                break
            patches = k.Region()
            for error in errors.each():
                bounds = error.bbox().enlarged(145)
                # All repair shapes are Manhattan and snap outward to 5 nm.
                bounds = k.Box(
                    (bounds.left // 5) * 5,
                    (bounds.bottom // 5) * 5,
                    math.ceil(bounds.right / 5) * 5,
                    math.ceil(bounds.top / 5) * 5,
                )
                patches.insert(bounds)
            if not (patches & forbidden).is_empty():
                print("Width repair needs review:", name, level, flush=True)
                break
            region = (region + patches).merged()
        merged += region
    top.shapes(index).clear()
    top.shapes(index).insert(merged)
layout.write(str(ROOT / "routed_candidate.gds"))

spice = [
    "* Original schematic dimensions; ideal 0 V probes are net aliases.",
    ".subckt Bandgap_Core_Res " + " ".join(manifest["pins"]),
]
for d in data["placed"]:
    original = devices[d["source"]]
    nodes = " ".join(root(n) for n in d["nodes"])
    model = d["model"]
    prefix = (
        "M"
        if "fet_" in model
        else (
            "Q" if model.startswith("pnp") else "C" if model.startswith("cap_") else "R"
        )
    )
    line = f'{prefix}{d["name"]} {nodes} {model}'
    if prefix != "Q":
        line += f' W={original["w"]}u L={original["l"]}u'
    spice.append(line)
spice.append(".ends Bandgap_Core_Res")
(ROOT / "reference.spice").write_text("\n".join(spice) + "\n")
(ROOT / "routing_status.json").write_text(
    json.dumps(
        {
            "failed": failed,
            "route_lengths_um": lengths,
            "aliases": aliases,
            "ports": ports,
        },
        indent=2,
    )
    + "\n"
)
print("Routing complete; unresolved terminals:", len(failed), flush=True)

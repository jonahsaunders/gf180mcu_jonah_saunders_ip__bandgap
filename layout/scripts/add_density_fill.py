"""Add deterministic fill following GF180 drawing rules, including uncoded rules.

References: GF180 design manual sections 13.1 and 13.3. This program reports
achieved density; it never edits, disables, or filters verification rules.
"""

from pathlib import Path
import json
import klayout.db as k

from layout_paths import BUILD_DIR as ROOT

layout = k.Layout()
layout.read(str(ROOT / "power_candidate.gds"))
top = layout.top_cell()
placement = json.loads((ROOT / "placement.json").read_text())
boundary = top.bbox()


def layer_region(layer, purpose=0):
    return k.Region(top.begin_shapes_rec(layout.layer(layer, purpose))).merged()


def rectangle(x, y, width, height):
    return k.Region(k.DBox(x, y, x + width, y + height).to_itype(layout.dbu))


def grow(region, microns):
    return region.sized(round(microns / layout.dbu))


def inside_boundary(candidate):
    box = candidate.bbox()
    return boundary.contains(box.p1) and boundary.contains(box.p2)


# Leave matching arrays and precision resistors free of metal fill. This
# prevents fill placement from changing the surroundings of individual units.
matching = k.Region()
for group in placement["groups"]:
    if len(group["names"]) > 1 or group["category"] == "resistors":
        matching += rectangle(group["x"], group["y"], group["width"], group["height"])
for device in placement["placed"]:
    if device["model"].startswith(("ppoly", "nwell", "pplus")):
        x1, y1, x2, y2 = device["bounds"]
        matching += rectangle(x1, y1, x2 - x1, y2 - y1)
top.shapes(layout.layer(152, 5)).insert(matching.merged())

# COMP: 5 um squares, 3 um nominal gap, 1.6 um staggering in both axes.
comp_obstacle = k.Region()
for number, purpose, gap in [
    (22, 0, 3.505),
    (30, 0, 1.505),
    (21, 0, 1.305),
    (12, 0, 4.005),
    (204, 0, 1.305),
    (55, 0, 1.305),
    (110, 5, 3.505),
    (111, 5, 3.505),
    (151, 5, 3.005),
]:
    comp_obstacle += grow(layer_region(number, purpose), gap)
comp_obstacle = comp_obstacle.merged()
comp_fill = k.Region()
for row in range(39):
    for col in range(77):
        x = 2.0 + col * 8 + (row % 2) * 1.6
        y = 4.0 + row * 8 + (col % 2) * 1.6
        candidate = rectangle(x, y, 5, 5)
        if inside_boundary(candidate) and (candidate & comp_obstacle).is_empty():
            comp_fill += candidate
top.shapes(layout.layer(22, 4)).insert(comp_fill)

# Metal fill is 2 um square on a staggered 3.2 um lattice. Consecutive levels
# use different offsets. Keep the specified 1 um gap to adjacent circuit
# metal layers even though that check is disabled in the current DRC deck.
metals = [34, 36, 42, 46, 81]
drawn = [layer_region(number) for number in metals]
fuse = layer_region(75)
exclude = grow(fuse + matching, 6.005)
records = []
for level, number in enumerate(metals):
    obstacle = exclude + grow(drawn[level], 2.005)
    previous = drawn[level - 1] if level else layer_region(30)
    obstacle += grow(previous, 1.005)
    if level < len(metals) - 1:
        obstacle += grow(drawn[level + 1], 1.005)
    obstacle = obstacle.merged()
    fill = k.Region()
    offset = 0.5 * level
    for row in range(97):
        for col in range(193):
            x = 2.0 + col * 3.2 + (row % 2) * 0.5 + offset
            y = 4.0 + row * 3.2 + (col % 2) * 0.5 + offset
            candidate = rectangle(x, y, 2, 2)
            if inside_boundary(candidate) and (candidate & obstacle).is_empty():
                fill += candidate
    top.shapes(layout.layer(number, 4)).insert(fill)
    density = (drawn[level] + fill).area() / boundary.area()
    records.append({"layer": number, "squares": fill.count(), "density": density})
    print(
        "Metal",
        level + 1,
        "fill squares",
        fill.count(),
        "density",
        round(100 * density, 3),
        flush=True,
    )

comp_density = (layer_region(22) + comp_fill).area() / boundary.area()
print(
    "COMP fill squares",
    comp_fill.count(),
    "density",
    round(100 * comp_density, 3),
    flush=True,
)
layout.write(str(ROOT / "filled_candidate.gds"))
(ROOT / "fill_summary.json").write_text(
    json.dumps({"metal": records, "comp_density": comp_density}, indent=2) + "\n"
)

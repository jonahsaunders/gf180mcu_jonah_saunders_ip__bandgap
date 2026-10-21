"""Restore device hierarchy for PEX without changing any fabricated geometry.

Magic misidentifies the foundry 5x5 PNP and does not recognize the nwell
resistor marker on GDS import. These two devices use their verified foundry
models as primitive cells. A layer-by-layer XOR proves geometry identity.
"""

from collections import defaultdict
from pathlib import Path
import json
import klayout.db as k

from layout_paths import BUILD_DIR as root

(root / "pex").mkdir(exist_ok=True)
data = json.loads((root / "placement.json").read_text())
manifest = json.loads((root / "primitive_manifest.json").read_text())
devices = {d["name"]: d for d in manifest["devices"]}
aliases = json.loads((root / "routing_status.json").read_text())["aliases"]

layout = k.Layout()
layout.read(str(root / "filled_candidate.gds"))
top = layout.top_cell()
original = {
    layout.get_info(li).to_s(): k.Region(top.begin_shapes_rec(li)).merged()
    for li in layout.layer_indexes()
}
source = k.Layout()
source.read(str(root / "placement_candidate.gds"))
cuts = defaultdict(k.Region)
created = {}
instances = []
commands = []
for device in data["placed"]:
    if device["model"] not in ("pnp_05p00x05p00", "nwell"):
        continue
    model = device["model"]
    src = source.cell(devices[device["source"]]["cell"])
    transform = k.DCplxTrans(1, device["rotation"], False, device["x"], device["y"])
    if model not in created:
        cell = layout.create_cell(model)
        for li in source.layer_indexes():
            target = layout.layer(source.get_info(li))
            cell.shapes(target).insert(k.Region(src.begin_shapes_rec(li)))
        labels = ["C", "B", "E"] if model.startswith("pnp") else ["A", "B", "W"]
        local = [transform.inverted() * k.DPoint(*p) for p in device["pins"]]
        commands.extend([f"load {model}", "select top cell"])
        if model == "nwell":
            # Restore Magic's derived resistor type over the independently
            # verified RES_MK span; this separates the well's two terminals.
            commands.extend(["box values -1um -10.095um 1um 10.095um", "paint rnw"])
        for index, (name, point) in enumerate(zip(labels, local), 1):
            cell.shapes(layout.layer(36, 10)).insert(
                k.DText(name, k.DTrans(point.x, point.y))
            )
            commands.append(f"port {name} make {index}")
            commands.append(f"port {name} index {index}")
        prop = (
            "primitive"
            if model.startswith("pnp")
            else "primitive r_width=2u r_length=20.19u"
        )
        commands.append(f'property device "{prop}"')
        created[model] = cell
    for li in source.layer_indexes():
        if source.get_info(li).layer == 36:
            # Keep pin metal in the parent as well, so parent vias meet a
            # parent conductor at the hierarchical primitive interface.
            continue
        region = k.Region(src.begin_shapes_rec(li))
        if not region.is_empty():
            target = layout.layer(source.get_info(li))
            integer_transform = k.ICplxTrans(
                1,
                device["rotation"],
                False,
                round(device["x"] / layout.dbu),
                round(device["y"] / layout.dbu),
            )
            cuts[target] += region.transformed(integer_transform)
    instances.append(k.DCellInstArray(created[model].cell_index(), transform))

for li, region in cuts.items():
    replacement = k.Region(top.begin_shapes_rec(li)) - region
    top.shapes(li).clear()
    top.shapes(li).insert(replacement)
for instance in instances:
    top.insert(instance)

for li in layout.layer_indexes():
    info = layout.get_info(li)
    if info.datatype == 10:
        continue
    rebuilt = k.Region(top.begin_shapes_rec(li)).merged()
    assert (rebuilt ^ original.get(info.to_s(), k.Region())).is_empty(), info.to_s()

# Layer 0 is a non-fabricating Magic boundary annotation. Remove it from
# this extraction-only copy to avoid repeated boundary-redefinition warnings.
boundary_layer = layout.layer(0, 0)
for cell in layout.each_cell():
    cell.shapes(boundary_layer).clear()

# Internal names make extracted results traceable to the schematic.
seen = set(manifest["pins"])
named_points = {}
primitive_anchors = {}
for device in data["placed"]:
    for name, point in zip(device["nodes"], device["pins"]):
        while name in aliases:
            name = aliases[name]
        if name not in manifest["pins"]:
            named_points.setdefault(name, point)
            if device["model"] in ("pnp_05p00x05p00", "nwell"):
                primitive_anchors.setdefault(name, point)
# extresist can otherwise omit a hierarchical primitive's internal terminal
# from its resistance tree. Anchor those terminals during extraction only.
named_points.update(primitive_anchors)
for name, point in named_points.items():
    top.shapes(layout.layer(36, 10)).insert(k.DText(name, k.DTrans(*point)))
    seen.add(name)
commands.extend(["load Bandgap_Core_Res", "select top cell"])
for name in sorted(seen):
    commands.append(f"port {name} remove")
extraction_ports = [*manifest["pins"], *sorted(primitive_anchors)]
for index, name in enumerate(extraction_ports, 1):
    commands.append(f"port {name} make {index}")
    commands.append(f"port {name} index {index}")
(root / "pex/define_primitives.tcl").write_text("\n".join(commands) + "\n")
(root / "pex/port_manifest.json").write_text(
    json.dumps(
        {
            "physical_ports": manifest["pins"],
            "extraction_ports": extraction_ports,
            "internal_anchor_points_um": primitive_anchors,
            "scope": "Temporary extraction interface only; fabricated geometry and connectivity unchanged",
        },
        indent=2,
    )
    + "\n"
)
layout.write(str(root / "pex/extraction_view.gds"))
print("PEX hierarchy restored; fabricated geometry XOR: zero differences")

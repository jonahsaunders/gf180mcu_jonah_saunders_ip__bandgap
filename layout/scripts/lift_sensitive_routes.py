"""Move the long vgn2 route to M5; keep M4 only at its layer transitions.

Device placement and terminal connections are unchanged. The unchanged
foundry DRC/LVS decks verify the complete result after supply routing and fill.
"""

from pathlib import Path
import json
import klayout.db as k
from layout_paths import BUILD_DIR as root

layout = k.Layout()
layout.read(str(root / "routed_candidate.gds"))
top = layout.top_cell()
ex = k.LayoutToNetlist(k.RecursiveShapeIterator(layout, top, []))
layers = {
    i: ex.make_layer(layout.layer(i, 0), f"L{i}")
    for i in [34, 35, 36, 38, 42, 40, 46, 41, 81]
}
fuse = ex.make_layer(layout.layer(75, 0), "FuseTop")
layers[41] = layers[41] - fuse
ex.register(layers[41], "Via4OutsideCapacitor")
for layer in layers.values():
    ex.connect(layer)
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
    ex.connect(layers[a], layers[b])
ex.extract_netlist()
data = json.loads((root / "placement.json").read_text())
drawn4 = k.Region(top.begin_shapes_rec(layout.layer(46, 0))).merged()
drawn5 = k.Region(top.begin_shapes_rec(layout.layer(81, 0))).merged()
via4 = k.Region(top.begin_shapes_rec(layout.layer(41, 0))).merged()
records = []
for name in ["vgn2"]:
    points = [
        p for d in data["placed"] for n, p in zip(d["nodes"], d["pins"]) if n == name
    ]
    net = ex.probe_net(layers[36], k.DPoint(*points[0]))
    own4 = ex.shapes_of_net(net, layers[46], True).merged()
    own5 = ex.shapes_of_net(net, layers[81], True).merged()
    cuts = ex.shapes_of_net(net, layers[40], True).merged()
    # Keep M4 only around transitions. M5 carries the long horizontal routes.
    landing = cuts.sized(170)
    # Existing M4-M5 terminal connections also need their M4 enclosure.
    landing += ex.shapes_of_net(net, layers[41], True).merged().sized(170)
    lifted = own4.sized(100).merged()
    assert (lifted & (drawn5 - own5).sized(465)).is_empty(), name
    drawn4 = (drawn4 - own4) + (landing & own4)
    drawn5 += lifted
    via4 += cuts
    records.append(
        dict(
            net=name,
            m4_area_um2=own4.area() * layout.dbu**2,
            m5_area_um2=lifted.area() * layout.dbu**2,
            via4_count=cuts.count(),
        )
    )
for number, region in [(46, drawn4), (81, drawn5), (41, via4)]:
    top.shapes(layout.layer(number, 0)).clear()
    top.shapes(layout.layer(number, 0)).insert(region.merged())
layout.write(str(root / "routed_candidate.gds"))
(root / "sensitive_routes.json").write_text(json.dumps(records, indent=2) + "\n")
print(records)

"""Layered view of the whole slot_4 layout, drawn like the core's 3D picture.

Same projection, layer stack, colours and canvas as render_layout_3d.py, which
is imported rather than copied. XY geometry is every selected polygon of the
flattened slot (datatypes 0 and 4); Z elevations are illustrative and
exaggerated, NOT foundry stack dimensions.

    python3 layout/scripts/render_slot4_3d.py   # writes docs/images/slot4-design.png
"""

from pathlib import Path
import gzip
import hashlib
import json

import klayout.db as k

from render_layout_3d import PNG_PALETTE, STACK, project
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import to_rgb

REPOSITORY = Path(__file__).resolve().parents[2]
GDS = REPOSITORY / "gds/slot_4.gds.gz"
PNG = REPOSITORY / "docs/images/slot4-design.png"
PROVENANCE = REPOSITORY / "layout/slot4/slot4-design.json"


def main():
    layout = k.Layout()
    layout.read(str(GDS))
    top = layout.top_cell()
    assert top.name == "slot_4"
    dbu = layout.dbu
    bounds = top.dbbox()
    fuse = k.Region(top.begin_shapes_rec(layout.layer(75, 0))).merged()

    fig, ax = plt.subplots(figsize=(16, 8.25), facecolor="white")
    ax.set_facecolor("white")
    base = [
        (bounds.left - 8, bounds.bottom - 8, -0.6),
        (bounds.right + 8, bounds.bottom - 8, -0.6),
        (bounds.right + 8, bounds.top + 8, -0.6),
        (bounds.left - 8, bounds.top + 8, -0.6),
    ]
    bottom_base = [(x, y, -3.2) for x, y, _ in base]
    for i, shade in [(0, "#878f93"), (1, "#707a80")]:
        face = [bottom_base[i], bottom_base[i + 1], base[i + 1], base[i]]
        ax.add_collection(
            PolyCollection([project(face)], facecolors=shade, edgecolors="none")
        )
    ax.add_collection(
        PolyCollection(
            [project(base)], facecolors="#e3edf4", edgecolors="#a6b2ba", linewidths=0.4
        )
    )

    records = []
    for number, name, z, height, color in STACK:
        region = k.Region()
        for datatype in (0, 4):
            index = layout.find_layer(number, datatype)
            if index is not None:
                region += k.Region(top.begin_shapes_rec(index))
        region.merge()
        if number == 41:
            region = region - fuse if name == "V4" else region & fuse
        roofs, sides = [], []
        for polygon in region.each():
            for part in polygon.decompose_trapezoids():
                xy = [(p.x * dbu, p.y * dbu) for p in part.each_point()]
                if len(xy) < 3:
                    continue
                lower = [(x, y, z) for x, y in xy]
                upper = [(x, y, z + height) for x, y in xy]
                if name.startswith("M") and height > 0:
                    for i in range(len(xy)):
                        j = (i + 1) % len(xy)
                        sides.append(project([lower[i], lower[j], upper[j], upper[i]]))
                roofs.append(project(upper))
        if not roofs:
            continue
        preview = PNG_PALETTE.get(name, color)
        rgb = to_rgb(preview)
        if sides:
            ax.add_collection(
                PolyCollection(
                    sides,
                    facecolors=[tuple(c * 0.58 for c in rgb)],
                    edgecolors="none",
                    rasterized=True,
                )
            )
        ax.add_collection(
            PolyCollection(
                roofs, facecolors=preview, edgecolors="none", rasterized=True
            )
        )
        records.append(
            {
                "layer": number,
                "name": name,
                "gds_area_um2": round(region.area() * dbu**2, 3),
                "trapezoids": len(roofs),
            }
        )
        print(name, len(roofs), "trapezoids", flush=True)

    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.margins(0.025)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    PNG.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(PNG, dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)

    PROVENANCE.write_text(
        json.dumps(
            {
                "image": str(PNG.relative_to(REPOSITORY)),
                "image_sha256": hashlib.sha256(PNG.read_bytes()).hexdigest(),
                "source_gds": str(GDS.relative_to(REPOSITORY)),
                "source_gds_sha256_uncompressed": hashlib.sha256(
                    gzip.decompress(GDS.read_bytes())
                ).hexdigest(),
                "top_cell": top.name,
                "bounds_um": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                "xy_geometry": "Every selected polygon of the flattened slot, "
                "datatypes 0 and 4",
                "z_geometry": "Illustrative elevations, 5x vertical exaggeration; "
                "not foundry stack dimensions",
                "style": "Projection, stack and colours imported from "
                "render_layout_3d.py",
                "layers": records,
            },
            indent=2,
        )
        + "\n"
    )
    print("wrote", PNG.relative_to(REPOSITORY))


if __name__ == "__main__":
    main()

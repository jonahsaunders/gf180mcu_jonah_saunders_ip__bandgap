"""Export exact GDS XY polygons as an illustrative layered GLB and PNG.

Z elevations are schematic and exaggerated, NOT foundry stack dimensions.
This is a geometry visualization, not an extraction or fabrication model.
All selected conductor/contact polygons, including datatype-4 fill, are
retained. KLayout trapezoid decomposition preserves polygon holes.
"""

import argparse
import hashlib
import json
from pathlib import Path
import struct

import klayout.db as k
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.colors import to_rgb
import numpy as np

# Layer number, label, illustrative elevation, thickness, color.
STACK = [(22, "Active", 0, .2, "#a7c7b5"), (30, "Poly", .5, .3, "#d4949c"),
         (33, "Contact", .8, .7, "#aeb5b5"), (34, "M1", 1.5, .6, "#d7bc73"),
         (35, "V1", 2.1, 1.4, "#aeb5b5"), (36, "M2", 3.5, .6, "#9fbdd4"),
         (38, "V2", 4.1, 1.4, "#aeb5b5"), (42, "M3", 5.5, .6, "#9abfb4"),
         (40, "V3", 6.1, 1.4, "#aeb5b5"), (46, "M4", 7.5, .7, "#b2b2c9"),
         (41, "V4", 8.2, 1.8, "#aeb5b5"),
         (75, "MIM top plate", 8.65, .2, "#c4a0a8"),
         (41, "MIM top contacts", 8.85, 1.15, "#aeb5b5"),
         (81, "M5", 10, 1.2, "#759db4")]


# Preview styling is separate from GLB materials and physical geometry.
PNG_PALETTE = {"Active": "#75b59b", "Poly": "#ce7964", "M1": "#dab35a",
               "M2": "#8eafb9", "M3": "#5fa99a"}

def project(points):
    a = np.asarray(points)
    azimuth, elevation = np.deg2rad([24, 45])
    ca, sa, se, ce = np.cos(azimuth), np.sin(azimuth), np.sin(elevation), np.cos(elevation)
    return np.column_stack((ca*a[:, 0]+sa*a[:, 1],
                            -sa*se*a[:, 0]+ca*se*a[:, 1]+5*ce*a[:, 2]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gds", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    layout = k.Layout()
    layout.read(str(args.gds))
    top = layout.top_cell()
    assert top.name == "Bandgap_Core_Res"
    dbu = layout.dbu
    bounds = top.dbbox()
    # Option B: FuseTop is a separate electrode above M4. Via4 inside it
    # lands on that electrode, not on the M4 bottom plate.
    # https://gf180mcu-pdk.readthedocs.io/en/latest/physical_verification/design_manual/drm_10_4_2.html
    fuse = k.Region(top.begin_shapes_rec(layout.layer(75, 0))).merged()
    doc = {"asset": {"version": "2.0", "generator": "render_layout_3d.py",
                     "extras": {"z": "Illustrative stack elevations; not process dimensions"}},
           "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [],
           "materials": [], "buffers": [], "bufferViews": [], "accessors": []}
    binary = bytearray()
    def accessor(data, component, kind, target, bounds=False):
        while len(binary) % 4:
            binary.append(0)
        view = len(doc["bufferViews"])
        doc["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": data.nbytes, "target": target})
        binary.extend(data.tobytes())
        entry = {"bufferView": view, "componentType": component, "count": len(data), "type": kind}
        if bounds:
            entry.update(min=data.min(axis=0).tolist(), max=data.max(axis=0).tolist())
        result = len(doc["accessors"])
        doc["accessors"].append(entry)
        return result
    fig, ax = plt.subplots(figsize=(16, 8.2), facecolor="white")
    ax.set_facecolor("white")
    base = [(bounds.left-8, bounds.bottom-8, -.6), (bounds.right+8, bounds.bottom-8, -.6),
            (bounds.right+8, bounds.top+8, -.6), (bounds.left-8, bounds.top+8, -.6)]
    bottom_base = [(x, y, -3.2) for x, y, _ in base]
    # Visible front and right faces of an illustrative support slab.
    for i, shade in [(0, "#878f93"), (1, "#707a80")]:
        j = i+1
        face = [bottom_base[i], bottom_base[j], base[j], base[i]]
        ax.add_collection(PolyCollection([project(face)], facecolors=shade, edgecolors="none"))
    ax.add_collection(PolyCollection([project(base)], facecolors="#e3edf4",
                                     edgecolors="#a6b2ba", linewidths=.4))
    records = []
    for number, name, z, height, color in STACK:
        region = k.Region()
        for datatype in (0, 4):
            idx = layout.find_layer(number, datatype)
            if idx is not None:
                region += k.Region(top.begin_shapes_rec(idx))
        region.merge()
        if number == 41:
            region = region - fuse if name == "V4" else region & fuse
        vertices, triangles, roofs, sides = [], [], [], []
        area = 0.0
        for polygon in region.each():
            for part in polygon.decompose_trapezoids():
                xy = [(p.x*dbu, p.y*dbu) for p in part.each_point()]
                if len(xy) < 3:
                    continue
                signed = sum(xy[i][0]*xy[(i+1)%len(xy)][1]-xy[(i+1)%len(xy)][0]*xy[i][1] for i in range(len(xy)))
                if signed < 0:
                    xy.reverse()
                area += abs(signed)/2
                n, offset = len(xy), len(vertices)
                bottom = [(x, y, z) for x, y in xy]
                upper = [(x, y, z+height) for x, y in xy]
                vertices.extend(bottom+upper)
                for i in range(1, n-1):
                    triangles.extend([(offset, offset+i+1, offset+i),
                                      (offset+n, offset+n+i, offset+n+i+1)])
                for i in range(n):
                    j = (i+1) % n
                    triangles.extend([(offset+i, offset+j, offset+n+j),
                                      (offset+i, offset+n+j, offset+n+i)])
                    if name.startswith("M") and height > 0:
                        sides.append(project([bottom[i], bottom[j], upper[j], upper[i]]))
                roofs.append(project(upper))
        if not vertices:
            continue
        assert abs(area-region.area()*dbu**2) < max(.01, area*1e-8), name
        v = np.asarray(vertices, dtype="<f4")
        t = np.asarray(triangles, dtype="<u4").reshape(-1)
        assert np.isfinite(v).all() and t.max() < len(v)
        assert np.max(np.abs(v[:, :2].astype(float) - np.asarray(vertices)[:, :2])) < dbu/2
        material = len(doc["materials"])
        rgb = to_rgb(color)
        doc["materials"].append({"name": name, "doubleSided": True,
                                 "pbrMetallicRoughness": {"baseColorFactor": [*rgb, 1], "metallicFactor": .35, "roughnessFactor": .5}})
        mesh = len(doc["meshes"])
        doc["meshes"].append({"name": name, "primitives": [{"attributes": {"POSITION": accessor(v, 5126, "VEC3", 34962, True)},
                                "indices": accessor(t, 5125, "SCALAR", 34963), "material": material}]})
        doc["scenes"][0]["nodes"].append(len(doc["nodes"]))
        # glTF units are metres; XY is converted from micrometres, Z is
        # intentionally stretched 5x for inspection of the layer order.
        doc["nodes"].append({"name": name, "mesh": mesh, "scale": [1e-6, 1e-6, 5e-6]})
        preview_color = PNG_PALETTE.get(name, color)
        preview_rgb = to_rgb(preview_color)
        if sides:
            ax.add_collection(PolyCollection(sides, facecolors=[tuple(c*.58 for c in preview_rgb)], edgecolors="none", rasterized=True))
        ax.add_collection(PolyCollection(roofs, facecolors=preview_color, edgecolors="none", rasterized=True))
        records.append({"layer": number, "name": name, "gds_area_um2": region.area()*dbu**2,
                        "selection": "outside FuseTop" if name == "V4" else "inside FuseTop" if name == "MIM top contacts" else "complete layer",
                        "mesh_projected_area_um2": area, "vertices": len(v), "triangles": len(t)//3,
                        "illustrative_z_um": z, "illustrative_thickness_um": height})
        print(name, len(v), "vertices", flush=True)
    ax.autoscale_view()
    ax.set_aspect("equal")
    ax.margins(.025)
    ax.axis("off")
    fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
    png = args.out / "bandgap-design.png"
    fig.savefig(png, dpi=200, facecolor=fig.get_facecolor())
    plt.close(fig)
    while len(binary) % 4:
        binary.append(0)
    doc["buffers"].append({"byteLength": len(binary)})
    js = json.dumps(doc, separators=(",", ":")).encode()
    js += b" "*((-len(js)) % 4)
    glb = args.out / "bandgap-layout.glb"
    payload = (struct.pack("<III", 0x46546C67, 2, 12+8+len(js)+8+len(binary)) +
               struct.pack("<II", len(js), 0x4E4F534A) + js +
               struct.pack("<II", len(binary), 0x004E4942) + binary)
    glb.write_bytes(payload)
    assert struct.unpack_from("<I", payload, 8)[0] == glb.stat().st_size
    assert len(doc["nodes"]) == len(records) == 14
    via4 = k.Region(top.begin_shapes_rec(layout.layer(41, 0))).merged()
    assert abs(sum(r["gds_area_um2"] for r in records if r["layer"] == 41) - via4.area()*dbu**2) < .01
    provenance = {"source_gds_sha256": hashlib.sha256(args.gds.read_bytes()).hexdigest(),
                  "source_gds": str(args.gds), "top_cell": top.name, "bounds_um": [bounds.left, bounds.bottom, bounds.right, bounds.top],
                  "xy_geometry": "Exact selected GDS polygons, datatypes 0 and 4; holes preserved by trapezoid decomposition",
                  "z_geometry": "Illustrative elevations, 5x vertical exaggeration; not foundry stack dimensions",
                  "mim_option_B": "FuseTop electrode is separated from M4; Via4 inside FuseTop starts at the top electrode, preserving the capacitor gap",
                  "layers": records, "sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in [png, glb]}}
    (args.out / "provenance.json").write_text(json.dumps(provenance, indent=2)+"\n")


if __name__ == "__main__":
    main()

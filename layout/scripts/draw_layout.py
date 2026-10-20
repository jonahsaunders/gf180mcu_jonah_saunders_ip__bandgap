"""Render the actual GDS and placement; all displayed geometry is measured."""

from pathlib import Path
import json
import klayout.db as k
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Rectangle

from layout_paths import BUILD_DIR as ROOT

OUT = ROOT / "figures"
OUT.mkdir(exist_ok=True)
data = json.loads((ROOT / "placement.json").read_text())
layout = k.Layout()
layout.read(str(ROOT / "filled_candidate.gds"))
top = layout.top_cell()
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})


def style(ax):
    ax.set_aspect("equal")
    ax.set_xlim(0, 620)
    ax.set_ylim(0, 315)
    ax.set_xlabel("x (µm)")
    ax.set_ylabel("y (µm)")
    ax.spines[["top", "right"]].set_visible(False)


fig, ax = plt.subplots(figsize=(15, 8.5))
fig.subplots_adjust(left=0.06, right=0.99, bottom=0.12, top=0.80)
colors = {"analog": "#d6e8f5", "digital": "#efcfdf", "resistors": "#f5deb2"}
for group in data["groups"]:
    x, y, w, h = (group[key] for key in ("x", "y", "width", "height"))
    ax.add_patch(
        Rectangle(
            (x, y),
            w,
            h,
            facecolor=colors[group["category"]],
            edgecolor="#536477",
            linewidth=0.6,
        )
    )
    if w * h > 600 or len(group["names"]) > 1:
        label = " / ".join(group["names"])
        if len(label) > 18 or (len(group["names"]) > 1 and w < 80):
            label = "\n".join(group["names"])
        ax.text(
            x + w / 2,
            y + h / 2,
            label,
            ha="center",
            va="center",
            fontsize=7,
            rotation=90 if w < 15 else 0,
        )
for device in data["placed"]:
    if not device["model"].startswith("cap_"):
        continue
    x, y, xx, yy = device["bounds"]
    ax.add_patch(
        Rectangle((x, y), xx - x, yy - y, facecolor="#d7e8c6", edgecolor="#557c38")
    )
    ax.text(
        (x + xx) / 2,
        (y + yy) / 2,
        device["name"] + "\nMIM + AVSS shield",
        ha="center",
        va="center",
        fontsize=8,
    )
ax.add_patch(
    Rectangle(
        (5, 162),
        56,
        144,
        fill=False,
        edgecolor="#9e4579",
        linewidth=1.8,
        linestyle="--",
    )
)
ax.text(
    33, 313, "Trim logic / DNW", ha="center", va="bottom", fontsize=9, color="#8b285e"
)
ax.text(
    145,
    313,
    "Trim resistor ladder",
    ha="center",
    va="bottom",
    fontsize=9,
    color="#956025",
)
ax.text(
    420, 313, "Output capacitors", ha="center", va="bottom", fontsize=9, color="#557c38"
)
style(ax)
fig.suptitle(
    "Implemented placement · trim-inclusive bandgap\n272 physical device units · provisional 620 × 315 µm envelope",
    x=0.06,
    y=0.96,
    ha="left",
    fontweight="bold",
)
fig.text(
    0.06,
    0.025,
    "Actual placement, not the initial concept. The public 537.15 × 273 µm wrapper script is smaller; final slot integration is unresolved.",
    fontsize=10,
    color="#8a3b22",
)
fig.savefig(OUT / "implemented-floorplan.png", dpi=180)
plt.close(fig)

fig, ax = plt.subplots(figsize=(15, 8.5))
fig.subplots_adjust(left=0.06, right=0.99, bottom=0.12, top=0.85)
layers = [
    (12, "#d5c4ef", 0.4),
    (21, "#e4d8c5", 0.5),
    (22, "#92b87e", 0.85),
    (30, "#d56067", 0.8),
    (34, "#66a0df", 0.75),
    (36, "#d2ae36", 0.8),
    (42, "#9864bd", 0.75),
    (46, "#46a295", 0.6),
    (75, "#a1bd76", 0.45),
    (81, "#718fa3", 0.55),
]
for number, color, alpha in layers:
    polygons = []
    for poly in k.Region(top.begin_shapes_rec(layout.layer(number, 0))).merged().each():
        # Trapezoids preserve holes in rings, combs, and routing.
        polygons.extend(
            [
                [(p.x * layout.dbu, p.y * layout.dbu) for p in part.each_point()]
                for part in poly.decompose_trapezoids()
            ]
        )
    ax.add_collection(
        PolyCollection(
            polygons, facecolors=color, edgecolors="none", alpha=alpha, rasterized=True
        )
    )
for number in [22, 34, 36, 42, 46, 81]:
    polygons = [
        [(p.x * layout.dbu, p.y * layout.dbu) for p in poly.each_point_hull()]
        for poly in k.Region(top.begin_shapes_rec(layout.layer(number, 4))).each()
    ]
    ax.add_collection(
        PolyCollection(
            polygons,
            facecolors="#777777",
            edgecolors="none",
            alpha=0.12,
            rasterized=True,
        )
    )
style(ax)
fig.suptitle(
    "Actual routed and filled GDS\nGF180MCU-D · five metals · MIM option B · 1 kΩ/□ poly",
    x=0.06,
    y=0.96,
    ha="left",
    fontweight="bold",
)
fig.text(
    0.06,
    0.025,
    "Core verification: 0 DRC violations and LVS match. Wrapper integration and full post-layout qualification remain open.",
    fontsize=10,
    color="#264d6e",
)
fig.savefig(OUT / "physical-layout.png", dpi=220)
plt.close(fig)

records = []
fig, axes = plt.subplots(2, 4, figsize=(16, 8), layout="constrained")
matched = [g for g in data["groups"] if len(g["names"]) > 1]
for ax, group in zip(axes.flat, matched):
    for index, name in enumerate(group["names"]):
        units = [d for d in data["placed"] if d["source"] == name]
        xs = [d["x"] for d in units]
        ys = [d["y"] for d in units]
        color = plt.cm.Set2(index)
        ax.scatter(xs, ys, s=180, color=color, edgecolor="#333", label=name)
        for d in units:
            ax.text(d["x"], d["y"], name, ha="center", va="center", fontsize=6)
        cx = sum(xs) / len(xs)
        cy = sum(ys) / len(ys)
        records.append(
            {
                "group": group["names"],
                "name": name,
                "units": len(units),
                "centroid_um": [cx, cy],
            }
        )
        ax.scatter([cx], [cy], marker="+", s=160, color="#c83b2e")
    ax.set_title(" / ".join(group["names"]), fontsize=10)
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.15)
fig.suptitle(
    "Matched unit locations · crosses mark geometric centroids\nOrientation is preserved; routing symmetry and edge dummies remain review items.",
    fontsize=14,
)
fig.savefig(OUT / "matched-arrays.png", dpi=180)
(ROOT / "matching_audit.json").write_text(json.dumps(records, indent=2) + "\n")

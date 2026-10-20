"""Dimensioned paper figures from the actual GDS and saved placement.

Functional annotations are overlays; device positions and polygons are never
altered for the illustration. The concept drawing is explicitly separate.
"""

import json
import klayout.db as k

from figure_style import WIDTH, INK, BLUE, configure, export
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Rectangle

BLOCK_COLORS = {"analog": "#DCEAF3", "digital": "#E4DBEE", "resistors": "#F4E4C5"}
CAP_COLOR = "#D7E9E1"


def rectangle(ax, x, y, w, h, color, label=None, **kwargs):
    ax.add_patch(
        Rectangle((x, y), w, h, facecolor=color, edgecolor=INK, lw=0.65, **kwargs)
    )
    if label:
        ax.text(x + w / 2, y + h / 2, label, ha="center", va="center", fontsize=8)


def dimension(ax, start, end, text, vertical=False):
    ax.annotate(
        "",
        xy=end,
        xytext=start,
        arrowprops={"arrowstyle": "<->", "lw": 0.7, "shrinkA": 0, "shrinkB": 0},
    )
    x, y = (start[0] + end[0]) / 2, (start[1] + end[1]) / 2
    ax.text(
        x,
        y,
        text,
        ha="center",
        va="center",
        rotation=90 if vertical else 0,
        fontsize=9,
        bbox={"facecolor": "white", "edgecolor": "none", "pad": 2},
    )


def layout_axes(bounds, height=4.25, dimension_y=24, decimals=1):
    fig, ax = plt.subplots(figsize=(WIDTH, height))
    fig.subplots_adjust(left=0.01, right=0.995, bottom=0.09, top=0.96)
    x0, y0, x1, y1 = bounds
    ax.set_aspect("equal")
    ax.set_xlim(x0 - 43, x1 + 8)
    ax.set_ylim(y0 - 14, y1 + 55)
    ax.axis("off")
    dimension(
        ax, (x0, y1 + dimension_y), (x1, y1 + dimension_y), f"{x1-x0:.{decimals}f} µm"
    )
    dimension(ax, (x0 - 24, y0), (x0 - 24, y1), f"{y1-y0:.1f} µm", True)
    ax.add_patch(
        Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor=INK, lw=0.8)
    )
    return fig, ax


def draw_initial(out):
    """Retain the original planning allocations, not the implemented footprint."""
    configure()
    fig, ax = layout_axes((0, 0, 537.15, 273), height=4.1, decimals=2)
    rectangle(ax, 12, 119, 34, 136, BLOCK_COLORS["digital"])
    ax.text(29, 187, "Trim logic / switches", rotation=90, ha="center", va="center")
    rectangle(ax, 53, 119, 81, 136, BLOCK_COLORS["resistors"], "1 kΩ/□\ntrim ladder")
    rectangle(ax, 12, 15, 122, 91, BLOCK_COLORS["analog"], "PNP / PTAT\ncore + mirrors")
    rectangle(
        ax,
        145,
        15,
        173,
        80,
        BLOCK_COLORS["analog"],
        "Error amplifier\nMatched input pair",
    )
    rectangle(ax, 329, 15, 111, 80, "#E9EAEC", "Bias +\nstartup")
    rectangle(ax, 453, 20, 63, 63, CAP_COLOR, "C1\nComp.")
    for row in range(2):
        for col in range(5):
            rectangle(
                ax,
                145 + col * 74.4,
                111 + row * 74.4,
                72,
                72,
                CAP_COLOR,
                f"C{2+row*5+col}",
            )
    ax.text(330, 265, "Output MIM bank (~100 pF)", ha="center", va="center", fontsize=8)
    ax.text(330, 103, "Routing / supply channel", ha="center", va="center", fontsize=7)
    fig.text(
        0.53,
        0.035,
        "HISTORICAL CONCEPT • superseded planning envelope",
        ha="center",
        fontsize=8,
    )
    export(fig, out, "initial-floorplan")


def draw_harness_fit(bounds, planning, out):
    """Show geometric fit only; the centered placement does not modify GDS."""
    configure()
    width, height = planning["width_um"], planning["height_um"]
    core_w, core_h = bounds[2] - bounds[0], bounds[3] - bounds[1]
    x_margin, y_margin = (width - core_w) / 2, (height - core_h) / 2
    fraction = core_w * core_h / (width * height)
    fig, ax = plt.subplots(figsize=(WIDTH, 3.0))
    fig.subplots_adjust(left=0.02, right=0.99, bottom=0.17, top=0.90)
    ax.set_aspect("equal")
    ax.set_xlim(-85, width + 20)
    ax.set_ylim(-20, height + 80)
    ax.axis("off")
    rectangle(ax, 0, 0, width, height, "#F4F5F6")
    rectangle(
        ax,
        x_margin,
        y_margin,
        core_w,
        core_h,
        BLOCK_COLORS["analog"],
        f"Existing trim-inclusive core\n{core_w:.1f} × {core_h:.1f} µm\n"
        f"{100*fraction:.1f}% of draft user area",
    )
    dimension(ax, (0, height + 40), (width, height + 40), f"≈ {width:g} µm")
    dimension(ax, (-45, 0), (-45, height), f"≈ {height:g} µm", vertical=True)
    for x in [x_margin / 2, width - x_margin / 2]:
        ax.text(
            x,
            height / 2,
            "Unallocated\nplanning space",
            ha="center",
            va="center",
            fontsize=8,
        )
    fig.text(
        0.5,
        0.955,
        "Chipalooza #3 • draft user area",
        ha="center",
        fontsize=9,
        weight="bold",
    )
    fig.text(
        0.5,
        0.095,
        f"Centered illustration: {x_margin:g} µm each side; {y_margin:g} µm top/bottom",
        ha="center",
        fontsize=8,
    )
    fig.text(
        0.5,
        0.035,
        "Approximate organizer guidance supplied by designer • final wrapper and pins pending",
        ha="center",
        fontsize=7,
    )
    export(fig, out, "harness-area-fit")


def draw_placement(data, bounds, out):
    configure()
    fig, ax = layout_axes(bounds, dimension_y=70)
    for group in data["groups"]:
        rectangle(
            ax,
            group["x"],
            group["y"],
            group["width"],
            group["height"],
            BLOCK_COLORS[group["category"]],
        )
    for device in data["placed"]:
        if device["model"].startswith("cap_"):
            x, y, xx, yy = device["bounds"]
            rectangle(ax, x, y, xx - x, yy - y, CAP_COLOR, device["name"])
    # Numbered matching groups keep tiny geometries readable without hiding
    # adjacent devices. Full source names are given beneath the drawing.
    groups = [g for g in data["groups"] if len(g["names"]) > 1]
    for number, group in enumerate(groups, 1):
        ax.text(
            group["x"] + group["width"] / 2,
            group["y"] + group["height"] / 2,
            str(number),
            ha="center",
            va="center",
            weight="bold",
            fontsize=8,
            bbox={"boxstyle": "circle,pad=.22", "fc": "white", "ec": INK, "lw": 0.6},
        )
    ax.add_patch(
        Rectangle((5, 162), 56, 144, fill=False, edgecolor="#705290", lw=1, ls="--")
    )
    for text, point, label in [
        ("Trim logic\nDNW", (33, 266), (33, 340)),
        ("Poly trim\nladder", (108, 275), (145, 340)),
        ("Output capacitors C2–C11", (420, 301), (420, 340)),
    ]:
        ax.annotate(
            text,
            xy=point,
            xytext=label,
            ha="center",
            va="bottom",
            fontsize=8,
            arrowprops={"arrowstyle": "-", "lw": 0.6},
        )
    # Move dimensions above the annotation line by using the available upper band.
    ax.set_ylim(bounds[1] - 14, bounds[3] + 85)
    fig.set_size_inches(WIDTH, 4.9)
    fig.subplots_adjust(bottom=0.21)
    entries = [f"{n}  {' / '.join(g['names'])}" for n, g in enumerate(groups, 1)]
    for index, text in enumerate(entries):
        fig.text(
            0.07 + 0.47 * (index // 4), 0.155 - 0.035 * (index % 4), text, fontsize=7.5
        )
    export(fig, out, "implemented-floorplan")


def polygon_vertices(layout, top, number, purpose=0):
    region = k.Region(top.begin_shapes_rec(layout.layer(number, purpose))).merged()
    # Decompose polygons so holes remain holes in the rendering.
    return [
        [(p.x * layout.dbu, p.y * layout.dbu) for p in part.each_point()]
        for poly in region.each()
        for part in poly.decompose_trapezoids()
    ]


def draw_physical(layout, bounds, out):
    configure()
    fig, ax = layout_axes(bounds, height=4.35)
    top = layout.top_cell()
    layers = [
        (12, "#D8C8E8", 0.35),
        (21, "#CABDA7", 0.40),
        (22, "#539565", 0.75),
        (30, "#C45146", 0.90),
        (34, "#3779B1", 0.80),
        (36, "#BC9127", 0.82),
        (42, "#8559A4", 0.78),
        (46, "#279786", 0.62),
        (75, "#A5AE68", 0.40),
        (81, "#527E8C", 0.60),
    ]
    for number, color, alpha in layers:
        ax.add_collection(
            PolyCollection(
                polygon_vertices(layout, top, number),
                facecolors=color,
                edgecolors="none",
                alpha=alpha,
                rasterized=True,
            )
        )
    for number in [22, 34, 36, 42, 46, 81]:
        ax.add_collection(
            PolyCollection(
                polygon_vertices(layout, top, number, 4),
                facecolors="#888888",
                edgecolors="none",
                alpha=0.12,
                rasterized=True,
            )
        )
    # Label broad regions only; the outline remains actual routed/filled GDS.
    for x, y, text in [
        (33, 237, "Trim\nlogic"),
        (118, 237, "Poly\nladder"),
        (420, 223, "Output MIM bank"),
        (166, 60, "PNP"),
        (275, 44, "Input pair"),
        (568, 54, "C1"),
    ]:
        ax.text(
            x,
            y,
            text,
            ha="center",
            va="center",
            fontsize=8,
            bbox={
                "facecolor": "white",
                "edgecolor": INK,
                "lw": 0.4,
                "alpha": 0.94,
                "pad": 2,
            },
        )
    fig.text(
        0.53,
        0.035,
        "LAYOUT RENDERING • GF180MCU-D • 5 metals • 614.0 × 307.6 µm",
        ha="center",
        fontsize=8,
    )
    export(fig, out, "physical-layout")


def draw_matching(data, out):
    configure()
    groups = [g for g in data["groups"] if len(g["names"]) > 1]
    assert len(groups) == 8, "Update the panel allocation if matching groups change"
    fig, axes = plt.subplots(
        4, 2, figsize=(WIDTH, 7.6), gridspec_kw={"height_ratios": [0.45, 1, 1, 1]}
    )
    fig.subplots_adjust(
        left=0.08, right=0.98, bottom=0.07, top=0.93, hspace=0.56, wspace=0.26
    )
    records = []
    colors = [BLUE, "#C84332", "#007F69"]
    markers = ["s", "o", "^"]
    for ax, group in zip(axes.flat, groups):
        xs_all, ys_all = [], []
        origin_x, origin_y = group["x"], group["y"]
        for index, name in enumerate(group["names"]):
            units = [d for d in data["placed"] if d["source"] == name]
            xs, ys = [d["x"] for d in units], [d["y"] for d in units]
            cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
            records.append(
                {
                    "group": group["names"],
                    "name": name,
                    "units": len(units),
                    "centroid_um": [cx, cy],
                }
            )
            local_x, local_y = [x - origin_x for x in xs], [y - origin_y for y in ys]
            xs_all.extend(local_x)
            ys_all.extend(local_y)
            ax.scatter(
                local_x,
                local_y,
                s=36,
                c=colors[index],
                marker=markers[index],
                edgecolor=INK,
                linewidth=0.4,
                label=f"{name} ×{len(units)}",
                zorder=3,
            )
            ax.scatter(
                [cx - origin_x],
                [cy - origin_y],
                marker="+",
                s=65,
                linewidth=1.1,
                color=INK,
                zorder=4,
            )
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(min(xs_all) - 5, max(xs_all) + 5)
        ax.set_ylim(min(ys_all) - 5, max(ys_all) + 5)
        ax.tick_params(labelsize=7, top=True, right=True)
        ax.set_xlabel("Local x (µm)", fontsize=7, labelpad=1)
        ax.set_ylabel("Local y (µm)", fontsize=7, labelpad=1)
        ax.grid(color="#D7DADD", lw=0.4)
        ax.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=len(group["names"]),
            fontsize=7,
            handletextpad=0.3,
            columnspacing=0.6,
            handlelength=1,
        )
    fig.text(
        0.5,
        0.975,
        "Matched unit placement • crosses mark geometric centroids",
        ha="center",
        fontsize=9,
        weight="bold",
    )
    fig.text(
        0.5,
        0.025,
        "Actual placement origins; equal axis scale within each panel. Q3 remains separate from Q1/Q2.",
        ha="center",
        fontsize=8,
    )
    export(fig, out, "matched-arrays")
    return records


def draw_all(gds_path, placement_path, out):
    data = json.loads(placement_path.read_text())
    layout = k.Layout()
    layout.read(str(gds_path))
    box = layout.top_cell().dbbox()
    bounds = (box.left, box.bottom, box.right, box.top)
    draw_initial(out)
    draw_placement(data, bounds, out)
    draw_physical(layout, bounds, out)
    return draw_matching(data, out)


if __name__ == "__main__":
    from layout_paths import BUILD_DIR

    records = draw_all(
        BUILD_DIR / "filled_candidate.gds",
        BUILD_DIR / "placement.json",
        BUILD_DIR / "figures",
    )
    (BUILD_DIR / "matching_audit.json").write_text(json.dumps(records, indent=2) + "\n")

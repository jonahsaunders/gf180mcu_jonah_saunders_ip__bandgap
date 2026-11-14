"""Draw the slot-4 integration from the same plan that builds gds/slot_4.gds.gz.

Core metal is read from the released GDS and the added traces from
build_slot4.plan_routes(), so the picture cannot drift from the layout.

    python3 layout/scripts/draw_slot4.py     # writes layout/slot4/slot4-integration.png
"""

import klayout.db as k

from figure_style import BLUE, GRAY, GREEN, INK, RED, WIDTH, configure
import build_slot4 as slot
import matplotlib.pyplot as plt
from matplotlib.collections import PolyCollection
from matplotlib.patches import Patch, Rectangle

OUTPUT = slot.REPOSITORY / "layout/slot4/slot4-integration.png"
AMBER, SKY, VIOLET = "#B8860B", "#3A9BD9", "#7B4FA3"
NETS = [
    ("avdd", RED, "avdd ← vdd_5v0"),
    ("avss", BLUE, "avss ← vss_5v0"),
    ("dvdd", AMBER, "dvdd ← vdd_3v3"),
    ("dvss", SKY, "dvss ← vss_3v3"),
    ("vref", GREEN, "vref → s4_an[0]"),
    ("b", VIOLET, "b0–b6 ← dig_in[0–6]"),
]
COLOR = {name: color for name, color, _ in NETS}
LAYER_STYLE = {
    slot.M5: {"alpha": 0.80, "hatch": None},
    slot.M4: {"alpha": 0.45, "hatch": "////"},
    slot.M3: {"alpha": 0.45, "hatch": "...."},
}


def outlines(region):
    return [
        [(p.x / 1000, p.y / 1000) for p in polygon.to_simple_polygon().each_point()]
        for polygon in region.merged().each()
    ]


def net_color(net):
    return COLOR["b"] if net.startswith("b") else COLOR[net]


def panel(ax, window, plan, core, wrapper, layers, core_layers, vias=False, flat=False):
    x0, y0, x1, y1 = window
    clip = k.Region(k.Box(*[round(v * 1000) for v in window]))
    place = k.Trans(k.Trans.M0, slot.nm(slot.CORE_X), slot.nm(slot.CORE_Y))
    core_cell = core.top_cell()
    for layer in core_layers:
        metal = (
            slot.flat_region(core, core_cell, layer, 0)
            + slot.flat_region(core, core_cell, layer, 4)
        ).transformed(place) & clip
        ax.add_collection(
            PolyCollection(
                outlines(metal),
                facecolor="#D9DCDF",
                edgecolor="#AEB3B8",
                linewidth=0.15,
                rasterized=True,
            )
        )
    for layer in layers:
        for net in plan.shapes:
            region = plan.region(layer, net) & clip
            if region.is_empty():
                continue
            style = LAYER_STYLE[slot.M5] if flat else LAYER_STYLE[layer]
            ax.add_collection(
                PolyCollection(
                    outlines(region),
                    facecolor=net_color(net),
                    edgecolor=net_color(net),
                    linewidth=0.3,
                    alpha=style["alpha"],
                    hatch=style["hatch"],
                )
            )
    if vias:
        for via, face in ((slot.VIA3, INK), (slot.VIA4, "white")):
            cuts = plan.region(via) & clip
            ax.add_collection(
                PolyCollection(
                    [
                        [(p.x / 1000, p.y / 1000) for p in c.each_point_hull()]
                        for c in cuts.each()
                    ],
                    facecolor=face,
                    edgecolor=INK,
                    linewidth=0.15,
                )
            )
    for layer in (slot.M3, slot.M5):
        pins = k.Region(
            wrapper.begin_shapes(wrapper.top_cell(), wrapper.find_layer(layer, 10))
        )
        ax.add_collection(
            PolyCollection(
                outlines(pins & clip), facecolor="none", edgecolor=INK, linewidth=0.6
            )
        )
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.tick_params(top=True, right=True)


def main():
    configure()
    wrapper = k.Layout()
    wrapper.read(str(slot.WRAPPER_GDS))
    plan = slot.plan_routes(slot.wrapper_pins(wrapper), wrapper)
    core = k.Layout()
    core.read(str(slot.CORE_GDS))
    outline = wrapper.top_cell().bbox()
    width, height = outline.width() / 1000, outline.height() / 1000

    fig = plt.figure(figsize=(WIDTH, 6.6))
    grid = fig.add_gridspec(
        2,
        3,
        height_ratios=[1.0, 1.3],
        width_ratios=[0.9, 1.5, 0.9],
        left=0.07,
        right=0.985,
        top=0.91,
        bottom=0.115,
        hspace=0.3,
        wspace=0.28,
    )
    full = fig.add_subplot(grid[0, :])
    panel(
        full,
        (-12, -8, width + 12, height + 8),
        plan,
        core,
        wrapper,
        (slot.M3, slot.M4, slot.M5),
        (slot.M5,),
        flat=True,
    )
    full.add_patch(
        Rectangle((0, 0), width, height, fill=False, edgecolor=INK, linewidth=0.7)
    )
    full.set_title(f"slot_4: {width:.2f} × {height:.2f} µm", pad=20)
    full.set_ylabel("y (µm)")
    full.text(
        950,
        150,
        "Bandgap_Core_Res\n(released GDS, mirrored in y)",
        ha="center",
        va="center",
        fontsize=8,
        color=INK,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.8, "pad": 2},
    )
    full.legend(
        handles=[
            Patch(facecolor=c, edgecolor=c, alpha=0.8, label=text)
            for _, c, text in NETS
        ],
        loc="lower center",
        bbox_to_anchor=(0.5, 1.0),
        ncol=6,
        columnspacing=1.0,
        handlelength=1.2,
        fontsize=7,
    )

    zooms = [
        (
            "Output: 0.6 µm from the core\nto a pad-height plate",
            (1244, 26, 1276, 78),
            "a",
        ),
        (
            "Core edge: digital supplies and trim bits\npass under the AVDD collector",
            (474, 182, 600, 246),
            "b",
        ),
        ("Pin row: 0.29 µm pins\nstep onto 2 µm tracks", (-2, 150, 34, 226), "c"),
    ]
    for column, (title, window, tag) in enumerate(zooms):
        ax = fig.add_subplot(grid[1, column])
        panel(
            ax,
            window,
            plan,
            core,
            wrapper,
            (slot.M3, slot.M4, slot.M5),
            (slot.M5,),
            vias=True,
        )
        ax.set_title(f"({tag}) {title}", fontsize=8, pad=5)
        ax.set_xlabel("x (µm)")
        if column == 0:
            ax.set_ylabel("y (µm)")
        x0, y0, x1, y1 = window
        full.add_patch(
            Rectangle(
                (x0, y0),
                x1 - x0,
                y1 - y0,
                fill=False,
                edgecolor=GRAY,
                linewidth=0.6,
                linestyle="--",
            )
        )
        full.text(
            x0 + (x1 - x0) / 2,
            y1 + 4,
            tag,
            ha="center",
            va="bottom",
            fontsize=7,
            color=GRAY,
        )
    fig.text(
        0.985,
        0.015,
        "Solid: metal 5 · hatched: metal 4 · dotted: metal 3 · white/black squares: "
        "via4/via3 · black outline: harness pin · metal 1/2 straps and dummy fill "
        "not drawn",
        ha="right",
        va="bottom",
        fontsize=6.5,
        color=GRAY,
    )
    fig.savefig(OUTPUT, dpi=300)
    plt.close(fig)
    print("wrote", OUTPUT.relative_to(slot.REPOSITORY))


if __name__ == "__main__":
    main()

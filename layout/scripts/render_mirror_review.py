"""Render measured mirror locations and an explicitly unimplemented array proposal."""

from pathlib import Path
import json

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np

from figure_style import WIDTH, INK, BLUE, configure, export

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "reviews/2026-10-21-mirrors"
data = json.loads((OUT / "mirror_geometry_audit.json").read_text())
placement = json.loads((ROOT / "release/placement.json").read_text())
units = data["units"]
colors = {
    "PREF": "#795A94",
    "PRMIR": "#795A94",
    "MPSINK": "#795A94",
    "MPFOLD": "#795A94",
    "VBNSINK": BLUE,
    "MSINK1": BLUE,
    "MSINK2": BLUE,
    "MFOLD1": "#BD5A31",
    "MFOLD2": "#BD5A31",
}


def center(names):
    points = [unit["channel_center_um"] for unit in units if unit["source"] in names]
    return np.mean(points, axis=0)


def draw_locations():
    configure()
    fig, ax = plt.subplots(figsize=(WIDTH, 4.15))
    fig.subplots_adjust(left=0.02, right=0.99, bottom=0.12, top=0.92)
    for device in placement["placed"]:
        x0, y0, x1, y1 = device["bounds"]
        ax.add_patch(
            Rectangle(
                (x0, y0),
                x1 - x0,
                y1 - y0,
                facecolor="#ECEEF0",
                edgecolor="#C5C9CD",
                lw=0.3,
            )
        )
    for unit in units:
        if unit["source"] not in colors:
            continue
        x0, y0, x1, y1 = unit["channel_bounds_um"]
        ax.add_patch(
            Rectangle(
                (x0, y0),
                x1 - x0,
                y1 - y0,
                facecolor=colors[unit["source"]],
                edgecolor="none",
            )
        )
    pairs = [
        ("PREF", "MPSINK"),
        ("PREF", "MPFOLD"),
        ("VBNSINK", "MSINK1"),
        ("MFOLD1", "MFOLD2"),
    ]
    for a, b in pairs:
        first, second = center([a]), center([b])
        ax.plot(
            [first[0], second[0]],
            [first[1], second[1]],
            color=colors[a],
            lw=0.8,
            ls="--",
            alpha=0.9,
        )
    annotations = [
        (["PREF", "PRMIR"], (67, -26), "PREF / PRMIR"),
        (["MPSINK"], (335, 125), "MPSINK"),
        (["MPFOLD"], (454, -26), "MPFOLD"),
        (["MSINK1", "MSINK2"], (353, -26), "MSINK1 / 2"),
        (["VBNSINK"], (493, 119), "VBNSINK"),
        (["MFOLD1"], (133, 137), "MFOLD1"),
        (["MFOLD2"], (196, -26), "MFOLD2"),
    ]
    for names, location, label in annotations:
        ax.annotate(
            label,
            xy=center(names),
            xytext=location,
            ha="center",
            va="center",
            fontsize=7.5,
            color=colors[names[0]],
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1},
            arrowprops={"arrowstyle": "-", "color": colors[names[0]], "lw": 0.65},
        )
    ax.text(33, 240, "Trim\nlogic", ha="center", va="center", fontsize=8)
    ax.text(124, 246, "Poly trim\nladder", ha="center", va="center", fontsize=8)
    ax.text(421, 260, "Output MIM capacitors", ha="center", va="center", fontsize=8)
    ax.text(568, 55, "C1", ha="center", va="center", fontsize=8)
    ax.set_xlim(-4, 620)
    ax.set_ylim(-44, 324)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.text(
        0.5,
        0.96,
        "Mirror and amplifier branch placement • measured channel locations",
        ha="center",
        fontsize=9,
        weight="bold",
    )
    fig.text(
        0.5,
        0.065,
        "PREF → MPSINK: 304.1 µm    PREF → MPFOLD: 373.2 µm",
        ha="center",
        fontsize=8,
    )
    fig.text(
        0.5,
        0.025,
        "VBNSINK → MSINK1/2: 92.1 µm    MFOLD1 → MFOLD2: 193.9 µm",
        ha="center",
        fontsize=8,
    )
    export(fig, OUT, "mirror-placement-review")


def moments(pattern):
    coordinates = {name: [] for name in "ABC"}
    for row, labels in enumerate(pattern):
        for col, name in enumerate(labels):
            coordinates[name].append((col - 2.5, row - 1))
    result = {}
    for name, points in coordinates.items():
        p = np.array(points)
        assert len(p) == 6 and np.allclose(p.mean(axis=0), [0, 0])
        result[name] = [
            float(np.mean(p[:, 0] ** 2)),
            float(np.mean(p[:, 0] * p[:, 1])),
            float(np.mean(p[:, 1] ** 2)),
        ]
    return result


def draw_patterns():
    configure()
    current = ["ABCCBA"] * 3
    proposed = ["ABCCBA", "BCAACB", "CABBAC"]
    # Verify the illustrated current pattern against actual gate locations.
    selected = [u for u in units if u["source"] in ["M1", "M2", "M5"]]
    row_positions = sorted({round(u["channel_center_um"][1], 6) for u in selected})
    mapping = {"M1": "A", "M2": "B", "M5": "C"}
    actual = []
    for y in row_positions:
        row = sorted(
            [u for u in selected if abs(u["channel_center_um"][1] - y) < 1e-5],
            key=lambda u: u["channel_center_um"][0],
        )
        actual.append("".join(mapping[u["source"]] for u in row))
    assert actual == current
    before, after = moments(current), moments(proposed)
    assert np.allclose(after["A"], after["B"]) and np.allclose(after["A"], after["C"])
    fig, axes = plt.subplots(1, 2, figsize=(WIDTH, 3.0))
    fig.subplots_adjust(left=0.03, right=0.98, bottom=0.27, top=0.70, wspace=0.18)
    palette = {"A": "#B7D5E8", "B": "#F0D1B0", "C": "#D3C4E0"}
    for ax, pattern, title in zip(
        axes,
        [current, proposed],
        ["Current • outer columns all M1", "Proposed permutation • not implemented"],
    ):
        for row, labels in enumerate(pattern):
            for col, label in enumerate(labels):
                ax.add_patch(
                    Rectangle(
                        (col, row),
                        0.92,
                        0.92,
                        facecolor=palette[label],
                        edgecolor=INK,
                        lw=0.5,
                    )
                )
                ax.text(
                    col + 0.46, row + 0.46, label, ha="center", va="center", fontsize=9
                )
        ax.set_xlim(-0.1, 6)
        ax.set_ylim(-0.1, 3)
        ax.set_aspect("equal")
        ax.axis("off")
        ax.set_title(title, fontsize=8, pad=10)
    fig.text(
        0.5,
        0.93,
        "Core mirror M1 / M2 / M5 • identical units, unchanged 6:6:6 count",
        ha="center",
        fontsize=9,
        weight="bold",
    )
    fig.text(
        0.5,
        0.85,
        "A = M1    B = M2    C = M5    •    all devices retain the same orientation",
        ha="center",
        fontsize=8,
    )
    fig.text(0.26, 0.21, "Mean x² / pitch²: 6.25, 2.25, 0.25", ha="center", fontsize=8)
    fig.text(
        0.75, 0.21, "Mean x² / pitch²: 2.917 for all three", ha="center", fontsize=8
    )
    fig.text(
        0.5,
        0.12,
        "Both patterns have equal centroids. The proposal also balances all second spatial moments.",
        ha="center",
        fontsize=7.5,
    )
    fig.text(
        0.5,
        0.045,
        "Gate-position model only • add surrounding dummies and balanced routing; then reverify electrically",
        ha="center",
        fontsize=7,
    )
    (OUT / "proposed_pattern_check.json").write_text(
        json.dumps(
            {
                "implemented": False,
                "current": current,
                "proposed": proposed,
                "current_moments_in_pitch_units": before,
                "proposed_moments_in_pitch_units": after,
            },
            indent=2,
        )
        + "\n"
    )
    export(fig, OUT, "mirror-array-review")


if __name__ == "__main__":
    draw_locations()
    draw_patterns()
    print("Rendered measured placement and verified, unimplemented array proposal.")

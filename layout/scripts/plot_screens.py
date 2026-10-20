"""Plot saved simulation evidence without rerunning or changing the circuit."""

import json
import re

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from layout_paths import BUILD_DIR

data = BUILD_DIR / "postlayout"
out = BUILD_DIR / "figures"
out.mkdir(exist_ok=True)
summary = json.loads((data / "nominal_summary.json").read_text())
assert all(r["exit_code"] == 0 and not r["errors"] for r in summary)

fig, axes = plt.subplots(2, 2, figsize=(13, 9), layout="constrained")
colors = {3.3: "#176caa", 5.0: "#b85230"}
for supply in [3.3, 5.0]:
    color = colors[supply]
    folder = data / f"rc_typical_typical_typical_{supply:g}"
    temp = np.loadtxt(folder / "temperature.dat", skiprows=1)
    axes[0, 0].plot(temp[:, 0], temp[:, 1], label=f"{supply:g} V RC", color=color)
    for mode, style in [("schematic", "--"), ("rc", "-")]:
        ac = np.loadtxt(
            data / f"{mode}_typical_typical_typical_{supply:g}/psrr.dat", skiprows=1
        )
        axes[0, 1].semilogx(
            ac[:, 0], ac[:, 1], style, color=color, label=f"{supply:g} V {mode}"
        )
    text = (folder / "trim.dat").read_text()
    rows = [
        list(map(float, line.split()))
        for line in text.splitlines()
        if re.match(r"^\s*[-+\d.]", line)
    ]
    trim = np.asarray(rows)
    axes[1, 0].plot(trim[:, 1], trim[:, 2], label=f"{supply:g} V", color=color)
    tran = np.loadtxt(
        data / f"rc_typical_typical_typical_{supply:g}_startup/startup.dat", skiprows=1
    )
    axes[1, 1].plot(tran[:, 0] * 1e3, tran[:, 2], label=f"{supply:g} V", color=color)
for ax, title, xlabel, ylabel in zip(
    axes.flat,
    [
        "Temperature · fixed trim 72",
        "Supply rejection · fixed trim 72",
        "All 128 trim codes · 25 °C",
        "Cold start and restart · trim 72",
    ],
    ["Temperature (°C)", "Frequency (Hz)", "Trim code", "Time (ms)"],
    ["Reference (V)", "PSRR (dB)", "Reference (V)", "Reference (V)"],
):
    ax.set(title=title, xlabel=xlabel, ylabel=ylabel)
    ax.grid(alpha=0.2)
    ax.legend(fontsize=8)
axes[0, 1].axhline(
    45, color="#666", lw=0.8, linestyle=":", label="README planning minimum"
)
fig.suptitle(
    "Nominal post-layout screens · TT · digital supply 3.3 V\nSignal/supply RC included; ground resistance omitted; MIM compatibility model used",
    fontsize=14,
)
fig.savefig(out / "post-layout-screens.png", dpi=170)
plt.close(fig)

corners = json.loads((data / "pvt_dc/summary.json").read_text())
fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), layout="constrained")
for ax, supply in zip(axes, [3.3, 5.0]):
    for mos, color in zip(["typical", "ff", "ss", "fs", "sf"], plt.cm.Set2.colors):
        points = [
            p
            for case in corners
            if case["mos"] == mos and case["supply_V"] == supply
            for p in case["points"]
        ]
        for temp in [-40, 25, 110, 125]:
            values = [p["vref_V"] for p in points if p["temperature_C"] == temp]
            x = temp + (["typical", "ff", "ss", "fs", "sf"].index(mos) - 2) * 1.2
            ax.scatter(
                [x] * len(values),
                values,
                s=13,
                color=color,
                alpha=0.7,
                label=mos if temp == -40 else None,
            )
    ax.set(
        title=f"Analog supply {supply:g} V",
        xlabel="Temperature (°C)",
        ylabel="Reference (V)",
    )
    ax.grid(alpha=0.2)
    ax.legend(fontsize=8, ncol=3)
fig.suptitle(
    "360 converged DC points · trim fixed at 72\n45 MOS/BJT/resistor combinations × 2 supplies × 4 temperatures; no per-corner calibration",
    fontsize=13,
)
fig.savefig(out / "process-corner-screen.png", dpi=170)

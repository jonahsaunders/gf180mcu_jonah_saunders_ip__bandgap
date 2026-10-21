"""Paper-scale figures from saved simulation evidence; never run a simulator."""

import gzip
import json
import re

from figure_style import (
    BLUE,
    RED,
    GRAY,
    SUPPLIES,
    WIDTH,
    configure,
    export,
    frequency_axis,
    plot_axes,
)
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.ticker import MultipleLocator
import numpy as np


def saved_path(path):
    return path if path.exists() else path.with_suffix(path.suffix + ".gz")


def samples(path):
    return np.loadtxt(saved_path(path), skiprows=1)


def trim_samples(path):
    path = saved_path(path)
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt") as stream:
        return np.asarray(
            [
                list(map(float, line.split()))
                for line in stream
                if re.match(r"^\s*[-+\d.]", line)
            ]
        )


def supply_legend(fig, y=0.98):
    handles = [
        Line2D([], [], color=c, ls=s, label=f"AVDD = {v:g} V") for v, c, s in SUPPLIES
    ]
    fig.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, y), ncol=2)


def plot_nominal(data, out):
    configure()
    summary = json.loads((data / "nominal_summary.json").read_text())
    assert all(r["exit_code"] == 0 and not r["errors"] for r in summary)
    fig, axes = plt.subplots(2, 2, figsize=(WIDTH, 4.9))
    fig.subplots_adjust(
        left=0.09, right=0.98, bottom=0.105, top=0.82, wspace=0.30, hspace=0.56
    )
    for supply, color, style in SUPPLIES:
        folder = data / f"rcc_typical_typical_typical_{supply:g}"
        temp = samples(folder / "temperature.dat")
        axes[0, 0].plot(temp[:, 0], temp[:, 1] * 1e3, color=color, ls=style)
        ac = samples(folder / "psrr.dat")
        axes[0, 1].plot(ac[:, 0], ac[:, 1], color=color, ls=style)
        trim = trim_samples(folder / "trim.dat")
        axes[1, 0].plot(trim[:, 1], trim[:, 2] * 1e3, color=color, ls=style)
        axes[1, 1].plot(temp[:, 0], -temp[:, 2] * 1e6, color=color, ls=style)
    for ax, title, xlabel, ylabel in zip(
        axes.flat,
        [
            "Temperature response",
            "Supply rejection",
            "Trim transfer",
            "Analog supply current",
        ],
        ["Temperature (°C)", "Frequency (Hz)", "Trim code", "Temperature (°C)"],
        ["Reference (mV)", "PSRR (dB)", "Reference (mV)", "Current (µA)"],
    ):
        plot_axes(ax, xlabel, ylabel, title)
    axes[0, 0].set_xticks([-40, 0, 40, 80, 125])
    axes[0, 0].set_xlim(-40, 125)
    frequency_axis(axes[0, 1])
    axes[0, 1].set_ylim(40, 95)
    axes[1, 0].set_xticks([0, 32, 64, 96, 127])
    axes[1, 0].set_xlim(0, 127)
    axes[1, 1].set_xlim(-40, 125)
    axes[1, 1].set_xticks([-40, 0, 40, 80, 125])
    supply_legend(fig)
    fig.text(
        0.5,
        0.90,
        "Simulated RCC approximation • TT • DVDD = 3.3 V • trim 72 except trim sweep",
        ha="center",
        fontsize=8,
    )
    export(fig, out, "post-layout-screens")


def plot_corners(corners_path, out):
    """Show saved points at actual temperatures, without x jitter.

    Separate MOS-corner panels make the nine BJT/resistor combinations legible.
    Vertical strokes show min–max; markers retain every individual sample,
    including overlapping samples. Lines between temperatures are not inferred.
    """
    configure()
    cases = json.loads(corners_path.read_text())
    fig, axes = plt.subplots(2, 5, figsize=(WIDTH, 4.15), sharex=True, sharey=True)
    fig.subplots_adjust(
        left=0.10, right=0.985, bottom=0.13, top=0.85, wspace=0.08, hspace=0.30
    )
    for row, (supply, color, _) in enumerate(SUPPLIES):
        for col, mos in enumerate(["typical", "ff", "ss", "fs", "sf"]):
            ax = axes[row, col]
            points = [
                p
                for case in cases
                if case["mos"] == mos and case["supply_V"] == supply
                for p in case["points"]
            ]
            for temp in [-40, 25, 110, 125]:
                values = [
                    p["vref_V"] * 1e3 for p in points if p["temperature_C"] == temp
                ]
                ax.vlines(temp, min(values), max(values), color=color, lw=0.6)
                ax.plot(
                    [temp] * len(values),
                    values,
                    "o",
                    color=color,
                    ms=2.4,
                    markerfacecolor="white",
                    markeredgewidth=0.6,
                    clip_on=False,
                )
            plot_axes(ax, "", "")
            ax.set_xlim(-55, 140)
            ax.set_ylim(1170, 1225)
            ax.set_xticks([-40, 25, 125])
            ax.tick_params(labelsize=7)
            if row == 0:
                ax.set_title("TT" if mos == "typical" else mos.upper())
            if col == 0:
                ax.set_ylabel(f"AVDD = {supply:g} V\nReference (mV)")
    fig.text(0.54, 0.025, "Temperature (°C)", ha="center", fontsize=9)
    fig.text(
        0.54,
        0.965,
        "Fixed trim 72 • 360 simulated DC points",
        ha="center",
        fontsize=9,
        weight="bold",
    )
    fig.text(
        0.54,
        0.915,
        "Each column: MOS corner; each temperature: 9 BJT/resistor combinations",
        ha="center",
        fontsize=8,
    )
    export(fig, out, "process-corner-screen")


def plot_power_sequence(data, out):
    configure()
    fig, axes = plt.subplots(
        2, 1, figsize=(WIDTH, 3.8), sharex=True, gridspec_kw={"height_ratios": [1, 2.2]}
    )
    fig.subplots_adjust(left=0.09, right=0.98, bottom=0.135, top=0.90, hspace=0.16)
    for supply, color, style in SUPPLIES:
        values = samples(data / f"{supply:g}/startup.dat")
        axes[0].plot(values[:, 0] * 1e3, values[:, 1], color=color, ls=style)
        axes[1].plot(values[:, 0] * 1e3, values[:, 3], color=color, ls=style)
    axes[0].plot(
        values[:, 0] * 1e3,
        values[:, 2],
        color=GRAY,
        ls=":",
        lw=1.0,
        label="DVDD (both cases)",
    )
    for ax, ylabel in zip(axes, ["Supply (V)", "Reference (V)"]):
        plot_axes(ax, "", ylabel)
        ax.set_xlim(0, 4)
    axes[0].set_ylim(-0.3, 5.7)
    axes[0].set_yticks([0, 3.3, 5])
    axes[0].legend(loc="center right", fontsize=7)
    axes[1].set_xlabel("Time (ms)")
    axes[1].text(0.40, 0.13, "Cold start", transform=axes[1].transAxes, ha="center")
    axes[1].text(0.86, 0.13, "Restart", transform=axes[1].transAxes, ha="center")
    supply_legend(fig, 1.005)
    export(fig, out, "both-supply-startup")


def plot_psrr(data, out):
    configure()
    styles = [
        ("schematic", "revised", "Schematic", "#222222", (0, (5, 2))),
        ("rcc", "baseline", "Previous layout · RCC", GRAY, (0, (2, 2))),
        ("rcc", "revised", "Routing revision · RCC", BLUE, "-"),
    ]
    fig, axes = plt.subplots(
        2, 2, figsize=(WIDTH, 5.15), gridspec_kw={"height_ratios": [1.65, 1]}
    )
    fig.subplots_adjust(
        left=0.09, right=0.98, bottom=0.10, top=0.78, wspace=0.30, hspace=0.53
    )
    for col, supply in enumerate([3.3, 5.0]):
        curves = {}
        ax = axes[0, col]
        for mode, revision, label, color, style in styles:
            curve = samples(data / f"{mode}_{supply:g}_{revision}.dat")
            curves[(mode, revision)] = curve
            ax.plot(curve[:, 0], curve[:, 1], color=color, ls=style, label=label)
        plot_axes(ax, "Frequency (Hz)", "PSRR (dB)", f"AVDD = {supply:g} V")
        frequency_axis(ax)
        ax.set_ylim(35, 95)
        ax.set_yticks([40, 50, 60, 70, 80, 90])
        zoom = axes[1, col]
        minima = []
        minimum_frequencies = []
        for revision, color, style in [
            ("baseline", GRAY, (0, (4, 2))),
            ("revised", BLUE, "-"),
        ]:
            curve = curves[("rcc", revision)]
            zoom.plot(curve[:, 0] / 1e3, curve[:, 1], color=color, ls=style)
            index = np.argmin(curve[:, 1])
            freq, value = curve[index]
            minima.append(value)
            minimum_frequencies.append(freq / 1e3)
            zoom.plot(freq / 1e3, value, "o", color=color, ms=3.5, mfc="white", mew=0.9)
        plot_axes(zoom, "Frequency (kHz)", "PSRR (dB)")
        low = min(minimum_frequencies)
        high = max(minimum_frequencies)
        span = max(high - low, 10)
        zoom.set_xlim(max(0, low - span / 2), high + span / 2)
        zoom.set_ylim(min(minima) - 0.35, max(minima) + 1.3)
        zoom.yaxis.set_major_locator(
            MultipleLocator(1 if max(minima) - min(minima) > 2.5 else 0.5)
        )
        zoom.text(
            0.98,
            0.96,
            f"Min: {minima[0]:.2f} → {minima[1]:.2f} dB\nLayout change: {minima[1]-minima[0]:+.2f} dB",
            transform=zoom.transAxes,
            ha="right",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "edgecolor": "none", "pad": 1.5},
        )
    handles, labels = axes[0, 0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.52, 0.99),
        ncol=2,
        columnspacing=1.5,
        handlelength=3.4,
    )
    fig.text(
        0.535,
        0.875,
        "Simulated • TT • 25 °C • DVDD = 3.3 V • trim 72 • no external load",
        ha="center",
        fontsize=8,
    )
    export(fig, out, "psrr-before-after")


if __name__ == "__main__":
    from layout_paths import BUILD_DIR

    data = BUILD_DIR / "postlayout"
    out = BUILD_DIR / "figures"
    plot_nominal(data, out)
    plot_corners(data / "pvt_dc/summary.json", out)
    plot_power_sequence(data / "power_sequence", out)

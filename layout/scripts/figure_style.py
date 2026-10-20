"""Shared paper-scale typography and lossless figure exports.

Plots use saved samples directly, without smoothing or curve fitting. Captions
carry the long qualifications, leaving the artwork legible at its stated size.
"""

from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import EngFormatter, LogLocator, NullFormatter

BLUE = "#0072B2"
RED = "#C84332"
GREEN = "#007F69"
GRAY = "#62666B"
INK = "#17191C"
SUPPLIES = [(3.3, BLUE, "-"), (5.0, RED, "--")]
WIDTH = 7.1


def configure():
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Liberation Sans", "DejaVu Sans"],
            "font.size": 8,
            "axes.labelsize": 9,
            "axes.titlesize": 9,
            "axes.titleweight": "bold",
            "axes.linewidth": 0.7,
            "axes.edgecolor": INK,
            "text.color": INK,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "xtick.direction": "in",
            "ytick.direction": "in",
            "xtick.major.width": 0.7,
            "ytick.major.width": 0.7,
            "lines.linewidth": 1.35,
            "lines.markersize": 3,
            "legend.fontsize": 8,
            "legend.frameon": False,
            "legend.handlelength": 2.8,
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "path",
            "svg.hashsalt": "gf180-bandgap-paper-figures",
            "path.simplify": False,
            "axes.formatter.useoffset": False,
        }
    )


def plot_axes(ax, xlabel, ylabel, title=None):
    ax.set(xlabel=xlabel, ylabel=ylabel)
    if title:
        ax.set_title(title, pad=7)
    ax.tick_params(top=True, right=True)
    ax.grid(axis="y", color="#D7DADD", linewidth=0.45)
    ax.set_axisbelow(True)


def frequency_axis(ax):
    ax.set_xscale("log")
    ax.set_xlim(1, 1e6)
    ax.set_xticks([1, 10, 100, 1e3, 1e4, 1e5, 1e6])
    ax.xaxis.set_major_formatter(EngFormatter(sep=""))
    ax.xaxis.set_minor_locator(LogLocator(base=10, subs=[2, 5]))
    ax.xaxis.set_minor_formatter(NullFormatter())


def export(fig, out, name):
    """Fixed paper size, vector text/lines, and a 600-dpi raster copy."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    fig.savefig(out / f"{name}.png", dpi=600)
    fig.savefig(out / f"{name}.pdf", dpi=600, metadata={"CreationDate": None})
    fig.savefig(out / f"{name}.svg", dpi=600, metadata={"Date": None})
    plt.close(fig)

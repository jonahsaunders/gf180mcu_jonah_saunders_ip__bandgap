"""Counterfactual AC experiments. These netlists are NOT physical deliverables."""

from pathlib import Path
from layout_paths import BUILD_DIR
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
import re
import subprocess
import numpy as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--build", default=str(BUILD_DIR))
parser.add_argument("--output", default=str(BUILD_DIR / "psrr_diagnostics"))
parser.add_argument("--sweep", action="store_true")
parser.add_argument("--pairs", action="store_true")
parser.add_argument(
    "--models",
    nargs="+",
    help="Run only these named variants (for example: schematic rcc); default: all",
)
parser.add_argument(
    "--supply-coupling",
    action="store_true",
    help="Diagnostic removal of AVDD coupling to selected high-impedance amplifier nodes",
)
args = parser.parse_args()
build = Path(args.build)
out = Path(args.output)
out.mkdir(parents=True, exist_ok=True)
source = (build / "netlists/TB10_RESISTOR_TRIM.spice").read_text()
header = source.split("* TB_PVT_MODELS_BEGIN", 1)[1].split("* TB_PVT_MODELS_END", 1)[0]
ports = "avdd avss vref b0 b1 b2 b3 b4 b5 b6 dvss dvdd".split()


def records(path):
    return [
        line.split() for line in re.sub(r"\n\+\s*", " ", path.read_text()).splitlines()
    ]


rc = records(build / "pex/Bandgap_Core_Res_rc_simulation.spice")
cc = records(build / "pex/Bandgap_Core_Res_c_simulation.spice")


def unions(rows, select=lambda a, b: True):
    parent = {}

    def find(a):
        while a in parent:
            a = parent[a]
        return a

    def rank(a):
        return a not in ports, a.count("."), len(a), a

    for row in rows:
        if row and row[0].startswith("R") and select(row[1], row[2]):
            a, b = find(row[1]), find(row[2])
            if a != b:
                keep, other = sorted([a, b], key=rank)
                parent[other] = keep
    return {a: find(a) for a in parent}


all_aliases = unions(rc)


def base(node):
    return all_aliases.get(node, node)


def transform(rows, zero_r=None, drop_c=None):
    aliases = unions(rows, lambda a, b: zero_r(base(a), base(b))) if zero_r else {}
    result = []
    for row in rows:
        if not row:
            continue
        kind = row[0][0]
        if kind == "C" and drop_c and drop_c(base(row[1]), base(row[2])):
            continue
        if kind in "XRC":
            row = [aliases.get(word, word) for word in row]
        if kind in "RC" and row[1] == row[2]:
            continue
        result.append(" ".join(row))
    return "\n".join(result) + "\n"


def capacitance(value):
    factors = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6}
    return (
        float(value[:-1]) * factors[value[-1]] if value[-1] in factors else float(value)
    )


for label, rows in [("c", cc), ("rc", rc)]:
    pairs = defaultdict(float)
    for row in rows:
        if row and row[0].startswith("C"):
            pair = tuple(sorted(map(base, row[1:3])))
            if pair[0] != pair[1]:
                pairs[pair] += capacitance(row[3]) * 1e15
    (out / f"{label}_capacitance_pairs.json").write_text(
        json.dumps(
            [
                dict(nodes=pair, fF=value)
                for pair, value in sorted(pairs.items(), key=lambda p: -p[1])
            ],
            indent=2,
        )
    )

variants = {
    "rc": transform(rc),
    "c": transform(cc),
    "rc_zero_all_r": transform(rc, zero_r=lambda a, b: True),
    "rc_drop_all_c": transform(rc, drop_c=lambda a, b: True),
}
variants["schematic"] = (build / "postlayout/schematic.spice").read_text()
variants["magic_lvs"] = (
    (build / "pex/Bandgap_Core_Res_magic_lvs_simulation.spice")
    .read_text()
    .replace("cap_mim_2f0_m4m5_noshield", "cap_mim_2f0fF")
)
if (build / "pex/Bandgap_Core_Res_rcc_simulation.spice").exists():
    rcc = records(build / "pex/Bandgap_Core_Res_rcc_simulation.spice")
    variants["rcc"] = transform(rcc)
    variants["rcc_zero_all_r"] = transform(rcc, zero_r=lambda a, b: True)
    if args.supply_coupling:
        for node in ["vgn2", "xf1", "xf2", "pm1", "pm2", "y1"]:
            variants[f"rcc_drop_avdd_{node}"] = transform(
                rcc, drop_c=lambda a, b, wanted={"avdd", node}: {a, b} == wanted
            )
if args.sweep:
    nets = sorted({base(r[1]) for r in rc if r and r[0].startswith("R")})
    for net in nets:
        variants[f"zero_r_{net}"] = transform(rc, zero_r=lambda a, b, n=net: a == n)
    nets = sorted({n for r in cc if r and r[0].startswith("C") for n in r[1:3]})
    for net in nets:
        variants[f"drop_c_{net}"] = transform(
            cc, drop_c=lambda a, b, n=net: n in (a, b)
        )
if args.pairs:
    for net in sorted({base(r[1]) for r in rc if r and r[0].startswith("R")}):
        variants[f"rc_drop_c_{net}"] = transform(
            rc, drop_c=lambda a, b, n=net: n in (a, b)
        )
    for a, b in sorted(
        {
            tuple(sorted(map(base, r[1:3])))
            for r in rc
            if r
            and r[0].startswith("C")
            and any(base(n) in ("vgn2", "xf2", "pm1", "y1") for n in r[1:3])
        }
    ):
        variants[f"rc_drop_pair_{a}_{b}"] = transform(
            rc, drop_c=lambda x, y, p={a, b}: {x, y} == p
        )
        variants[f"c_drop_pair_{a}_{b}"] = transform(
            cc, drop_c=lambda x, y, p={a, b}: {x, y} == p
        )


def run(case):
    name, supply = case
    folder = out / f"{name}_{supply:g}"
    folder.mkdir(exist_ok=True)
    fingerprint = hashlib.sha256(
        (variants[name] + header + repr(supply)).encode()
    ).hexdigest()
    metrics = folder / "metrics.json"
    if metrics.exists():
        old = json.loads(metrics.read_text())
        if old.get("sha256") == fingerprint:
            return old
    (folder / "dut.spice").write_text(variants[name])
    lines = [
        "* PSRR diagnostic only",
        header,
        ".options klu num_threads=1",
        ".include dut.spice",
        "XDUT avdd 0 vref b0 b1 b2 b3 b4 b5 b6 0 dvdd Bandgap_Core_Res",
        f"VDD avdd 0 {supply} AC 1",
        "VDVDD dvdd 0 3.3",
    ]
    lines += [f"VB{i} b{i} 0 {3.3 if 72 & (1 << i) else 0}" for i in range(7)]
    lines += [
        ".control",
        "set num_threads=1",
        "set noaskquit",
        "set numdgt=15",
        "set wr_singlescale",
        "set wr_vecnames",
        "op",
        "wrdata op.dat v(vref) i(vdd)",
        "ac dec 80 1 1Meg",
        "let psrr=-db(v(vref))",
        "wrdata psrr.dat psrr",
        "quit",
        ".endc",
        ".end",
    ]
    (folder / "test.spice").write_text("\n".join(lines) + "\n")
    for filename in ["op.dat", "psrr.dat"]:
        (folder / filename).unlink(missing_ok=True)
    with (folder / "ngspice.log").open("w") as log:
        p = subprocess.run(
            ["ngspice", "-b", "test.spice"],
            cwd=folder,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    log = (folder / "ngspice.log").read_text()
    if p.returncode or re.search(
        r"(?i)error|timestep too small|simulation interrupted", log
    ):
        raise RuntimeError(f"Failed: {folder}")
    ac = np.loadtxt(folder / "psrr.dat", skiprows=1)
    op = np.loadtxt(folder / "op.dat", skiprows=1)
    assert ac.shape == (481, 2) and np.isfinite(ac).all() and np.isfinite(op).all()
    worst = np.argmin(ac[:, 1])
    record = dict(
        name=name,
        supply=supply,
        psrr_min_dB=float(ac[worst, 1]),
        worst_Hz=float(ac[worst, 0]),
        dc_psrr_dB=float(ac[0, 1]),
        vref_V=float(op[1]),
        idd_uA=float(-op[2] * 1e6),
        sha256=fingerprint,
    )
    metrics.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    return record


if args.models:
    unknown = set(args.models) - variants.keys()
    if unknown:
        parser.error(f"Unknown model variants: {sorted(unknown)}")
    variants = {name: variants[name] for name in dict.fromkeys(args.models)}

with ThreadPoolExecutor(max_workers=4) as pool:
    results = list(pool.map(run, [(n, v) for n in variants for v in [3.3, 5.0]]))
(out / "summary.json").write_text(json.dumps(results, indent=2) + "\n")
if {"c", "rcc_zero_all_r"}.issubset(variants):
    checks = []
    for supply in [3.3, 5.0]:
        reference = np.loadtxt(out / f"c_{supply:g}/psrr.dat", skiprows=1)
        collapsed = np.loadtxt(out / f"rcc_zero_all_r_{supply:g}/psrr.dat", skiprows=1)
        assert np.array_equal(reference[:, 0], collapsed[:, 0])
        error = float(np.max(np.abs(reference[:, 1] - collapsed[:, 1])))
        assert error < 0.001, f"Capacitance conservation AC check failed: {error} dB"
        checks.append(
            dict(
                supply_V=supply,
                maximum_curve_error_dB=error,
                tolerance_dB=0.001,
                frequency_points=len(reference),
            )
        )
    (out / "conservation_ac_check.json").write_text(json.dumps(checks, indent=2) + "\n")

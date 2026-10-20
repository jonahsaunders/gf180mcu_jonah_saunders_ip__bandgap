"""Start and restart both rails together, with trim inputs tracking DVDD.

This complements the analog-only power-cycle bench, where DVDD stays at 3.3 V.
No initial conditions, UIC, nodesets, or startup injection are applied.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import hashlib
import json
import re
import subprocess

import numpy as np

from layout_paths import BUILD_DIR
from screen_extracted import HEADER, circuit_file
from plot_screens import plot_power_sequence

OUT = BUILD_DIR / "postlayout/power_sequence"
OUT.mkdir(parents=True, exist_ok=True)


def run_case(supply):
    folder = OUT / f"{supply:g}"
    folder.mkdir(exist_ok=True)

    def ramp(voltage):
        return f"PWL(0 0 100u {voltage} 2m {voltage} 2.001m 0 2.5m 0 2.6m {voltage} 4m {voltage})"

    lines = [
        f"* Both rails start from zero, analog supply {supply:g} V",
        HEADER,
        ".options klu num_threads=1",
        f'.include {circuit_file("rcc")}',
        "XDUT avdd 0 vref b0 b1 b2 b3 b4 b5 b6 0 dvdd Bandgap_Core_Res",
        f"VDD avdd 0 {ramp(supply)}",
        f"VDVDD dvdd 0 {ramp(3.3)}",
    ]
    for bit in range(7):
        if 72 & (1 << bit):
            lines.append(f"EB{bit} b{bit} 0 dvdd 0 1")
        else:
            lines.append(f"VB{bit} b{bit} 0 0")
    lines += [
        ".control",
        "set noaskquit",
        "set num_threads=1",
        "set numdgt=15",
        "set wr_singlescale",
        "set wr_vecnames",
        "save v(avdd) v(dvdd) v(vref) i(vdd)",
        "tran 1u 4m 0 100n",
        "wrdata startup.dat v(avdd) v(dvdd) v(vref) i(vdd)",
        "quit",
        ".endc",
        ".end",
    ]
    text = "\n".join(lines) + "\n"
    (folder / "test.spice").write_text(text)
    with (folder / "ngspice.log").open("w") as log:
        result = subprocess.run(
            ["ngspice", "-b", "test.spice"],
            cwd=folder,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    log = (folder / "ngspice.log").read_text()
    errors = [
        line
        for line in log.splitlines()
        if re.search(r"(?i)error|timestep too small|simulation interrupted", line)
    ]
    if result.returncode or errors:
        raise RuntimeError(f"Failed simulation: {folder}")
    time, avdd, dvdd, vref, current = np.loadtxt(folder / "startup.dat", skiprows=1).T
    assert time[-1] >= 0.004 and np.isfinite(vref).all()
    record = {
        "analog_supply_V": supply,
        "digital_supply_V": 3.3,
        "trim_code": 72,
        "both_rails_cycled": True,
        "exit_code": result.returncode,
        "errors": errors,
        "initial_vref_V": float(vref[0]),
        "end_s": float(time[-1]),
        "deck_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "model": "capacitance-conserving RCC approximation",
        "rc_netlist_sha256": hashlib.sha256(
            circuit_file("rcc").read_bytes()
        ).hexdigest(),
    }
    for name, first, last in [("cold", 0.0001, 0.002), ("restart", 0.0026, 0.004)]:
        final = np.mean(vref[(time >= last - 0.0002) & (time <= last)])
        bad = time[
            (time >= first)
            & (time <= last)
            & (np.abs(vref - final) > 0.005 * abs(final))
        ]
        record[f"{name}_final_V"] = float(final)
        record[f"{name}_settling_s_after_ramp"] = float(max(bad, default=first) - first)
    print(json.dumps(record), flush=True)
    return record


with ThreadPoolExecutor(max_workers=2) as pool:
    records = list(pool.map(run_case, [3.3, 5.0]))
(OUT / "summary.json").write_text(json.dumps(records, indent=2) + "\n")
plot_power_sequence(OUT, BUILD_DIR / "figures")

"""Run bounded post-layout screens; this is not the complete qualification suite.

Ground resistance is omitted because Magic's resistor extractor falsely joins
the isolated grounds. MOS diffusion geometry and signal/supply RC are retained.
The original project's explicitly documented MIM compatibility model is used.
"""

from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess

import numpy as np

from layout_paths import BUILD_DIR as ROOT

RESULTS = ROOT / "postlayout"
SOURCE = (ROOT / "netlists/TB10_RESISTOR_TRIM.spice").read_text()
HEADER = SOURCE.split("* TB_PVT_MODELS_BEGIN", 1)[1].split("* TB_PVT_MODELS_END", 1)[0]
TARGET = 1.194
CIRCUITS = {}


def circuit_file(mode):
    if mode == "schematic":
        start = SOURCE.index(".subckt Bandgap_Core_Res ")
        stop = SOURCE.index("\n.ends", start)
        path = RESULTS / "schematic.spice"
        path.write_text(SOURCE[start:stop] + "\n.ends Bandgap_Core_Res\n")
        return path
    if mode == "magic_lvs":
        path = RESULTS / "magic_lvs.spice"
        source = (ROOT / "pex/Bandgap_Core_Res_magic_lvs.spice").read_text()
        path.write_text(source.replace("cap_mim_2f0_m4m5_noshield", "cap_mim_2f0fF"))
        return path
    return ROOT / "pex" / f"Bandgap_Core_Res_{mode}_simulation.spice"


def run_case(case):
    mode, mos, bjt, resistor, supply, transient = case
    label = f"{mode}_{mos}_{bjt}_{resistor}_{supply:g}" + (
        "_startup" if transient else ""
    )
    folder = RESULTS / label
    folder.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256(
        CIRCUITS[mode].read_bytes()
        + HEADER.encode()
        + repr(case).encode()
        + Path(__file__).read_bytes()
    ).hexdigest()
    if (folder / "metrics.json").exists():
        previous = json.loads((folder / "metrics.json").read_text())
        if (
            previous.get("input_sha256") == fingerprint
            and not previous["errors"]
            and previous["exit_code"] == 0
        ):
            return previous
    header = HEADER.replace("ngspice typical", f"ngspice {mos}")
    header = header.replace("bjt_typical", f"bjt_{bjt}")
    header = header.replace("res_typical", f"res_{resistor}")
    lines = [
        f"* Post-layout screen {label}",
        header,
        ".options klu num_threads=1",
        f".include {CIRCUITS[mode]}",
        "XDUT avdd 0 vref b0 b1 b2 b3 b4 b5 b6 0 dvdd Bandgap_Core_Res",
        "VDVDD dvdd 0 3.3",
    ]
    if transient:
        # Cold start, deliberate power-off interval, and restart. No UIC or kick.
        waveform = f"PWL(0 0 100u {supply} 2m {supply} 2.001m 0 2.5m 0 2.6m {supply} 4m {supply})"
        lines.append(f"VDD avdd 0 {waveform}")
    else:
        lines.append(f"VDD avdd 0 {supply} AC 1")
    for bit in range(7):
        lines.append(f"VB{bit} b{bit} 0 {3.3 if 72 & (1 << bit) else 0}")
    control = [
        ".control",
        "set noaskquit",
        "set num_threads=1",
        "set numdgt=15",
        "set wr_singlescale",
        "set wr_vecnames",
        "save v(avdd) v(vref) i(vdd) i(vdvdd)",
    ]
    if transient:
        control += ["tran 1u 4m 0 100n", "wrdata startup.dat v(avdd) v(vref) i(vdd)"]
    else:
        control += [
            "op",
            "wrdata operating.dat v(vref) i(vdd) i(vdvdd)",
            "ac dec 40 1 1Meg",
            "let psrr_db=-db(v(vref))",
            "wrdata psrr.dat psrr_db",
            "dc temp -40 125 1",
            "wrdata temperature.dat v(vref) i(vdd)",
        ]
        # All trim codes at 25 C, same physical layout and digital supply.
        control += ["set temp=25", "unset appendwrite"]
        for code in range(128):
            for bit in range(7):
                control.append(f"alter VB{bit} {3.3 if code & (1 << bit) else 0}")
            control += [
                "op",
                f"let trim_code={code}",
                "wrdata trim.dat trim_code v(vref) i(vdd)",
                "set appendwrite",
            ]
    control += ["quit", ".endc", ".end"]
    (folder / "test.spice").write_text("\n".join(lines + control) + "\n")
    with (folder / "ngspice.log").open("w") as log:
        result = subprocess.run(
            ["ngspice", "-b", "test.spice"],
            cwd=folder,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=300,
        )
    record = dict(
        mode=mode,
        mos=mos,
        bjt=bjt,
        resistor=resistor,
        supply_V=supply,
        transient=transient,
        exit_code=result.returncode,
        path=label,
        input_sha256=fingerprint,
    )
    log = (folder / "ngspice.log").read_text()
    record["errors"] = [
        line
        for line in log.splitlines()
        if re.search(r"(?i)error|timestep too small|simulation interrupted", line)
    ]
    if result.returncode or record["errors"]:
        raise RuntimeError(f"Simulation failed; inspect {folder / 'ngspice.log'}")
    if transient:
        values = np.loadtxt(folder / "startup.dat", skiprows=1)
        time, voltage, reference, current = values.T
        record.update(
            end_s=float(time[-1]),
            peak_V=float(max(reference)),
            final_V=float(np.mean(reference[time >= 0.0038])),
        )
        for name, first, last in [("cold", 0.0001, 0.002), ("restart", 0.0026, 0.004)]:
            mask = (time >= first) & (time <= last)
            final = np.mean(reference[(time >= last - 0.0002) & (time <= last)])
            bad = time[mask & (np.abs(reference - final) > 0.005 * abs(final))]
            record[f"{name}_settling_s_after_ramp"] = float(
                max(bad, default=first) - first
            )
            record[f"{name}_final_V"] = float(final)
    else:
        op = np.loadtxt(folder / "operating.dat", skiprows=1)
        temp = np.loadtxt(folder / "temperature.dat", skiprows=1)
        psrr = np.loadtxt(folder / "psrr.dat", skiprows=1)
        # Each wrdata invocation prints a header when append mode is enabled.
        rows = [
            line
            for line in (folder / "trim.dat").read_text().splitlines()
            if re.match(r"^\s*[-+\d.]", line)
        ]
        trims = np.array([[float(x) for x in line.split()] for line in rows])
        closest = int(np.argmin(np.abs(trims[:, 2] - TARGET)))
        record.update(
            vref_V=float(op[1]),
            idd_uA=float(-op[2] * 1e6),
            idigital_uA=float(-op[3] * 1e6),
            tc_box_ppm_C=float(np.ptp(temp[:, 1]) / TARGET / 165 * 1e6),
            psrr_min_dB=float(np.min(psrr[:, 1])),
            trim_codes=int(len(trims)),
            best_code=int(trims[closest, 1]),
            best_vref_V=float(trims[closest, 2]),
            trim_min_V=float(np.min(trims[:, 2])),
            trim_max_V=float(np.max(trims[:, 2])),
        )
    (folder / "metrics.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pvt", action="store_true")
    args = parser.parse_args()
    RESULTS.mkdir(exist_ok=True)
    for mode in ["schematic", "magic_lvs", "c", "rc"]:
        CIRCUITS[mode] = circuit_file(mode)
    if args.pvt:
        cases = [
            ("rc", mos, bjt, res, supply, False)
            for mos, bjt, res, supply in product(
                ["typical", "ff", "ss", "fs", "sf"],
                ["typical", "ff", "ss"],
                ["typical", "ff", "ss"],
                [3.3, 5.0],
            )
        ]
    else:
        cases = [
            (mode, "typical", "typical", "typical", supply, False)
            for mode, supply in product(
                ["schematic", "magic_lvs", "c", "rc"], [3.3, 5.0]
            )
        ]
        cases += [
            ("rc", "typical", "typical", "typical", supply, True)
            for supply in [3.3, 5.0]
        ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        records = list(pool.map(run_case, cases))
    name = "pvt_summary.json" if args.pvt else "nominal_summary.json"
    (RESULTS / name).write_text(json.dumps(records, indent=2) + "\n")


if __name__ == "__main__":
    main()

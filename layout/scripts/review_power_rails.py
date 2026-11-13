"""Independent before/after evidence; a completed screen is not tapeout signoff.

Runs fresh RCC line/load/noise and both analog-only and simultaneous-rail
startup/restart screens. Every transient has the same waveform, solver options
and time limit on both layouts. Timeouts and specification failures are data,
never silently treated as passes. The original DC/AC/corner results are also
compared point for point. No PDK models or extracted devices are modified.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import subprocess

import numpy as np

BASE_COMMIT = "0818d5f8d65c6bd1c25f599b13a52347e6589f04"
RAIL_COMMIT = "9bb0e18631cc6bb1182dafeda5009b7d5f93b0d7"


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def samples(path, columns, expected=None):
    data = np.loadtxt(path, skiprows=1, ndmin=2)
    if data.shape[1] != columns or not np.isfinite(data).all():
        raise ValueError(f"Invalid finite sample matrix: {path}")
    if expected is not None and len(data) != expected:
        raise ValueError(f"Expected {expected} samples, got {len(data)}: {path}")
    return data


def run_case(case):
    label, root, supply, kind, output, timeout = case
    folder = output / label / f"{kind}_{supply:g}V"
    folder.mkdir(parents=True, exist_ok=False)
    source = (root / "netlists/TB10_RESISTOR_TRIM.spice").read_text()
    header = source.split("* TB_PVT_MODELS_BEGIN", 1)[1].split("* TB_PVT_MODELS_END", 1)[0]
    model = root / "pex/Bandgap_Core_Res_rcc_simulation.spice"
    # ngspice KLU does not support noise analysis; use Sparse for these cases.
    solver = "sparse" if kind == "dc_noise" else "klu"
    lines = [f"* Independent rail review: {label} {kind}", header,
             f".options {solver} num_threads=1", ".temp 25", f'.include "{model}"',
             "XDUT avdd 0 vref b0 b1 b2 b3 b4 b5 b6 0 dvdd Bandgap_Core_Res"]
    def ramp(v):
        return f"PWL(0 0 100u {v} 2m {v} 2.001m 0 2.5m 0 2.6m {v} 4m {v})"
    transient = kind != "dc_noise"
    lines += [f"VDD avdd 0 {ramp(supply) if transient else str(supply) + ' AC 1'}",
              f"VDVDD dvdd 0 {ramp(3.3) if kind == 'both_rails' else '3.3'}",
              "ILOAD vref 0 0"]
    for bit in range(7):
        high = bool(72 & (1 << bit))
        lines.append(f"EB{bit} b{bit} 0 dvdd 0 1" if high else f"VB{bit} b{bit} 0 0")
    lines += [".control", "set noaskquit", "set num_threads=1", "set numdgt=15",
              "set wr_singlescale", "set wr_vecnames", "set temp=25"]
    if transient:
        lines += ["save v(avdd) v(dvdd) v(vref) i(vdd)", "tran 1u 4m 0 100n",
                  "wrdata startup.dat v(avdd) v(dvdd) v(vref) i(vdd)"]
    else:
        lines += ["op", "wrdata operating.dat v(vref) i(vdd)",
                  "noise v(vref) VDD dec 40 1 1Meg", "wrdata noise_integrated.dat onoise_total",
                  "setplot noise1", "wrdata noise_spectrum.dat onoise_spectrum",
                  "dc VDD 3.3 5 0.1", "wrdata line.dat v(vref) i(vdd)",
                  f"alter VDD {supply}", "dc ILOAD 0 10u 1u", "wrdata load.dat v(vref) i(vdd)"]
    lines += ["quit", ".endc", ".end"]
    deck = "\n".join(lines) + "\n"
    (folder / "test.spice").write_text(deck)
    record = {"layout": label, "kind": kind, "supply_V": supply, "temperature_C": 25,
              "trim_code": 72, "rcc_sha256": sha(model), "deck_sha256": sha(folder / "test.spice"),
              "timeout_s": timeout, "solver": solver, "status": "INCOMPLETE", "errors": []}
    try:
        with (folder / "ngspice.log").open("w") as log:
            proc = subprocess.run(["ngspice", "-b", "test.spice"], cwd=folder,
                                  stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
        record["exit_code"] = proc.returncode
        text = (folder / "ngspice.log").read_text()
        record["errors"] = [line for line in text.splitlines()
                            if re.search(r"(?i)error|timestep too small|simulation interrupted|no such vector", line)]
        if proc.returncode or record["errors"]:
            raise ValueError("ngspice error; inspect the retained log")
        if transient:
            a = samples(folder / "startup.dat", 5)
            t, avdd, dvdd, vref, current = a.T
            if t[-1] < .004 or not np.all(np.diff(t) > 0):
                raise ValueError("Incomplete or non-monotonic transient")
            record.update(end_s=float(t[-1]), initial_vref_V=float(vref[0]),
                          peak_vref_V=float(vref.max()), minimum_vref_V=float(vref.min()),
                          peak_supply_current_uA=float(np.max(-current) * 1e6))
            for name, begin, end in [("cold", .0001, .002), ("restart", .0026, .004)]:
                tail = (t >= end - .0002) & (t <= end)
                if not tail.any():
                    raise ValueError("Missing settling tail")
                final = float(np.mean(vref[tail]))
                bad = t[(t >= begin) & (t <= end) & (np.abs(vref - final) > .005 * abs(final))]
                record[name + "_final_V"] = final
                record[name + "_settling_s_after_ramp"] = float(max(bad, default=begin) - begin)
                # A zero-output equilibrium must never count as successful startup.
                record[name + "_functional"] = bool(1.1 < final < 1.3 and np.ptp(vref[tail]) < .005 * abs(final))
            record["functional"] = record["cold_functional"] and record["restart_functional"]
        else:
            op = samples(folder / "operating.dat", 3, 1)
            line = samples(folder / "line.dat", 3, 18)
            load = samples(folder / "load.dat", 3, 11)
            noise = samples(folder / "noise_integrated.dat", 2, 1)
            spectrum = samples(folder / "noise_spectrum.dat", 2, 241)
            if not np.allclose(line[:, 0], np.linspace(3.3, 5, 18)) or not np.allclose(load[:, 0], np.linspace(0, 1e-5, 11), atol=1e-12):
                raise ValueError("Unexpected line/load sweep axes")
            record.update(vref_V=float(op[0, 1]), idd_uA=float(-op[0, 2]*1e6),
                          line_span_uV=float(np.ptp(line[:, 1])*1e6),
                          line_endpoint_uV_per_V=float((line[-1, 1]-line[0, 1])/1.7*1e6),
                          load_span_uV=float(np.ptp(load[:, 1])*1e6),
                          noise_1Hz_1MHz_rms_V=float(noise[0, 1]),
                          noise_10Hz_V_rtHz=float(np.interp(10, spectrum[:, 0], spectrum[:, 1])))
        record["status"] = "COMPLETE"
    except subprocess.TimeoutExpired:
        record["errors"].append("WALL_CLOCK_TIMEOUT: no functional conclusion")
    except (ValueError, OSError) as error:
        record["errors"].append(str(error))
    (folder / "summary.json").write_text(json.dumps(record, indent=2) + "\n")
    print(label, kind, supply, record["status"], record["errors"], flush=True)
    return record


def compare_nominal(base, candidate):
    before = read(base / "postlayout/nominal_dc_summary.json")
    after = read(candidate / "postlayout/nominal_dc_summary.json")
    key = lambda r: (r["mode"], r["supply_V"])
    old = {key(r): r for r in before}
    assert len(before) == len(after) == 10 and set(old) == {key(r) for r in after}
    rows = []
    for r in after:
        b = old[key(r)]
        assert not r["errors"] and not b["errors"] and r["exit_code"] == b["exit_code"] == 0
        changes = {name: {"baseline": b[name], "candidate": r[name], "delta": r[name]-b[name]}
                   for name in ["vref_V", "idd_uA", "tc_box_ppm_C", "psrr_min_dB", "best_code", "trim_min_V", "trim_max_V"]}
        curves = {}
        for filename, count in [("psrr.dat", 241), ("temperature.dat", 166)]:
            x = np.loadtxt(base / "postlayout" / b["path"] / filename, skiprows=1)
            y = np.loadtxt(candidate / "postlayout" / r["path"] / filename, skiprows=1)
            assert x.shape == y.shape and len(x) == count and np.array_equal(x[:, 0], y[:, 0])
            assert np.isfinite(x).all() and np.isfinite(y).all()
            curves[filename] = {"samples": len(x), "minimum_delta": float(np.min(y[:, 1]-x[:, 1])),
                                "maximum_delta": float(np.max(y[:, 1]-x[:, 1]))}
        rows.append({"mode": r["mode"], "supply_V": r["supply_V"], "metrics": changes, "curves": curves})
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=600)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    verification = {}
    for label, root in [("baseline", args.baseline), ("candidate", args.candidate)]:
        v = read(root / "verification.json")
        assert v["drc_violations"] == 0 and v["lvs_match"] and v["expanded_device_units"] == 533
        for name, digest in v["sha256"].items():
            assert sha(root / name) == digest, (label, name)
        verification[label] = v
    for name in ["drc_deck_sha256", "lvs_deck_sha256", "schematic_sha256", "pdk_version"]:
        assert verification["baseline"][name] == verification["candidate"][name]
    summary = {"baseline_commit": BASE_COMMIT, "rail_commit": RAIL_COMMIT,
               "status": "REVIEW_REQUIRED_NOT_TAPEOUT_SIGNOFF", "verification": verification,
               "nominal": compare_nominal(args.baseline, args.candidate)}
    pvt = []
    b = read(args.baseline / "postlayout/pvt_dc/summary.json")
    c = read(args.candidate / "postlayout/pvt_dc/summary.json")
    key = lambda r: (r["mos"], r["bjt"], r["resistor"], r["supply_V"])
    old = {key(r): r for r in b}
    assert len(b) == len(c) == 90 and set(old) == {key(r) for r in c}
    for r in c:
        prior = old[key(r)]
        assert r["exit_code"] == prior["exit_code"] == 0 and not r["errors"] and not prior["errors"]
        assert len(r["points"]) == len(prior["points"]) == 4
        for x, y in zip(prior["points"], r["points"]):
            assert x["temperature_C"] == y["temperature_C"]
            pvt.append({"corner": list(key(r)), "temperature_C": x["temperature_C"],
                        "baseline_vref_V": x["vref_V"], "candidate_vref_V": y["vref_V"],
                        "delta_vref_uV": (y["vref_V"]-x["vref_V"])*1e6,
                        "delta_idd_uA": y["idd_uA"]-x["idd_uA"]})
    summary["pvt"] = pvt
    rails = []
    b = read(args.baseline / "precision_layout_audit.json")["source_resistance"]["rails"]
    c = read(args.candidate / "precision_layout_audit.json")["source_resistance"]["rails"]
    for rail in b:
        assert set(b[rail]) == set(c[rail])
        for name in b[rail]:
            old_r = b[rail][name]["mean_individual_source_to_port_ohm"]
            new_r = c[rail][name]["mean_individual_source_to_port_ohm"]
            rails.append({"rail": rail, "device": name, "baseline_ohm": old_r,
                          "candidate_ohm": new_r, "change_percent": 100*(new_r/old_r-1)})
    summary["source_resistance"] = rails
    (args.output / "comparison.json").write_text(json.dumps(summary, indent=2)+"\n")
    cases = [(label, root, v, kind, args.output, args.timeout)
             for kind in ["dc_noise", "analog_only", "both_rails"]
             for label, root in [("baseline", args.baseline), ("candidate", args.candidate)]
             for v in [3.3, 5.0]]
    with ThreadPoolExecutor(max_workers=4) as pool:
        summary["additional_screens"] = list(pool.map(run_case, cases))
    summary["incomplete_screens"] = sum(r["status"] != "COMPLETE" for r in summary["additional_screens"])
    summary["startup_functional_failures"] = [r for r in summary["additional_screens"] if r.get("functional") is False]
    (args.output / "comparison.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps({"pvt_points": len(pvt), "incomplete_screens": summary["incomplete_screens"],
                      "startup_functional_failures": len(summary["startup_functional_failures"])}), flush=True)


if __name__ == "__main__":
    main()

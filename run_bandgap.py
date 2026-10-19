#!/usr/bin/env python3
"""Run and report the ten GF180 bandgap testbenches.

The command-line entry point is main(), at the end of this file. It loads
bandgap_config.json, validates the netlist and model dependencies, schedules
cases, and writes reports. Test-specific helpers build SPICE decks and parse
measurements; run_case() and run_trim_case() own simulator execution.

Only the Python standard library is required. Run under Linux/FOSS because
output-directory locking uses fcntl. The separate plot_bandgap.py renders data.
Resume accepts only an exact fingerprint match for the current source, runner,
configuration, simulator and model files. Historical runners live in Git,
not in encoded payloads inside this program.
"""

import argparse
import cmath
import concurrent.futures
import contextlib
import fcntl
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
TESTS = (
    "TB01_PSRR",
    "TB02_LINE_REGULATION",
    "TB03_STARTUP_RESTART",
    "TB04_TEMPERATURE",
    "TB05_LOOP_STABILITY",
    "TB06_MONTE_CARLO",
    "TB07_NOISE",
    "TB08_POWER_DEVICE_LIMITS",
    "TB09_SUPPLY_DISTURBANCE",
    "TB10_RESISTOR_TRIM",
)
TC, LG, MC, NOISE, POWER, DISTURB, TRIM = TESTS[3:]
MIM_FACTORS = {"mimcap_typical": 1.0, "mimcap_ff": 0.9, "mimcap_ss": 1.1}
ALLOWED = {
    "mos_corners": {"typical", "ff", "ss", "fs", "sf"},
    "bjt_corners": {"bjt_typical", "bjt_ff", "bjt_ss"},
    "res_corners": {"res_typical", "res_ff", "res_ss"},
    "mim_corners": set(MIM_FACTORS),
}


def atomic_json(path, data):
    """Replace a result file only after a complete JSON document is written."""
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def _base_validate_config(config):
    """Validate shared PVT axes, line sweep bounds and scheduling settings."""
    for key, allowed in ALLOWED.items():
        if (
            not config[key]
            or len(set(config[key])) != len(config[key])
            or not set(config[key]) <= allowed
        ):
            raise ValueError("Invalid or duplicate values for " + key)
    for key in ["temperatures_C", "supply_voltages_V"]:
        if not config[key] or not all(
            isinstance(v, (float, int)) and math.isfinite(v) for v in config[key]
        ):
            raise ValueError("Invalid " + key)
        if len(config[key]) != len(set(config[key])):
            raise ValueError("Duplicate " + key)
    if min(config["supply_voltages_V"]) <= 0:
        raise ValueError("Supply sweep must contain positive voltages")
    a, b, h = (config[k] for k in ["line_start_V", "line_stop_V", "line_step_V"])
    if not (0 < a < b and h > 0 and h <= b - a):
        raise ValueError("Invalid line sweep")
    if abs((b - a) / h - round((b - a) / h)) > 1e-6:
        raise ValueError(
            "Line endpoints must be separated by an integer number of steps"
        )
    if not a <= config["nominal_supply_min_V"] < config["nominal_supply_max_V"] <= b:
        raise ValueError("Line sweep must contain the full nominal range")
    if int(config["parallel_jobs"]) < 1 or config["case_timeout_seconds"] <= 0:
        raise ValueError("Invalid jobs/timeout")


def _base_cases(config, test):
    """Yield fixed-corner cases with stable identifiers for reports and resume."""
    axes = [
        config[k]
        for k in [
            "mos_corners",
            "bjt_corners",
            "res_corners",
            "mim_corners",
            "temperatures_C",
        ]
    ]
    for mos, bjt, res, mim, temp in itertools.product(*axes):
        for vdd in (config["supply_voltages_V"] if test != TESTS[1] else [None]):
            d = dict(mos=mos, bjt=bjt, res=res, mim=mim, temperature_C=temp, vdd_V=vdd)
            d["range"] = (
                "nominal_and_stress_sweep"
                if vdd is None
                else (
                    "nominal"
                    if config["nominal_supply_min_V"]
                    <= vdd
                    <= config["nominal_supply_max_V"]
                    else "stress"
                )
            )
            tag = f"{mos}_{bjt}_{res}_{mim}_T{temp:g}"
            if vdd is not None:
                tag += f"_V{vdd:g}"
            d["case_id"] = tag.replace(".", "p").replace("-", "m")
            yield d


def _base_prepare_source(deck):
    """Read an Xschem netlist and resolve its directly included model files."""
    s = deck.read_text()
    # Read the original Xschem netlist, not ngspice's expanded listing.
    if re.search(r"^\s*\.control\b", s, re.M | re.I):
        s = re.sub(
            r"^\s*\.control\b.*?^\s*\.endc\b[^\n]*", "", s, flags=re.M | re.S | re.I
        )
    s = re.sub(r"^\s*\.end\s*$", "", s, flags=re.M | re.I)
    if len(re.findall(r"^VDD\s+", s, re.M | re.I)) != 1:
        raise ValueError("Expected exactly one top-level VDD source in the netlist")
    if ".subckt cap_mim_2f0ff" not in s.lower():
        raise ValueError("Embedded cap_mim_2f0fF compatibility model is missing")
    # Corners are switched by replacing actual .lib statements in a NEW deck.
    # .lib inside parameter-controlled .if is deliberately not used.
    paths = []
    for family in ["typical", "bjt_typical", "res_typical"]:
        hits = re.findall(r"^\s*\.lib\s+(.+?)\s+" + family + r"\s*$", s, re.M | re.I)
        if len(hits) != 1:
            raise ValueError("Expected one bootstrap .lib section: " + family)
        p = Path(hits[0].strip().strip("\"'"))
        if not p.is_absolute():
            p = (deck.parent / p).resolve()
        if not p.is_file():
            raise FileNotFoundError(p)
        paths.append(p)
    for inc in re.findall(r"^\s*\.include\s+(.+?)\s*$", s, re.M | re.I):
        p = Path(inc.strip().strip("\"'"))
        if not p.is_absolute():
            p = (deck.parent / p).resolve()
        if not p.is_file():
            raise FileNotFoundError(p)
        paths.append(p)

    # Absolute includes remain valid when cases run from their own directories.
    def abs_inc(m):
        token = m[2].strip().strip("\"'")
        p = Path(token)
        if not p.is_absolute():
            p = (deck.parent / p).resolve()
        return m[1] + '"' + str(p) + '"' + (m[3] or "")

    s = re.sub(
        r"^(\s*\.lib\s+)(.+?)(\s+(?:typical|bjt_typical|res_typical))\s*$",
        abs_inc,
        s,
        flags=re.M | re.I,
    )
    s = re.sub(r"^(\s*\.include\s+)(.+?)()\s*$", abs_inc, s, flags=re.M | re.I)
    return s, list(dict.fromkeys(paths))


def _fixed_corner_source(source, case, test):
    """Apply one case's model corners, temperature and supply to a netlist copy."""
    s = source
    for initial, key in [
        ("typical", "mos"),
        ("bjt_typical", "bjt"),
        ("res_typical", "res"),
    ]:
        s, n = re.subn(
            r"^(\s*\.lib\s+.+?\s+)" + initial + r"\s*$",
            lambda m: m[1] + case[key],
            s,
            flags=re.M | re.I,
        )
        if n != 1:
            raise ValueError("Ambiguous model section replacement: " + initial)
    s, n = re.subn(
        r"mim_corner_2p0fF\s*=\s*1(?:\.0)?\b",
        "mim_corner_2p0fF=" + str(MIM_FACTORS[case["mim"]]),
        s,
        flags=re.I,
    )
    if n != 1:
        raise ValueError("Expected one nominal MIM corner parameter assignment")
    s = re.sub(
        r"^\s*\.temp\s+[^\n]*",
        ".temp " + str(case["temperature_C"]),
        s,
        flags=re.M | re.I,
    )
    v = case["vdd_V"] if case["vdd_V"] is not None else 5.0
    if test == TESTS[2]:
        supply = f"PWL(0 0 1u 0 101u {v} 4m {v} 4001u 0 4500u 0 4501u {v} 6m {v})"
    else:
        supply = f"DC {v} AC " + ("1" if test == TESTS[0] else "0")
    s = re.sub(r"^VDD\s+[^\n]*", "VDD avdd 0 " + supply, s, flags=re.M | re.I)
    return s


def _base_control_for_case(template, test, config):
    """Build the PSRR, line-regulation or startup measurement commands."""
    out = "case_report.txt"
    pre = f""".control
set noaskquit
set numdgt=15
set wr_vecnames
set wr_singlescale
unset appendwrite
echo PVT_SINGLE_CASE > {out}
set appendwrite
"""
    if test == TESTS[1]:
        body = f"""save v(avdd) v(vref) i(vdd)
dc vdd {config['line_start_V']} {config['line_stop_V']} {config['line_step_V']}
let VREF_V=v(vref)
let IDD_UA=-i(vdd)*1e6
echo BEGIN_DC_DATA >> {out}
wrdata {out} VREF_V IDD_UA
echo END_DC_DATA >> {out}
echo END_OF_TB02_LINE_REGULATION_RUN >> {out}
"""
    else:
        body = template[template.index("save v(avdd)") : template.rindex(".endc")]
        body = re.sub(
            r"/foss/designs/chipalooza-bandgap/individual_tests/\S+_UPLOAD_THIS\.txt",
            out,
            body,
        )
    return pre + body + "\nquit\n.endc\n.end\n"


def waveform_rows(text):
    active = False
    rows = []
    for line in text.splitlines():
        if line.startswith(("BEGIN_AC_DATA", "BEGIN_DC_DATA", "BEGIN_TRAN_DATA")):
            active = True
            continue
        if line.startswith(("END_AC_DATA", "END_DC_DATA", "END_TRAN_DATA")):
            active = False
        if active:
            try:
                values = [float(x) for x in line.split()]
            except ValueError:
                continue
            if len(values) >= 3:
                if not all(math.isfinite(x) for x in values):
                    raise ValueError("Nonfinite waveform data")
                rows.append(values)
    return rows


def line_metrics(rows, lo, hi):
    points = [r for r in rows if lo - 1e-8 <= r[0] <= hi + 1e-8]
    if (
        len(points) < 2
        or abs(points[0][0] - lo) > 1e-7
        or abs(points[-1][0] - hi) > 1e-7
    ):
        raise ValueError("Line sweep does not include requested range endpoints")
    vs = [r[1] for r in points]
    span = max(vs) - min(vs)
    slopes = [(b[1] - a[1]) / (b[0] - a[0]) * 1e3 for a, b in zip(points, points[1:])]
    return {
        "vdd_min_V": lo,
        "vdd_max_V": hi,
        "vref_min_V": min(vs),
        "vref_max_V": max(vs),
        "vref_at_low_V": vs[0],
        "vref_at_high_V": vs[-1],
        "vref_span_mV": span * 1e3,
        "span_percent_of_1p194V": span / 1.194 * 100,
        "span_mV_per_V": span * 1e3 / (hi - lo),
        "endpoint_slope_mV_per_V": (vs[-1] - vs[0]) * 1e3 / (hi - lo),
        "max_abs_adjacent_slope_mV_per_V": max(map(abs, slopes)),
        "max_IDD_uA": max(r[2] for r in points),
        "all_points_within_0p5pct_of_1p194V": all(
            abs(v - 1.194) <= 0.005 * 1.194 for v in vs
        ),
    }


def _base_read_result(text, test, config):
    if "END_OF_" + test + "_RUN" not in text:
        raise ValueError("Missing completion marker (simulation may have aborted)")
    metrics = {
        k: float(v)
        for k, v in re.findall(r"^([a-z0-9_]+)\s*=\s*([-+0-9.eE]+)$", text, re.M)
    }
    rows = waveform_rows(text)
    if not rows:
        raise ValueError("No waveform data exported")
    if test == TESTS[0]:
        if abs(rows[0][0] - 1) > 1e-8 or abs(rows[-1][0] - 1e8) > 1:
            raise ValueError("Incomplete AC sweep")
        band = [r for r in rows if r[0] <= 1e6 * (1 + 1e-9)]
        worst = min(band, key=lambda r: r[1])
        metrics.update(psrr_min_1hz_1mhz_db=worst[1], psrr_min_frequency_Hz=worst[0])
        required = ["op_valid", "op_vref_v", "op_idd_ua", "psrr_1mhz_db", "psrr_pass"]
    elif test == TESTS[1]:
        metrics = {
            "nominal": line_metrics(
                rows, config["nominal_supply_min_V"], config["nominal_supply_max_V"]
            ),
            "full_stress_sweep": line_metrics(
                rows, config["line_start_V"], config["line_stop_V"]
            ),
        }
        required = []
    else:
        if abs(rows[-1][0] - 0.006) > 1e-9:
            raise ValueError("Incomplete transient export")
        required = [
            "sim_end_s",
            "pre_vref_v",
            "final_vref_v",
            "start_ready_300us",
            "restart_ready_300us",
            "start_peak_v",
            "restart_peak_v",
            "pre_valid",
            "final_valid",
        ]
    if any(k not in metrics for k in required):
        raise ValueError("Missing required measurements")
    return metrics


def run_case(
    case, *, root, source, template, test, config, fingerprint, ngspice, retry_failed
):
    """Reuse an exact cache match or run, parse and save one simulation case."""
    if test == TRIM:
        return run_trim_case(
            case,
            root=root,
            source=source,
            config=config,
            fingerprint=fingerprint,
            ngspice=ngspice,
            retry_failed=retry_failed,
        )
    case_directory = root / "cases" / case["case_id"]
    case_directory.mkdir(parents=True, exist_ok=True)
    cache = case_directory / "result.json"
    if config["resume"] and cache.exists():
        cached_result = json.loads(cache.read_text())
        if cached_result.get("fingerprint") == fingerprint and (
            cached_result.get("status") == "complete" or not retry_failed
        ):
            return dict(cached_result, resumed=True)
    # Remove only this case's prior transient artifacts, so failure cannot reuse stale metrics.
    for name in ["case_report.txt", "waveforms.txt.gz", "run.log", "circuit.spice"]:
        (case_directory / name).unlink(missing_ok=True)
    result = dict(case, fingerprint=fingerprint, status="failed", resumed=False)
    start = time.monotonic()
    try:
        case_config = dict(config, _case=case)
        deck = prepare_case_deck(source, case, test, config, template)
        (case_directory / "circuit.spice").write_text(deck)
        with (case_directory / "run.log").open("w") as log:
            process = subprocess.run(
                [ngspice, "-b", str(case_directory / "circuit.spice")],
                cwd=case_directory,
                stdout=log,
                stderr=subprocess.STDOUT,
                timeout=config["case_timeout_seconds"],
            )
        log = (case_directory / "run.log").read_text(errors="replace")
        if process.returncode != 0:
            raise ValueError("ngspice exit " + str(process.returncode))
        if re.search(
            r"(?im)^\s*Error:|^\s*ERROR:|Warning from checkvalid|failed!|TRANSIENT_ABORTED",
            log,
        ):
            raise ValueError("ngspice analysis or measurement error; see run.log")
        warning_lines = [
            line.strip()
            for line in log.splitlines()
            if re.search(r"(?i)warning|unsupported|not recognized", line)
        ]
        result["simulator_warnings"] = list(dict.fromkeys(warning_lines))[:30]
        result["metrics"] = read_result(
            (case_directory / "case_report.txt").read_text(), test, case_config
        )
        # Compressed full traces remain plottable even when deck/log retention is off.
        import gzip

        with gzip.open(case_directory / "waveforms.txt.gz", "wt") as waveform_file:
            waveform_file.write((case_directory / "case_report.txt").read_text())
        result["status"] = "complete"
        if not config["retain_waveforms"]:
            (case_directory / "case_report.txt").unlink()
            (case_directory / "circuit.spice").unlink()
            (case_directory / "run.log").unlink()
    except subprocess.TimeoutExpired:
        result["status"] = "timeout"
        result["error"] = (
            "Exceeded case_timeout_seconds; this is not a performance pass"
        )
    except Exception as exc:
        result["error"] = str(exc)
    result["elapsed_seconds"] = round(time.monotonic() - start, 3)
    if result["status"] != "complete":
        log_path = case_directory / "run.log"
        result["log_tail"] = (
            log_path.read_text(errors="replace")[-1800:] if log_path.exists() else ""
        )
    atomic_json(cache, result)
    return result


def _base_summarize(results, test):
    complete = [r for r in results if r["status"] == "complete"]
    summary = {
        "attempted": len(results),
        "simulation_complete": len(complete),
        "execution_failed_or_timed_out": len(results) - len(complete),
    }
    if test == TESTS[0]:
        for group in ["nominal", "stress"]:
            rr = [r for r in complete if r["range"] == group]
            if rr:
                worst = min(rr, key=lambda r: r["metrics"]["psrr_min_1hz_1mhz_db"])
                summary[group] = {
                    "cases": len(rr),
                    "psrr_pass_count": sum(r["metrics"]["psrr_pass"] == 1 for r in rr),
                    "minimum_PSRR_dB": worst["metrics"]["psrr_min_1hz_1mhz_db"],
                    "worst_case_id": worst["case_id"],
                }
    elif test == TESTS[1]:
        for group in ["nominal", "full_stress_sweep"]:
            if complete:
                worst = max(complete, key=lambda r: r["metrics"][group]["vref_span_mV"])
                summary[group] = {
                    "maximum_span_mV": worst["metrics"][group]["vref_span_mV"],
                    "worst_case_id": worst["case_id"],
                }
    else:
        for group in ["nominal", "stress"]:
            rr = [r for r in complete if r["range"] == group]
            if rr:
                summary[group] = {
                    "cases": len(rr),
                    "startup_ready_count": sum(
                        r["metrics"]["start_ready_300us"] == 1 for r in rr
                    ),
                    "restart_ready_count": sum(
                        r["metrics"]["restart_ready_300us"] == 1 for r in rr
                    ),
                    "maximum_start_peak_V": max(
                        r["metrics"]["start_peak_v"] for r in rr
                    ),
                    "maximum_restart_peak_V": max(
                        r["metrics"]["restart_peak_v"] for r in rr
                    ),
                }
    return summary


def select_cases(planned, ids_path=None, max_cases=None):
    """Validate an optional case list and preserve the planned execution order."""
    selected = planned
    if ids_path is not None:
        ids = [
            line.strip()
            for line in Path(ids_path).read_text().splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("Case selection must contain unique nonempty case IDs")
        unknown = set(ids) - {c["case_id"] for c in planned}
        if unknown:
            raise ValueError(
                "Unknown case IDs for this test/profile: "
                + ", ".join(sorted(unknown)[:5])
            )
        wanted = set(ids)
        selected = [c for c in planned if c["case_id"] in wanted]
    return selected[:max_cases] if max_cases is not None else selected


# Temperature and conditional loop analyses.


def validate_loop_dut_connection(source):
    """Reject the supplied disconnected TB05 probes before scheduling a sweep."""
    top = []
    depth = 0
    for line in source.splitlines():
        tokens = line.split()
        if not tokens or tokens[0].startswith("*"):
            continue
        if tokens[0].lower() == ".subckt":
            depth += 1
        elif tokens[0].lower() == ".ends":
            depth = max(0, depth - 1)
        elif depth == 0:
            top.append([x.lower() for x in tokens])
    if not any(t[0] == "vlg_main" for t in top):
        return
    ports = {"lg_main_e", "lg_main_f", "lg_bias_e", "lg_bias_f"}
    if not any(t[0].startswith("x") and ports.issubset(set(t[1:])) for t in top):
        raise ValueError(
            "TB05_LOOP_PROBES_NOT_CONNECTED: the DUT instance must connect "
            "lg_main_e, lg_main_f, lg_bias_e and lg_bias_f to the intended "
            "loop-probe core. The supplied three-pin Bandgap_Core and this "
            "runner do not insert those cuts. No loop sweep was started."
        )


def prepare_source(deck):
    """Check loop-probe connections before preparing the original netlist."""
    validate_loop_dut_connection(deck.read_text())
    source, paths = _base_prepare_source(deck)
    return source, paths


def _validate_temperature_loop_config(config):
    """Validate shared settings plus temperature and loop-analysis sweeps."""
    _base_validate_config(config)
    a, b, h = (
        config[k]
        for k in [
            "temperature_sweep_start_C",
            "temperature_sweep_stop_C",
            "temperature_sweep_step_C",
        ]
    )
    if not a < b or h <= 0 or abs((b - a) / h - round((b - a) / h)) > 1e-7:
        raise ValueError("Invalid dense temperature sweep")
    if not a <= 25 <= b or abs((25 - a) / h - round((25 - a) / h)) > 1e-7:
        raise ValueError("Temperature sweep must include exactly 25 C")
    if (
        not 0 < config["loop_start_Hz"] < config["loop_stop_Hz"]
        or config["loop_points_per_decade"] < 20
    ):
        raise ValueError("Invalid loop frequency sweep")


def _tb01_to_tb05_cases(config, test):
    """Generate the temperature sweep or ordinary fixed-corner cases."""
    if test != TC:
        yield from _base_cases(config, test)
        return
    axes = [
        config[k] for k in ["mos_corners", "bjt_corners", "res_corners", "mim_corners"]
    ]
    for mos, bjt, res, mim in itertools.product(*axes):
        for v in config["supply_voltages_V"]:
            yield dict(
                mos=mos,
                bjt=bjt,
                res=res,
                mim=mim,
                temperature_C=25,
                vdd_V=v,
                temperature_sweep_C=[
                    config["temperature_sweep_start_C"],
                    config["temperature_sweep_stop_C"],
                    config["temperature_sweep_step_C"],
                ],
                range=(
                    "nominal"
                    if config["nominal_supply_min_V"]
                    <= v
                    <= config["nominal_supply_max_V"]
                    else "stress"
                ),
                case_id=f"{mos}_{bjt}_{res}_{mim}_Tsweep_V{v:g}".replace(".", "p"),
            )


def _tb01_to_tb05_control(template, test, config):
    """Select the measurement commands for the first five testbenches."""
    if test not in (TC, LG):
        return _base_control_for_case(template, test, config)
    out = "case_report.txt"
    s = f""".control
set noaskquit
set numdgt=15
set wr_vecnames
set wr_singlescale
unset appendwrite
echo {test}_SINGLE_CASE > {out}
set appendwrite
"""
    if test == TC:
        s += f"""save v(avdd) v(vref) i(vdd)
dc temp {config['temperature_sweep_start_C']} {config['temperature_sweep_stop_C']} {config['temperature_sweep_step_C']}
let IDD_UA=-i(vdd)*1e6
echo BEGIN_TC_DATA >> {out}
wrdata {out} v(vref) IDD_UA
echo END_TC_DATA >> {out}
"""
    else:
        s += f"""save v(avdd) v(vref) v(lg_main_e) v(lg_main_f) v(lg_bias_e) v(lg_bias_f) i(vdd) i(VLG_MAIN) i(VLG_BIAS)
op
let OP_VREF_V=v(vref)
let OP_IDD_UA=-i(vdd)*1e6
let MAIN_DC_ERROR_V=v(lg_main_e)-v(lg_main_f)
let BIAS_DC_ERROR_V=v(lg_bias_e)-v(lg_bias_f)
print OP_VREF_V OP_IDD_UA MAIN_DC_ERROR_V BIAS_DC_ERROR_V >> {out}
let OP_REF_ERROR_V=abs(OP_VREF_V-1.194)
if OP_REF_ERROR_V gt 0.005*1.194
  echo LOOP_OPERATING_POINT_OUTSIDE_REFERENCE_TARGET_NO_MARGIN_ACCEPTED >> {out}
  echo LOOP_OPERATING_POINT_OUTSIDE_REFERENCE_TARGET_CHECK_DC_AND_STARTUP
  quit
end
"""
        for cut in ["main", "bias"]:
            for mode in ["voltage", "current"]:
                for other in ["MAIN", "BIAS"]:
                    s += f"alter VLG_{other} acmag=0\nalter ILG_{other} acmag=0\n"
                s += f'alter {"VLG" if mode=="voltage" else "ILG"}_{cut.upper()} acmag=1\n'
                s += f"""ac dec {config['loop_points_per_decade']} {config['loop_start_Hz']} {config['loop_stop_Hz']}
echo BEGIN_{cut.upper()}_{mode.upper()} >> {out}
wrdata {out} real(v(lg_{cut}_e)) imag(v(lg_{cut}_e)) real(v(lg_{cut}_f)) imag(v(lg_{cut}_f)) real(i(VLG_{cut.upper()})) imag(i(VLG_{cut.upper()}))
echo END_{cut.upper()}_{mode.upper()} >> {out}
"""
    return s + f"echo END_OF_{test}_RUN >> {out}\nquit\n.endc\n.end\n"


def blocks(text):
    """Parse named numeric waveform blocks from the simulator report."""
    out = {}
    active = None
    for line in text.splitlines():
        if line.startswith("BEGIN_"):
            active = line[6:].strip()
            out[active] = []
        elif line.startswith("END_"):
            active = None
        elif active:
            try:
                row = [float(x) for x in line.split()]
            except ValueError:
                continue
            if len(row) >= 3:
                if not all(math.isfinite(x) for x in row):
                    raise ValueError("Nonfinite waveform data")
                out[active].append(row)
    return out


def temperature_metrics(rows, config):
    lo, hi, step = [
        config[k]
        for k in [
            "temperature_sweep_start_C",
            "temperature_sweep_stop_C",
            "temperature_sweep_step_C",
        ]
    ]
    n = round((hi - lo) / step) + 1
    if len(rows) != n or any(
        abs(r[0] - (lo + i * step)) > 1e-6 for i, r in enumerate(rows)
    ):
        raise ValueError("Incomplete or incorrect temperature grid")
    vs = [r[1] for r in rows]
    mean = sum(vs) / n
    if mean <= 0:
        raise ValueError("Nonpositive mean reference")
    v25 = rows[round((25 - lo) / step)][1]
    tc = (max(vs) - min(vs)) / mean / (hi - lo) * 1e6
    err = max(abs(v - 1.194) / 1.194 * 100 for v in vs)
    return dict(
        temperature_min_C=lo,
        temperature_max_C=hi,
        temperature_step_C=step,
        points=n,
        vref_at_25C_V=v25,
        vref_min_V=min(vs),
        vref_max_V=max(vs),
        vref_mean_V=mean,
        temperature_at_vref_min_C=rows[vs.index(min(vs))][0],
        temperature_at_vref_max_C=rows[vs.index(max(vs))][0],
        TC_box_ppm_per_C=tc,
        TC_normalized_to_1p194V_ppm_per_C=(max(vs) - min(vs)) / 1.194 / (hi - lo) * 1e6,
        max_absolute_error_percent=err,
        accuracy_pass=err <= 0.5,
        TC_pass=tc <= 10,
        max_IDD_uA=max(r[2] for r in rows),
        IDD_60uA_pass=max(r[2] for r in rows) <= 60,
        temperature_accuracy_pass=(err <= 0.5 and tc <= 10),
    )


def loop_gain(voltage, current):
    if len(voltage) != len(current) or not voltage:
        raise ValueError("Missing or mismatched injection sweeps")
    values = []
    conditions = []
    for a, b in zip(voltage, current):
        if len(a) != 7 or len(b) != 7 or abs(a[0] - b[0]) > 1e-7 * max(a[0], 1):
            raise ValueError("Injection data mismatch")
        ev, fv, iv = complex(a[1], a[2]), complex(a[3], a[4]), complex(a[5], a[6])
        ei, fi, ii = complex(b[1], b[2]), complex(b[3], b[4]), complex(b[5], b[6])
        det = ev * fi - ei * fv
        scale = math.hypot(abs(ev), abs(fv)) * math.hypot(abs(ei), abs(fi))
        conditioning = abs(det) / max(scale, 1e-300)
        conditions.append(conditioning)
        if conditioning < 1e-12 or det == 0:
            raise ValueError(
                "Ill-conditioned two-port reconstruction at " + str(a[0]) + " Hz"
            )
        yee = (-iv * fi - (1 - ii) * fv) / det
        yef = ((1 - ii) * ev + iv * ei) / det
        yfe = (iv * fi - ii * fv) / det
        yff = (ii * ev - iv * ei) / det
        if abs(yee + yff) < 1e-20:
            raise ValueError("Near-zero two-port loop-gain denominator")
        gain = (yef + yfe) / (yee + yff)
        if not math.isfinite(gain.real) or not math.isfinite(gain.imag):
            raise ValueError("Nonfinite loop gain")
        values.append((a[0], gain))
    return values, min(conditions)


def loop_metrics(values, conditioning, config):
    freq = [x[0] for x in values]
    g = [x[1] for x in values]
    if (
        abs(freq[0] / config["loop_start_Hz"] - 1) > 1e-6
        or abs(freq[-1] / config["loop_stop_Hz"] - 1) > 1e-6
    ):
        raise ValueError("Incomplete loop frequency sweep")
    db = [20 * math.log10(max(abs(z), 1e-300)) for z in g]
    phase = []
    for z in g:
        p = math.degrees(cmath.phase(z))
        if phase:
            while p - phase[-1] > 180:
                p -= 360
            while p - phase[-1] < -180:
                p += 360
        phase.append(p)
    # Report each crossing. PM is the distance to the next -180+360k phase boundary
    # on a lagging continuation; positive-feedback LF branches are flagged for review.
    unity = []
    gm = []
    for i in range(len(freq) - 1):
        d0, d1 = db[i : i + 2]
        p0, p1 = phase[i : i + 2]
        if (d0 >= 0 > d1) or (d0 < 0 <= d1):
            w = -d0 / (d1 - d0)
            f = math.exp(math.log(freq[i]) + w * math.log(freq[i + 1] / freq[i]))
            p = p0 + w * (p1 - p0)
            principal = (p + 180) % 360 - 180
            # Conventional PM branch around a negative-feedback 0-degree LF loop.
            pm = 180 + p
            unity.append(
                dict(
                    frequency_Hz=f,
                    gain_direction="down" if d0 > d1 else "up",
                    unwrapped_phase_deg=p,
                    phase_margin_deg=pm,
                    principal_phase_deg=principal,
                )
            )
        if p1 != p0:
            lo, hi = sorted((p0, p1))
            k0 = math.ceil((lo + 180) / 360)
            k1 = math.floor((hi + 180) / 360)
            for k in range(k0, k1 + 1):
                target = -180 + 360 * k
                if not (lo <= target < hi):
                    continue
                w = (target - p0) / (p1 - p0)
                f = math.exp(math.log(freq[i]) + w * math.log(freq[i + 1] / freq[i]))
                gm.append(
                    dict(
                        frequency_Hz=f,
                        phase_deg=target,
                        gain_margin_dB=-(d0 + w * (d1 - d0)),
                    )
                )
    low_phase = phase[0]
    conventional = (
        abs(low_phase) < 45
        and db[0] > 0
        and len(unity) == 1
        and unity[0]["gain_direction"] == "down"
    )
    sampled = min(range(len(g)), key=lambda i: abs(1 + g[i]))
    if conventional:
        status = "CONVENTIONAL_SINGLE_CROSSING_CONDITIONAL_MARGIN"
    elif not unity:
        status = "NO_UNITY_CROSSING_IN_SWEEP_NO_PHASE_MARGIN"
    else:
        status = "MULTIPLE_CROSSINGS_OR_NONSTANDARD_PHASE_REVIEW_REQUIRED"
    # Sample a compact curve into the consolidated upload file; full curves can be retained.
    indices = sorted(
        set(
            [0, len(g) - 1, sampled]
            + list(range(0, len(g), max(1, config["loop_points_per_decade"] // 2)))
        )
    )
    samples = [
        dict(
            frequency_Hz=freq[i],
            gain_dB=db[i],
            phase_deg=phase[i],
            real=g[i].real,
            imag=g[i].imag,
        )
        for i in indices
    ]
    return dict(
        status=status,
        low_frequency_gain_dB=db[0],
        low_frequency_phase_deg=low_phase,
        unity_crossings=unity,
        phase_crossings=gm,
        phase_margin_deg=unity[0]["phase_margin_deg"] if conventional else None,
        minimum_gain_margin_dB=(
            min((x["gain_margin_dB"] for x in gm), default=None)
            if conventional
            else None
        ),
        low_frequency_positive_feedback_gain_headroom_dB=(
            -db[0] if abs(abs(low_phase) - 180) < 1 else None
        ),
        minimum_sampled_abs_1_plus_L=abs(1 + g[sampled]),
        frequency_at_min_abs_1_plus_L_Hz=freq[sampled],
        minimum_normalized_injection_determinant=conditioning,
        whole_network_stability_certified=False,
        curve_samples=samples,
    )


def _read_tb01_to_tb05_result(text, test, config):
    if test not in (TC, LG):
        return _base_read_result(text, test, config)
    if test == LG and "LOOP_OPERATING_POINT_OUTSIDE_REFERENCE_TARGET" in text:
        raise ValueError(
            "Loop operating point outside the configured bench reference target (1.194 V +/-0.5%); inspect DC/startup before accepting margins"
        )
    if "END_OF_" + test + "_RUN" not in text:
        raise ValueError("Missing completion marker")
    data = blocks(text)
    if test == TC:
        return temperature_metrics(data.get("TC_DATA", []), config)
    op = {
        k: float(v)
        for k, v in re.findall(r"^([a-z0-9_]+)\s*=\s*([-+0-9.eE]+)$", text, re.M)
    }
    for k in ["op_vref_v", "op_idd_ua", "main_dc_error_v", "bias_dc_error_v"]:
        if k not in op:
            raise ValueError("Missing " + k)
    if max(abs(op["main_dc_error_v"]), abs(op["bias_dc_error_v"])) > 1e-9:
        raise ValueError("Probe changed DC voltage continuity")
    op["reference_accuracy_valid"] = abs(op["op_vref_v"] - 1.194) <= 0.005 * 1.194
    op["loops"] = {}
    for cut in ["MAIN", "BIAS"]:
        vals, cond = loop_gain(
            data.get(cut + "_VOLTAGE", []), data.get(cut + "_CURRENT", [])
        )
        op["loops"][cut.lower()] = loop_metrics(vals, cond, config)
    op["scope"] = (
        "Two conditional loop cuts with other feedback paths closed; no blanket stability pass"
    )
    return op


def _summarize_tb01_to_tb05(results, test):
    if test not in (TC, LG):
        return _base_summarize(results, test)
    ok = [r for r in results if r["status"] == "complete"]
    s = dict(
        attempted=len(results),
        simulation_complete=len(ok),
        execution_failed_or_timed_out=len(results) - len(ok),
    )
    for group in ["nominal", "stress"]:
        rr = [r for r in ok if r["range"] == group]
        if not rr:
            continue
        if test == TC:
            worst = max(rr, key=lambda r: r["metrics"]["TC_box_ppm_per_C"])
            s[group] = dict(
                cases=len(rr),
                TC_accuracy_pass_count=sum(
                    r["metrics"]["temperature_accuracy_pass"] for r in rr
                ),
                maximum_TC_ppm_per_C=worst["metrics"]["TC_box_ppm_per_C"],
                worst_TC_case=worst["case_id"],
                maximum_absolute_error_percent=max(
                    r["metrics"]["max_absolute_error_percent"] for r in rr
                ),
            )
        else:
            s[group] = {"cases": len(rr), "loops": {}}
            for cut in ["main", "bias"]:
                conventional = [
                    r
                    for r in rr
                    if r["metrics"]["loops"][cut]["phase_margin_deg"] is not None
                ]
                s[group]["loops"][cut] = dict(
                    conventional_margin_cases=len(conventional),
                    review_required_cases=len(rr) - len(conventional),
                )
                if conventional:
                    worst = min(
                        conventional,
                        key=lambda r: r["metrics"]["loops"][cut]["phase_margin_deg"],
                    )
                    s[group]["loops"][cut].update(
                        minimum_conditional_PM_deg=worst["metrics"]["loops"][cut][
                            "phase_margin_deg"
                        ],
                        worst_case=worst["case_id"],
                    )
    if test == LG:
        s["whole_network_stability_certified"] = False
    return s


# Optional path configuration.
def quoted(value):
    if any(c in str(value) for c in '\n\r\x00$`"\\{}'):
        raise ValueError(
            "Use a folder path without newlines, dollar signs, backticks, quotes, backslashes or braces"
        )
    return '"' + str(value) + '"'


def configure(folder, netlist_dir):
    # Resolve and validate every edit before writing any file.
    """Update portable core references and Python launchers in all ten benches."""
    updates = []
    for test in TESTS:
        p = folder / (test + ".sch")
        s = p.read_text()
        runner = "run_bandgap.py"
        cmd = (
            "shell python3 "
            + quoted(folder / runner)
            + " --deck "
            + quoted(netlist_dir / (test + ".spice"))
            + " --test "
            + test
        )
        if test == "TB05_LOOP_STABILITY":
            pass  # TB05 default profile is selected in the single configuration file.
        value = (
            ".control\nset noaskquit\necho BANDGAP_SINGLE_RUNNER_V9_"
            + test
            + "\n"
            + cmd
            + "\necho RUNNER_RETURNED_CHECK_REPORT_AND_ERRORS\n.endc\n"
        )
        if test == TRIM:
            value = value.replace(".endc\n", "quit\n.endc\n")
        # Quotes pass through both the .sch record reader and the attribute
        # parser; preserve them so paths with spaces reach the netlist intact.
        encoded = value.replace("\\", r"\\\\").replace('"', r"\\\"")
        replacement = (
            'C {code.sym} 530 420 0 0 {name=TEST only_toplevel=true format="@value" value="'
            + encoded
            + '"}'
        )
        s, n = re.subn(
            r'C \{code.sym\} 530 420 0 0 \{name=TEST.*?\n"\}',
            lambda m: replacement,
            s,
            flags=re.S,
        )
        if n != 1:
            raise ValueError("Expected one TEST block: " + str(p))
        # Resolve beside the open schematic, independently of Xschem's working
        # directory and library search order. Keep the saved reference portable.
        core_name = (
            "Bandgap_Core_Res.sym"
            if test == TRIM
            else ("Bandgap_Core_LoopProbe.sym" if test == LG else "Bandgap_Core.sym")
        )
        core = (
            "tcleval([file join [file dirname [xschem get schname]] " + core_name + "])"
        )
        position = "500 400" if test == TRIM else "400 240"
        pattern = (
            r"C \{[^\n{}]*Bandgap_Core(?:_LoopProbe|_Res)?(?:\(1\))?\.sym(?:\]\))?\} "
            + position
            + r" 0 0 \{name=x1\}"
        )
        s, n = re.subn(
            pattern, lambda m: "C {" + core + "} " + position + " 0 0 {name=x1}", s
        )
        if n != 1:
            raise ValueError("Expected one core instance: " + str(p))
        updates.append((p, s))
    for p, s in updates:
        p.write_text(s)
    print("Configured ten testbenches in " + str(folder))
    print("Expected Xschem netlist directory: " + str(netlist_dir))
    print("Open canonical TBxx filenames, regenerate the netlist, then simulate.")
    print(
        "Per-test profiles come from bandgap_config.json; full includes TB10 PVT; trim_nominal is a quick trim check."
    )


# TB06-TB09 and consolidated configuration. Existing TB01-TB05 math stays above.


def model_dependencies(paths):
    """Hash nested model includes as well as the directly included model files.
    The deck's simulator executable and source are hashed by main().
    """
    pending = list(paths)
    seen = set()
    out = []
    while pending:
        p = Path(pending.pop()).resolve()
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
        for line in p.read_text(errors="replace").splitlines():
            m = re.match(
                r"^\s*\.(?:include|inc|lib)\s+(?:['\"]([^'\"]+)['\"]|([^\s]+))",
                line,
                re.I,
            )
            if not m:
                continue
            token = m[1] or m[2]
            # .lib SECTION declarations have no file path and no second token.
            if re.match(r"^\s*\.lib\s+\S+\s*$", line, re.I):
                continue
            candidate = Path(token)
            if not candidate.is_absolute():
                candidate = p.parent / candidate
            if candidate.is_file():
                pending.append(candidate)
            elif "." in token or "/" in token:
                raise FileNotFoundError(
                    "Nested model dependency not found: " + str(candidate)
                )
    return sorted(out)


def load_config(path, test, profile=None):
    """Merge defaults, test overrides and the selected profile in that order."""
    path = Path(path).expanduser().resolve()
    doc = json.loads(path.read_text())
    if doc.get("schema_version") != 3:
        raise ValueError("Use the consolidated bandgap_config.json (schema_version 3)")
    profile = profile or doc.get("test_profiles", {}).get(test, doc["default_profile"])
    if profile not in doc["profiles"]:
        raise ValueError("Unknown profile: " + profile)
    config = dict(doc["defaults"])
    config.update(doc.get("test_overrides", {}).get(test, {}))
    config.update(doc["profiles"][profile])
    root = Path(doc["output_directory"]).expanduser()
    if not root.is_absolute():
        root = path.parent / root
    config.update(results_directory=str(root.resolve() / profile), _profile=profile)
    if any(not isinstance(v, str) for v in doc["controls"].values()):
        raise ValueError("Control templates must be strings")
    if set(doc["controls"]) != set(TESTS):
        raise ValueError("controls must contain all ten test names")
    if test == TRIM:
        config["_test"] = TRIM
    return config, doc["controls"]


def validate_config(config):
    """Validate all shared and test-specific settings before scheduling work."""
    _validate_temperature_loop_config(config)
    if config.get("_test") == TRIM:
        validate_trim_config(config)
    for key in [
        "mc_samples",
        "mc_seed_start",
        "repeated_cycle_count",
        "noise_points_per_decade",
    ]:
        if type(config[key]) is not int or config[key] < 1:
            raise ValueError(key + " must be a positive integer")
    if config["mc_seed_start"] + config["mc_samples"] >= 2147483647:
        raise ValueError("MC seed exceeds signed 32-bit range")
    if not config["mc_modes"] or not set(config["mc_modes"]) <= {
        "global",
        "mismatch",
        "combined",
    }:
        raise ValueError("Invalid mc_modes")
    if len(set(config["mc_modes"])) != len(config["mc_modes"]):
        raise ValueError("Duplicate mc_modes")
    if not 0 < config["noise_start_Hz"] < config["noise_stop_Hz"]:
        raise ValueError("Invalid noise frequency range")
    for a, b in config["noise_bands_Hz"]:
        if not config["noise_start_Hz"] <= a < b <= config["noise_stop_Hz"]:
            raise ValueError("Noise integration band outside sweep")
    for key in [
        "startup_stop_s",
        "startup_ramp_s",
        "transient_max_step_s",
        "recovery_limit_s",
        "recovery_observe_s",
        "disturbance_hold_s",
        "disturbance_edge_s",
        "reference_target_V",
        "reference_tolerance_fraction",
        "idd_limit_uA",
    ]:
        if (
            not isinstance(config[key], (int, float))
            or not math.isfinite(config[key])
            or config[key] <= 0
        ):
            raise ValueError("Invalid " + key)
    if config["startup_stop_s"] <= config["startup_ramp_s"] + 1e-6:
        raise ValueError("Startup must continue after ramp")
    if config["recovery_observe_s"] < config["recovery_limit_s"]:
        raise ValueError("Observation window shorter than recovery deadline")
    for key in ["disturbance_ramps_s", "brownout_durations_s"]:
        if (
            not config[key]
            or len(set(config[key])) != len(config[key])
            or any(v <= 0 or not math.isfinite(v) for v in config[key])
        ):
            raise ValueError("Invalid " + key)
    if (
        not config["supply_step_levels_V"]
        or len(set(config["supply_step_levels_V"]))
        != len(config["supply_step_levels_V"])
        or any(not 0 < v <= 5.5 for v in config["supply_step_levels_V"])
    ):
        raise ValueError("Invalid supply_step_levels_V")
    if (
        not config["brownout_levels_V"]
        or len(set(config["brownout_levels_V"])) != len(config["brownout_levels_V"])
        or any(
            v < 0 or v >= min(config["supply_voltages_V"])
            for v in config["brownout_levels_V"]
        )
    ):
        raise ValueError("Brownout levels must be below every selected supply")
    if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_]*", config["intrinsic_mos_name"]):
        raise ValueError("Invalid intrinsic_mos_name")
    if not config["device_screen_limits_V"] or any(
        v <= 0 or not math.isfinite(v)
        for v in config["device_screen_limits_V"].values()
    ):
        raise ValueError("Invalid device screening limits")


def cases(config, test):
    """Dispatch case generation for trim, statistical, disturbance or fixed corners."""
    if test == TRIM:
        validate_trim_config(config)
        supplies = list(map(float, config["supply_voltages_V"]))
        for mos, bjt, res, mim, temp in itertools.product(
            config["mos_corners"],
            config["bjt_corners"],
            config["res_corners"],
            config["mim_corners"],
            config["temperatures_C"],
        ):
            separate_calibration = temp != 25 or supplies[0] != 3.3
            case_id = f"{mos}_{bjt}_{res}_{mim}_T{temp:g}_trim".replace(
                ".", "p"
            ).replace("-", "m")
            yield dict(
                mos=mos,
                bjt=bjt,
                res=res,
                mim=mim,
                temperature_C=temp,
                vdd_V=None,
                range=(
                    "mixed"
                    if any(trim_supply_range(v, config) == "stress" for v in supplies)
                    else "nominal"
                ),
                case_id=case_id,
                dc_operating_points=128 * len(supplies),
                calibration_dc_operating_points=128 if separate_calibration else 0,
                startup_runs=len(supplies) if config["trim_startup"] else 0,
                supply_voltages_V=supplies,
                calibration_supply_V=3.3,
                calibration_temperature_C=25,
            )
    elif test == MC:
        # Statistical libraries replace deterministic process corners. Mixing global
        # random process shifts with ff/ss is not a population-yield calculation.
        for mode, sample in itertools.product(
            config["mc_modes"], range(config["mc_samples"])
        ):
            seed = config["mc_seed_start"] + sample
            for temp, v in itertools.product(
                config["temperatures_C"], config["supply_voltages_V"]
            ):
                yield dict(
                    mos="statistical",
                    bjt="bjt_statistical",
                    res="res_statistical",
                    mim="mimcap_typical",
                    temperature_C=temp,
                    vdd_V=v,
                    mc_mode=mode,
                    sample=sample,
                    seed=seed,
                    range=(
                        "nominal"
                        if config["nominal_supply_min_V"]
                        <= v
                        <= config["nominal_supply_max_V"]
                        else "stress"
                    ),
                    case_id=f"{mode}_S{seed}_T{temp:g}_V{v:g}".replace(
                        ".", "p"
                    ).replace("-", "m"),
                )
    elif test == DISTURB:
        scenarios = [dict(kind="ramp", ramp_s=r) for r in config["disturbance_ramps_s"]]
        scenarios += [
            dict(kind="brownout", low_V=v, duration_s=t)
            for v, t in itertools.product(
                config["brownout_levels_V"], config["brownout_durations_s"]
            )
        ]
        scenarios += [
            dict(kind="cycles", low_V=0, duration_s=max(config["brownout_durations_s"]))
        ]
        for base in _base_cases(config, test):
            steps = [
                dict(kind="step", low_V=v, duration_s=config["disturbance_hold_s"])
                for v in config["supply_step_levels_V"]
                if abs(v - base["vdd_V"]) > 1e-9
            ]
            for i, sc in enumerate(scenarios + steps):
                yield dict(
                    base,
                    scenario=sc,
                    case_id=base["case_id"] + f'_event{i:02d}_{sc["kind"]}',
                )
    else:
        yield from _tb01_to_tb05_cases(config, test)


def disturbance_waveform(case, config):
    v = case["vdd_V"]
    sc = case["scenario"]
    edge = config["disturbance_edge_s"]
    hold = config["disturbance_hold_s"]
    obs = config["recovery_observe_s"]
    if sc["kind"] == "ramp":
        end = 1e-6 + sc["ramp_s"]
        return [(0, 0), (1e-6, 0), (end, v), (end + obs, v)], [end], end + obs
    # Establish a real cold start, then wait before disturbing the rail.
    ramp = 1e-6 + config["startup_ramp_s"]
    t = ramp + obs + hold
    pts = [(0, 0), (1e-6, 0), (ramp, v), (t, v)]
    recover = []
    count = config["repeated_cycle_count"] if sc["kind"] == "cycles" else 1
    for _ in range(count):
        pts.extend(
            [(t + edge, sc["low_V"]), (t + edge + sc["duration_s"], sc["low_V"])]
        )
        end = t + 2 * edge + sc["duration_s"]
        pts.append((end, v))
        recover.append(end)
        t = end + obs + hold
        pts.append((t, v))
    return pts, recover, t


def source_for_case(source, case, test):
    """Apply corner selection, statistical switches and noise excitation."""
    s = _fixed_corner_source(source, case, test)
    if test == MC:
        for key, on in [
            ("sw_stat_global", case["mc_mode"] != "mismatch"),
            ("sw_stat_mismatch", case["mc_mode"] != "global"),
        ]:
            s, n = re.subn(
                r"\b" + key + r"\s*=\s*0\b", key + "=" + str(int(on)), s, flags=re.I
            )
            if n != 1:
                raise ValueError("Expected one disabled statistical switch: " + key)
        # MIM bank remains its original charge-form model. Its fixed nominal
        # capacitance has no effect on this DC-only mismatch/accuracy measurement.
    elif test == NOISE:
        s = re.sub(
            r"^VDD\s+[^\n]*",
            f'VDD avdd 0 DC {case["vdd_V"]} AC 1',
            s,
            flags=re.M | re.I,
        )
    # Waveforms require config, inserted by control_for_case through a parameter
    # independent .alter is avoided; run_case prepares them in per-case source below.
    return s


def spice_statements(source):
    joined = re.sub(r"\n\s*\+\s*", " ", source)
    return [
        l.split()
        for l in joined.splitlines()
        if l.strip() and not l.lstrip().startswith("*")
    ]


def mos_devices(source):
    """Extract MOS subcircuit instances from the actual connected Bandgap_Core.
    Never assume the top instance is XDUT; uploaded schematics use x1.
    Unsupported nested custom blocks fail instead of silently omitting devices.
    """
    sub = {}
    top = []
    active = None
    for t in spice_statements(source):
        if t[0].lower() == ".subckt":
            active = t[1].lower()
            sub[active] = {
                "ports": [v.lower() for v in t[2:] if "=" not in v],
                "lines": [],
            }
        elif t[0].lower() == ".ends":
            active = None
        elif active:
            sub[active]["lines"].append(t)
        else:
            top.append(t)
    dut = [
        t
        for t in top
        if t[0].lower().startswith("x") and any(v.lower() == "bandgap_core" for v in t)
    ]
    if len(dut) != 1 or "bandgap_core" not in sub:
        raise ValueError("Expected one embedded Bandgap_Core DUT for device screening")
    inst = dut[0]
    body = sub["bandgap_core"]
    idx = next(i for i, v in enumerate(inst) if v.lower() == "bandgap_core")
    if len(inst[1:idx]) != len(body["ports"]):
        raise ValueError("DUT formal/actual pin count mismatch")
    mapping = dict(zip(body["ports"], [n.lower() for n in inst[1:idx]]))
    prefix = inst[0].lower()
    devices = []
    for t in body["lines"]:
        if not t[0].lower().startswith("x"):
            continue
        model = t[5].lower() if len(t) > 5 else ""
        if re.search(r"(?:n|p)fet_0[356]v[03]", model):
            nodes = [
                mapping.get(n.lower(), prefix + "." + n.lower()) if n != "0" else "0"
                for n in t[1:5]
            ]
            nodes = [ground_name(n) for n in nodes]
            devices.append(
                dict(
                    name=t[0],
                    path=prefix + "." + t[0].lower(),
                    model=model,
                    nodes=nodes,
                )
            )
        elif any(x.lower() in sub for x in t[1:] if "=" not in x):
            # Known primitive MIM is embedded; custom hierarchy needs explicit support.
            if not any(x.lower() == "cap_mim_2f0ff" for x in t):
                raise ValueError(
                    "Nested custom DUT block needs device probe support: " + t[0]
                )
    if not devices:
        raise ValueError("No MOS devices found in Bandgap_Core")
    return devices


def prepare_case_deck(source, case, test, config, template):
    """Combine the case's circuit copy with its measurement control block."""
    s = source_for_case(source, case, test)
    if test in (POWER, DISTURB):
        if test == POWER:
            end = 1e-6 + config["startup_ramp_s"]
            points = [
                (0, 0),
                (1e-6, 0),
                (end, case["vdd_V"]),
                (config["startup_stop_s"], case["vdd_V"]),
            ]
        else:
            points, _, _ = disturbance_waveform(case, config)
        wave = "PWL(" + " ".join(f"{t:.15g} {v:.15g}" for t, v in points) + ")"
        # DC value is deliberately nominal for the OP screen. UIC transient starts
        # at zero and follows the PWL, independent of the OP analysis.
        s = re.sub(
            r"^VDD\s+[^\n]*",
            f'VDD avdd 0 DC {case["vdd_V"]} ' + wave,
            s,
            flags=re.M | re.I,
        )
    return s + control_for_case(template, test, dict(config, _case=case))


def export_block(name, vectors):
    return f"echo BEGIN_{name} >> case_report.txt\nwrdata case_report.txt {vectors}\necho END_{name} >> case_report.txt\n"


def control_for_case(template, test, config):
    """Dispatch the SPICE measurement commands for the requested testbench."""
    if test not in (MC, NOISE, POWER, DISTURB):
        return _tb01_to_tb05_control(template, test, config)
    case = config["_case"]
    out = "case_report.txt"
    s = f".control\nset noaskquit\nset numdgt=15\nset wr_vecnames\nset wr_singlescale\nunset appendwrite\necho {test}_SINGLE_CASE > {out}\nset appendwrite\n"
    if test == MC:
        # Reparse after resetting the random generator. Each PVT point uses the
        # same seed and deck ordering, preserving its model draw across V/T.
        s += f'setseed {case["seed"]}\nreset\n'
    s += "save all\n"
    if test == POWER:
        for d in config["_devices"]:
            base = "@m." + d["path"] + "." + config["intrinsic_mos_name"]
            s += f"save {base}[vdsat] {base}[gm] {base}[id]\n"
    s += "op\nlet OP_VREF_V=v(vref)\nlet OP_IDD_UA=-i(vdd)*1e6\nprint OP_VREF_V OP_IDD_UA >> case_report.txt\n"
    if test == NOISE:
        s += "unset sqrnoise\n"
        s += f'noise v(vref) VDD dec {config["noise_points_per_decade"]} {config["noise_start_Hz"]} {config["noise_stop_Hz"]} 1\nsetplot noise1\n'
        s += "let OUTPUT_ASD=onoise_spectrum\nlet OUTPUT_PSD=OUTPUT_ASD*OUTPUT_ASD\n"
        s += export_block("NOISE_DATA", "OUTPUT_ASD OUTPUT_PSD")
    if test == POWER:
        for i, d in enumerate(config["_devices"]):
            dn, gn, sn, bn = d["nodes"]
            base = "@m." + d["path"] + "." + config["intrinsic_mos_name"]
            for label, a, b in [
                ("vds", dn, sn),
                ("vgs", gn, sn),
                ("vgd", gn, dn),
                ("vgb", gn, bn),
                ("vdb", dn, bn),
                ("vsb", sn, bn),
            ]:
                expr = voltage(a, b)
                s += f"let dev{i}_{label}={expr}\nprint dev{i}_{label} >> {out}\n"
            for par in ["vdsat", "gm", "id"]:
                s += f"let dev{i}_{par}={base}[{par}]\nprint dev{i}_{par} >> {out}\n"
    if test in (POWER, DISTURB):
        stop = (
            config["startup_stop_s"]
            if test == POWER
            else disturbance_waveform(case, config)[2]
        )
        step = config["transient_max_step_s"]
        s += f"tran {step:.15g} {stop:.15g} 0 {step:.15g} uic\nlet IDD_UA=-i(vdd)*1e6\n"
        s += export_block("TRAN_DATA", "v(avdd) v(vref) IDD_UA")
        if test == POWER:
            for i, d in enumerate(config["_devices"]):
                dn, gn, sn, bn = d["nodes"]
                for label, a, b in [
                    ("vds", dn, sn),
                    ("vgs", gn, sn),
                    ("vgd", gn, dn),
                    ("vgb", gn, bn),
                    ("vdb", dn, bn),
                    ("vsb", sn, bn),
                ]:
                    s += f"let dev{i}_{label}_wave=abs({voltage(a,b)})\n"
                    s += f"meas tran dev{i}_{label}_peak MAX dev{i}_{label}_wave\nprint dev{i}_{label}_peak >> {out}\n"
    return s + f"echo END_OF_{test}_RUN >> {out}\nquit\n.endc\n.end\n"


def ground_name(node):
    node = node.strip().lower()
    return "0" if node in ("0", "gnd") else node


def voltage(a, b):
    a, b = ground_name(a), ground_name(b)
    if a == b:
        return "0*v(avdd)"
    if b == "0":
        return "v(" + a + ")"
    if a == "0":
        return "-v(" + b + ")"
    return "v(" + a + ")-v(" + b + ")"


def scalars(text):
    pairs = re.findall(r"^([a-z0-9_]+)\s*=\s*([-+0-9.eE]+)\s*$", text, re.M | re.I)
    vals = {k.lower(): float(v) for k, v in pairs}
    if any(not math.isfinite(v) for v in vals.values()):
        raise ValueError("Nonfinite measurement")
    return vals


def integrate_psd(rows, lo, hi):
    """Log-log power-law interpolation/integration of a nonnegative PSD.
    Unlike trapezoids across wide logarithmic steps, this is exact for 1/f.
    """
    total = 0.0
    for r1, r2 in zip(rows, rows[1:]):
        x1, y1 = r1[0], r1[2]
        x2, y2 = r2[0], r2[2]
        a, b = max(lo, x1), min(hi, x2)
        if b <= a:
            continue
        if y1 > 0 and y2 > 0:
            k = math.log(y2 / y1) / math.log(x2 / x1)
            ya = y1 * (a / x1) ** k
            total += (
                ya * a * math.log(b / a)
                if abs(k + 1) < 1e-10
                else ya * a * math.expm1((k + 1) * math.log(b / a)) / (k + 1)
            )
        else:
            ya = y1 + (y2 - y1) * (a - x1) / (x2 - x1)
            yb = y1 + (y2 - y1) * (b - x1) / (x2 - x1)
            total += (ya + yb) / 2 * (b - a)
    return math.sqrt(total)


def recovery_metrics(rows, start, end, config):
    pts = [r for r in rows if start - 1e-12 <= r[0] <= end + 1e-12]
    if len(pts) < 2 or pts[-1][0] < end - 2 * config["transient_max_step_s"]:
        raise ValueError("Recovery window incomplete")
    target = config["reference_target_V"]
    tol = target * config["reference_tolerance_fraction"]
    last_bad = max(
        (i for i, r in enumerate(pts) if abs(r[2] - target) > tol), default=-1
    )
    settled = None if last_bad == len(pts) - 1 else max(0, pts[last_bad + 1][0] - start)
    # Ready means stays in-band to the end of this observation window.
    return dict(
        recovery_edge_s=start,
        observation_end_s=end,
        settling_time_s=settled,
        ready_by_deadline=settled is not None and settled <= config["recovery_limit_s"],
        final_vref_V=pts[-1][2],
        vref_min_V=min(r[2] for r in pts),
        vref_peak_V=max(r[2] for r in pts),
        overshoot_above_target_mV=max(0, max(r[2] for r in pts) - target) * 1e3,
    )


def read_result(text, test, config):
    """Validate exported measurements and calculate the requested bench metrics."""
    if test not in (MC, NOISE, POWER, DISTURB):
        return _read_tb01_to_tb05_result(text, test, config)
    if "END_OF_" + test + "_RUN" not in text:
        raise ValueError("Missing completion marker")
    m = scalars(text)
    if any(k not in m for k in ["op_vref_v", "op_idd_ua"]):
        raise ValueError("Missing operating point")
    target = config["reference_target_V"]
    err = m["op_vref_v"] - target
    m.update(
        absolute_error_percent=abs(err) / target * 100,
        op_reference_in_target=abs(err)
        <= config["reference_tolerance_fraction"] * target,
        power_uW=m["op_idd_ua"] * config["_case"]["vdd_V"],
        idd_within_limit=0 <= m["op_idd_ua"] <= config["idd_limit_uA"],
    )
    if test == MC:
        m.update(
            required_output_correction_mV=-err * 1e3,
            physical_trim_simulated=False,
            scope="DC statistical accuracy and power; correction is an ideal output offset requirement, not a resistor trim code; no MC startup/noise/PSRR signoff",
        )
        return m
    data = blocks(text)
    if test == NOISE:
        rows = data.get("NOISE_DATA", [])
        if (
            len(rows) < 2
            or abs(rows[0][0] / config["noise_start_Hz"] - 1) > 1e-6
            or abs(rows[-1][0] / config["noise_stop_Hz"] - 1) > 1e-6
        ):
            raise ValueError("Incomplete noise sweep")
        if any(r[1] < 0 or r[2] < 0 or len(r) != 3 for r in rows) or not any(
            r[2] > 0 for r in rows
        ):
            raise ValueError("Invalid or zero noise spectrum; review PDK noise support")
        if any(b[0] <= a[0] for a, b in zip(rows, rows[1:])):
            raise ValueError("Nonmonotonic noise frequencies")
        m["bands"] = [
            dict(low_Hz=a, high_Hz=b, output_rms_uV=integrate_psd(rows, a, b) * 1e6)
            for a, b in config["noise_bands_Hz"]
        ]
        m.update(
            noise_requirement_pass=None,
            noise_qualified_at_target=m["op_reference_in_target"],
            noise_scope="Output ASD and integrated output RMS using installed PDK models; no numerical noise requirement assigned",
        )
        return m
    rows = data.get("TRAN_DATA", [])
    stop = (
        config["startup_stop_s"]
        if test == POWER
        else disturbance_waveform(config["_case"], config)[2]
    )
    if len(rows) < 2 or abs(rows[-1][0] - stop) > max(1e-12, stop * 1e-7):
        raise ValueError("Incomplete transient")
    if any(len(r) != 4 for r in rows):
        raise ValueError("Transient export column mismatch")
    m.update(
        transient_peak_IDD_uA=max(r[3] for r in rows),
        transient_final_vref_V=rows[-1][2],
    )
    if test == POWER:
        devs = []
        for i, d in enumerate(config["_devices"]):
            dv = dict(d)
            for key in ["vds", "vgs", "vgd", "vgb", "vdb", "vsb", "vdsat", "gm", "id"]:
                dv[key] = m.pop(f"dev{i}_{key}")
            dv["startup_max_abs_V"] = {
                k: m.pop(f"dev{i}_{k}_peak")
                for k in ["vds", "vgs", "vgd", "vgb", "vdb", "vsb"]
            }
            family = next(
                (k for k in config["device_screen_limits_V"] if k in d["model"]), None
            )
            if family is None:
                raise ValueError("No voltage screening threshold for " + d["model"])
            limit = config["device_screen_limits_V"][family]
            worst = max(
                [abs(dv[k]) for k in ["vds", "vgs", "vgd", "vgb", "vdb", "vsb"]]
                + list(dv["startup_max_abs_V"].values())
            )
            dv.update(
                screen_limit_V=limit,
                max_abs_terminal_V=worst,
                voltage_screen_flag=worst > limit,
                saturation_margin_V=abs(dv["vds"]) - abs(dv["vdsat"]),
                gm_over_id_per_V=(
                    abs(dv["gm"] / dv["id"]) if abs(dv["id"]) > 1e-15 else None
                ),
            )
            devs.append(dv)
        m["devices"] = devs
        m["voltage_screen_flag_count"] = sum(d["voltage_screen_flag"] for d in devs)
        m["foundry_reliability_signoff"] = False
        m["device_scope"] = (
            "Conservative user-editable voltage screens, not foundry absolute maxima; saturation margin is DC only. BJT/passive/ERC and extracted-layout checks remain separate."
        )
        m["startup"] = recovery_metrics(
            rows, 1e-6 + config["startup_ramp_s"], stop, config
        )
    else:
        _, edges, _ = disturbance_waveform(config["_case"], config)
        events = [
            recovery_metrics(rows, t, t + config["recovery_observe_s"], config)
            for t in edges
        ]
        sc = config["_case"]["scenario"]
        baseline = (
            None
            if sc["kind"] == "ramp"
            else recovery_metrics(
                rows,
                1e-6 + config["startup_ramp_s"],
                1e-6 + config["startup_ramp_s"] + config["recovery_observe_s"],
                config,
            )
        )
        if sc["kind"] == "step":
            after = [
                r
                for r in rows
                if r[0]
                >= 1e-6 + config["startup_ramp_s"] + config["recovery_observe_s"]
            ]
            m["step_max_reference_deviation_mV"] = max(
                abs(r[2] - target) * 1e3 for r in after
            )
        m.update(
            events=events,
            baseline_startup=baseline,
            all_events_ready=all(e["ready_by_deadline"] for e in events)
            and (baseline is None or baseline["ready_by_deadline"]),
            max_recovery_overshoot_mV=max(
                e["overshoot_above_target_mV"] for e in events
            ),
        )
    return m


def summarize(results, test):
    """Aggregate completed cases while retaining explicit execution-failure counts."""
    if test == TRIM:
        return summarize_trim(results)
    if test not in (MC, NOISE, POWER, DISTURB):
        return _summarize_tb01_to_tb05(results, test)
    ok = [r for r in results if r["status"] == "complete"]
    s = dict(
        attempted=len(results),
        simulation_complete=len(ok),
        execution_failed_or_timed_out=len(results) - len(ok),
    )
    for group in ["nominal", "stress"]:
        rr = [r for r in ok if r["range"] == group]
        if not rr:
            continue
        ss = dict(
            cases=len(rr),
            reference_in_target_count=sum(
                r["metrics"]["op_reference_in_target"] for r in rr
            ),
        )
        if test == MC:
            ss["modes"] = {}
            for mode in sorted({r["mc_mode"] for r in rr}):
                subset = [r for r in rr if r["mc_mode"] == mode]
                samples = {
                    seed: [r for r in subset if r["seed"] == seed]
                    for seed in {r["seed"] for r in subset}
                }
                ss["modes"][mode] = dict(
                    completed_sample_ids=len(samples),
                    completed_PVT_points=len(subset),
                    accuracy_pass_PVT_points=sum(
                        r["metrics"]["op_reference_in_target"] for r in subset
                    ),
                    maximum_required_correction_mV=max(
                        abs(r["metrics"]["required_output_correction_mV"])
                        for r in subset
                    ),
                )
            ss["population_yield_certified"] = False
        elif test == NOISE:
            ss["noise_requirement_pass"] = None
        elif test == POWER:
            ss.update(
                idd_pass_count=sum(r["metrics"]["idd_within_limit"] for r in rr),
                cases_with_voltage_flags=sum(
                    r["metrics"]["voltage_screen_flag_count"] > 0 for r in rr
                ),
            )
        else:
            ss.update(
                all_events_ready_count=sum(
                    r["metrics"]["all_events_ready"] for r in rr
                ),
                maximum_overshoot_mV=max(
                    r["metrics"]["max_recovery_overshoot_mV"] for r in rr
                ),
            )
        s[group] = ss
    return s


# TB10: physical resistor trim across PVT. Calibration is always at 25 C / 3.3 V.
TRIM_PORTS = "avdd avss vref b0 b1 b2 b3 b4 b5 b6 dvss dvdd"
TRIM_IDS = ("xm7", "xm20", "xsupinj", "xn1", "xm16", "xm18")
TRIM_FIELDS = [
    "code",
    "avdd_V",
    "dvdd_V",
    "vref_V",
    "vq3_V",
    "analog_idd_A",
    "digital_idd_A",
    "error_mV",
] + [dev + "_A" for dev in TRIM_IDS]
TRIM_ERRORS = re.compile(
    r"error:|error on line|timestep too small|simulation(?:s)? aborted|"
    r"no such vector|unknown parameter|fatal error",
    re.I,
)


def validate_trim_config(config):
    if config.get("trim_dvdd_V") != 3.3 or type(config.get("trim_startup")) is not bool:
        raise ValueError("TB10 requires DVDD=3.3 V and boolean trim_startup")
    for key in [
        "trim_transient_max_step_s",
        "trim_tail_window_s",
        "trim_startup_off_limit_A",
        "trim_bias_min_A",
        "trim_gate_screen_V",
    ]:
        if (
            not isinstance(config.get(key), (int, float))
            or not math.isfinite(config[key])
            or config[key] <= 0
        ):
            raise ValueError("Invalid " + key)
    if (
        config["trim_transient_max_step_s"] > 1e-7
        or config["trim_tail_window_s"] < 1e-4
    ):
        raise ValueError("TB10 needs max step <=100 ns and tail observation >=100 us")
    if config["trim_tail_window_s"] > 0.001 or config["recovery_limit_s"] > 0.001:
        raise ValueError(
            "TB10 timing criteria exceed the fixed restart observation window"
        )


def trim_bits(code):
    if type(code) is not int or not 0 <= code < 128:
        raise ValueError("7-bit code must be an integer in 0..127")
    return [(code >> i) & 1 for i in range(7)]


def validate_trim_source(source):
    # The dedicated probes assume these physical core pins/nodes, never the old 3-pin core.
    statements = [[w.lower() for w in row] for row in spice_statements(source)]
    header = [".subckt", "bandgap_core_res"] + TRIM_PORTS.split()
    if statements.count(header) != 1:
        raise ValueError(
            "TB10 requires the embedded 12-pin Bandgap_Core_Res in its original pin order"
        )
    instance = "x1 avdd 0 vref b0 b1 b2 b3 b4 b5 b6 0 dvdd bandgap_core_res".split()
    if statements.count(instance) != 1:
        raise ValueError(
            "TB10 DUT instance/ground/bit connections do not match the 12-pin symbol"
        )
    for name, nodes in [("vdd", ["avdd", "0"]), ("vdvdd", ["dvdd", "0"])] + [
        ("vbit" + str(i), ["b" + str(i), "0"]) for i in range(7)
    ]:
        found = [row for row in statements if row[0] == name]
        if len(found) != 1 or found[0][1:3] != nodes:
            raise ValueError("Missing or misconnected TB10 stimulus: " + name)


def trim_supply_range(v, config):
    return (
        "nominal"
        if config["nominal_supply_min_V"] <= v <= config["nominal_supply_max_V"]
        else "stress"
    )


def trim_sweep_control(config):
    output = "trim_sweep.txt"
    TARGET = config["reference_target_V"]
    lines = [
        ".control",
        "set noaskquit",
        "set num_threads=1",
        "set numdgt=15",
        "set wr_singlescale",
        "unset wr_vecnames",
        "set appendwrite",
        "save all " + " ".join(f"@m.x1.{x}.m0[id]" for x in TRIM_IDS),
        "echo " + " ".join(TRIM_FIELDS) + f" > {output}",
        "foreach supply " + " ".join(f"{v:g}" for v in config["supply_voltages_V"]),
        "alter VDD dc = $supply",
        "foreach code " + " ".join(map(str, range(128))),
    ]
    for i in range(7):
        lines += [
            f"let bit_value = 3.3*(floor($code/{2**i})-2*floor($code/{2**(i+1)}))",
            f"alter VBIT{i} dc = $&bit_value",
        ]
    lines += [
        "op",
        "let vr = v(vref)",
        "let vq = v(x1.vq3)",
        "let ia = -i(vdd)",
        "let idig = -i(vdvdd)",
        f"let err = 1000*(v(vref)-{TARGET})",
    ]
    for i, dev in enumerate(TRIM_IDS):
        lines.append(f"let cur{i} = @m.x1.{dev}.m0[id]")
    lines += [
        "let code_value = $code",
        "let supply_value = $supply",
        "let digital_value = 3.3",
        "setscale code_value",
        f"wrdata {output} supply_value digital_value vr vq ia idig err "
        + " ".join(f"cur{i}" for i in range(6)),
        "destroy $curplot",
        "end",
        "end",
        "echo TRIM_SWEEP_COMPLETE",
        ".endc",
    ]
    return "\n".join(lines) + "\n"


def trim_startup_deck(source, vdd, code, config):
    text = re.sub(
        r"^VDD\s+[^\n]*",
        f"VDD avdd 0 PWL(0 0 20u 0 120u {vdd} 4m {vdd} 4001u 0 4500u 0 4501u {vdd} 6m {vdd})",
        source,
        flags=re.M | re.I,
    )
    text = re.sub(
        r"^VDVDD\s+[^\n]*",
        "VDVDD dvdd 0 PWL(0 0 1u 0 11u 3.3 6m 3.3)",
        text,
        flags=re.M | re.I,
    )
    for i, bit in enumerate(trim_bits(code)):
        text = re.sub(
            r"^VBIT" + str(i) + r"\s+[^\n]*",
            f"EBIT{i} b{i} 0 dvdd 0 {bit}",
            text,
            flags=re.M | re.I,
        )
    vectors = {
        "avdd": "v(avdd)",
        "dvdd": "v(dvdd)",
        "vref": "v(vref)",
        "vq3": "v(x1.vq3)",
        "analog_idd": "-i(vdd)",
        "digital_idd": "-i(vdvdd)",
    }
    vectors.update({d: f"@m.x1.{d}.m0[id]" for d in TRIM_IDS})
    vectors.update({f"b{i}": f"v(b{i})" for i in range(7)})
    vectors.update({f"b{i}_b": f"v(x1.b{i}_b)" for i in range(7)})
    vectors.update({f"rbit{i}top": f"v(x1.rbit{i}top)" for i in range(7)})
    text += ".control\nset noaskquit\nset num_threads=1\nset numdgt=15\nset wr_singlescale\nset wr_vecnames\n"
    text += (
        "save all "
        + " ".join(f"@m.x1.{d}.m0[id]" for d in TRIM_IDS)
        + f'\ntran {config["trim_transient_max_step_s"]:.15g} 6m 0 {config["trim_transient_max_step_s"]:.15g}\n'
    )
    for key, expr in vectors.items():
        text += f"let out_{key} = {expr}\n"
    text += (
        "wrdata trace.txt "
        + " ".join("out_" + k for k in vectors)
        + "\necho STARTUP_COMPLETE\n.endc\n.end\n"
    )
    return text, list(vectors)


def trim_table(path, ncols):
    with Path(path).open() as f:
        next(f)  # wrdata field names
        rows = [[float(v) for v in line.split()] for line in f if line.strip()]
    if not rows or any(
        len(row) != ncols or not all(math.isfinite(v) for v in row) for row in rows
    ):
        raise ValueError("Incomplete/nonfinite TB10 table: " + str(path))
    return rows


def trim_read_sweep(path, config):
    data = trim_table(path, len(TRIM_FIELDS))
    expected = {(code, v) for code in range(128) for v in config["supply_voltages_V"]}
    if (
        len(data) != len(expected)
        or {(r[0], r[1]) for r in data} != expected
        or any(r[2] != 3.3 for r in data)
    ):
        raise ValueError("Missing, duplicated or invalid TB10 code/supply point")
    rows = []
    for values in data:
        row = dict(zip(TRIM_FIELDS, values))
        row["code"] = int(row["code"])
        row["bits_b6_to_b0"] = format(row["code"], "07b")
        row["range"] = trim_supply_range(row["avdd_V"], config)
        row["self_sustaining_op"] = all(
            abs(row[d + "_A"]) <= config["trim_startup_off_limit_A"]
            for d in TRIM_IDS[:3]
        ) and all(abs(row[d + "_A"]) > config["trim_bias_min_A"] for d in TRIM_IDS[3:])
        rows.append(row)
    return sorted(rows, key=lambda r: (r["avdd_V"], r["code"]))


def trim_calibration_code(rows):
    valid = [r for r in rows if r["avdd_V"] == 3.3 and r["self_sustaining_op"]]
    if not valid:
        raise ValueError("No self-sustaining calibration code at 25 C / 3.3 V")
    return min(valid, key=lambda r: (abs(r["error_mV"]), r["code"]))["code"]


def trim_dc_metrics(rows, config, calibration_code=None):
    summary = {}
    for v in map(float, config["supply_voltages_V"]):
        rr = [r for r in rows if r["avdd_V"] == v]
        valid = [r for r in rr if r["self_sustaining_op"]]
        best = min(valid, key=lambda r: abs(r["error_mV"])) if valid else None
        volts = [r["vref_V"] for r in rr]
        ordered = sorted(volts)
        summary[str(v)] = dict(
            best_code=best["code"] if best else None,
            bits_b6_to_b0=best["bits_b6_to_b0"] if best else None,
            best_vref_V=best["vref_V"] if best else None,
            best_error_mV=best["error_mV"] if best else None,
            min_vref_V=min(volts),
            max_vref_V=max(volts),
            largest_sorted_gap_mV=max(b - a for a, b in zip(ordered, ordered[1:]))
            * 1e3,
            upward_code_steps=sum(b - a > 1e-8 for a, b in zip(volts, volts[1:])),
            self_sustaining_codes=len(valid),
        )
    code = trim_calibration_code(rows) if calibration_code is None else calibration_code
    trim_bits(code)
    held = [r for r in rows if r["code"] == code]
    for row in held:
        row["accuracy_pass"] = (
            abs(row["vref_V"] - config["reference_target_V"])
            <= config["reference_target_V"] * config["reference_tolerance_fraction"]
        )
    return dict(
        dc=summary,
        calibration_code=code,
        calibration_temperature_C=25,
        calibration_supply_V=3.3,
        fixed_code_results=held,
        startup=[],
        fixed_code_accuracy_pass=all(r["accuracy_pass"] for r in held),
        fixed_code_self_sustaining_pass=all(r["self_sustaining_op"] for r in held),
    )


def trim_startup_metrics(path, columns, config):
    a = trim_table(path, len(columns) + 1)
    if (
        a[-1][0] < 0.006 - 1e-10
        or a[0][0] > 1e-9
        or any(b[0] <= x[0] for x, b in zip(a, a[1:]))
    ):
        raise ValueError("TB10 startup trace incomplete or unordered")
    t = [r[0] for r in a]
    d = {key: [r[i + 1] for r in a] for i, key in enumerate(columns)}
    d["zero"] = [0.0] * len(t)
    good = [
        abs(v - config["reference_target_V"])
        <= config["reference_target_V"] * config["reference_tolerance_fraction"]
        for v in d["vref"]
    ]
    for dev in TRIM_IDS[:3]:
        good = [
            g and abs(v) <= config["trim_startup_off_limit_A"]
            for g, v in zip(good, d[dev])
        ]
    for dev in TRIM_IDS[3:]:
        good = [g and abs(v) > config["trim_bias_min_A"] for g, v in zip(good, d[dev])]
    events = []
    for name, start, end in [("cold", 120e-6, 0.004), ("restart", 0.004501, 0.006)]:
        indexes = [i for i, x in enumerate(t) if start <= x <= end]
        if not indexes:
            raise ValueError("TB10 startup event has no samples")
        bad = [i for i in indexes if not good[i]]
        first = bad[-1] + 1 if bad else indexes[0]
        settled = (
            first <= indexes[-1]
            and t[indexes[-1]] - t[first] >= config["trim_tail_window_s"]
        )
        delay = t[first] - start if settled else None
        tail = [
            i for i, x in enumerate(t) if end - config["trim_tail_window_s"] <= x <= end
        ]
        events.append(
            dict(
                event=name,
                settle_after_ramp_us=None if delay is None else delay * 1e6,
                status=(
                    "PASS"
                    if delay is not None and delay <= config["recovery_limit_s"]
                    else "FAIL"
                ),
                tail_vref_V=sum(d["vref"][i] for i in tail) / len(tail),
                tail_startup_max_A={
                    dev: max(abs(d[dev][i]) for i in tail) for dev in TRIM_IDS[:3]
                },
            )
        )
    stress = []
    for bit in range(7):
        high, low = f"rbit{bit}top", f"rbit{bit+1}top" if bit < 6 else "vq3"
        for name, gate, body in [
            (f"M{24+2*bit}", f"b{bit}_b", "dvdd"),
            (f"M{25+2*bit}", f"b{bit}", "zero"),
        ]:
            max_gate = max(
                abs(g - v)
                for node in (high, low, body)
                for g, v in zip(d[gate], d[node])
            )
            forward = max(
                (v - b if body == "dvdd" else b - v)
                for node in (high, low)
                for v, b in zip(d[node], d[body])
            )
            stress.append(
                dict(
                    device=name,
                    max_abs_gate_to_node_V=max_gate,
                    max_body_junction_forward_bias_V=forward,
                    gate_screen_limit_V=config["trim_gate_screen_V"],
                    above_gate_screen=max_gate > config["trim_gate_screen_V"],
                )
            )
    # Metrics above use every adaptive sample. Bound plotting data size for full
    # PVT; lossless full terminal traces remain in the attempt (compressed if requested).
    indices = list(range(0, len(t), max(1, math.ceil(len(t) / 12000))))
    if indices[-1] != len(t) - 1:
        indices.append(len(t) - 1)
    wave = "\n".join(
        " ".join(
            f"{v:.15e}"
            for v in [
                t[i],
                d["avdd"][i],
                d["dvdd"][i],
                d["vref"][i],
                max(abs(d[dev][i]) for dev in TRIM_IDS[:3]),
            ]
        )
        for i in indices
    )
    return dict(events=events, trim_switch_stress=stress, rows=len(t)), wave


def trim_simulate(deck, config, ngspice, marker):
    env = dict(os.environ, OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
    log = deck.with_suffix(".log")
    with log.open("w") as f:
        p = subprocess.run(
            [ngspice, "-n", "-b", str(deck)],
            cwd=deck.parent,
            stdout=f,
            stderr=subprocess.STDOUT,
            timeout=config["case_timeout_seconds"],
            env=env,
        )
    text = log.read_text(errors="replace")
    if p.returncode or TRIM_ERRORS.search(text) or marker not in text:
        raise ValueError("Incomplete/error TB10 simulation; see " + str(log))
    return list(
        dict.fromkeys(
            l.strip()
            for l in text.splitlines()
            if re.search(r"warning|unsupported|not recognized", l, re.I)
        )
    )[:30]


def run_trim_case(case, *, root, source, config, fingerprint, ngspice, retry_failed):
    """Run a trim-code sweep and selected-code startup checks as one cached bundle."""
    import csv
    import gzip

    case_directory = root / "cases" / case["case_id"]
    case_directory.mkdir(parents=True, exist_ok=True)
    cache = case_directory / "result.json"
    if config["resume"] and cache.is_file():
        cached_result = json.loads(cache.read_text())
        if cached_result.get("fingerprint") == fingerprint and (
            cached_result.get("status") == "complete"
            and (case_directory / "waveforms.txt.gz").is_file()
            or cached_result.get("status") != "complete"
            and not retry_failed
        ):
            return dict(cached_result, resumed=True)
    # Changed inputs/retries get a new artifact directory; never discard old trim traces.
    attempt = case_directory / "attempts" / ("run_" + str(time.time_ns()))
    attempt.mkdir(parents=True, exist_ok=False)
    result = dict(
        case,
        fingerprint=fingerprint,
        status="failed",
        resumed=False,
        artifact_directory=str(attempt.relative_to(root)),
    )
    began = time.monotonic()
    try:
        # Every temperature bundle uses the SAME room-temperature calibration for
        # its deterministic process corner, never the locally optimal hot/cold code.
        nominal_source = source
        source = source_for_case(nominal_source, case, TRIM)
        deck = attempt / "circuit.spice"
        deck.write_text(source + trim_sweep_control(config) + ".end\n")
        result["simulator_warnings"] = trim_simulate(
            deck, config, ngspice, "TRIM_SWEEP_COMPLETE"
        )
        rows = trim_read_sweep(attempt / "trim_sweep.txt", config)
        with (attempt / "trim_sweep.csv").open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        waveform = (
            "BEGIN_TRIM_SWEEP\n"
            + (attempt / "trim_sweep.txt").read_text()
            + "END_TRIM_SWEEP\n"
        )
        calibration_rows = rows
        if case["temperature_C"] != 25 or config["supply_voltages_V"][0] != 3.3:
            folder = attempt / "calibration_T25_3p3V"
            folder.mkdir()
            cal_config = dict(config, supply_voltages_V=[3.3])
            cal_source = source_for_case(
                nominal_source, dict(case, temperature_C=25, vdd_V=3.3), TRIM
            )
            cal_deck = folder / "circuit.spice"
            cal_deck.write_text(cal_source + trim_sweep_control(cal_config) + ".end\n")
            result["simulator_warnings"] += trim_simulate(
                cal_deck, cal_config, ngspice, "TRIM_SWEEP_COMPLETE"
            )
            calibration_rows = trim_read_sweep(folder / "trim_sweep.txt", cal_config)
            waveform += (
                "BEGIN_TRIM_CALIBRATION\n"
                + (folder / "trim_sweep.txt").read_text()
                + "END_TRIM_CALIBRATION\n"
            )
        metrics = trim_dc_metrics(rows, config, trim_calibration_code(calibration_rows))
        result["metrics"] = metrics
        if config["trim_startup"]:

            def job(v):
                folder = attempt / ("startup_" + str(v).replace(".", "p") + "V")
                folder.mkdir()
                text, cols = trim_startup_deck(
                    source, v, metrics["calibration_code"], config
                )
                deck = folder / "circuit.spice"
                deck.write_text(text)
                atomic_json(folder / "columns.json", ["time_s"] + cols)
                try:
                    warnings = trim_simulate(deck, config, ngspice, "STARTUP_COMPLETE")
                    m, wave = trim_startup_metrics(folder / "trace.txt", cols, config)
                    if not config["retain_waveforms"]:
                        # Only replace this newly generated trace after a successful
                        # lossless write; never remove historical attempt artifacts.
                        with (
                            (folder / "trace.txt").open("rb") as src,
                            gzip.open(folder / "trace.txt.gz", "wb") as dst,
                        ):
                            shutil.copyfileobj(src, dst)
                        (folder / "trace.txt").unlink()
                    return (
                        dict(
                            avdd_V=v,
                            range=trim_supply_range(v, config),
                            code=metrics["calibration_code"],
                            status="COMPLETE",
                            **m,
                        ),
                        wave,
                        warnings,
                    )
                except Exception as exc:
                    return (
                        dict(
                            avdd_V=v,
                            range=trim_supply_range(v, config),
                            code=metrics["calibration_code"],
                            status="UNRESOLVED",
                            error=str(exc),
                        ),
                        "",
                        [],
                    )

            # Cases already run in parallel; avoid multiplying the configured jobs.
            for m, wave, warnings in map(job, case["supply_voltages_V"]):
                metrics["startup"].append(m)
                result["simulator_warnings"] += warnings
                name = "TRIM_STARTUP_" + str(m["avdd_V"]).replace(".", "P")
                waveform += "BEGIN_" + name + "\n" + wave + "\nEND_" + name + "\n"
        metrics["startup_pass"] = (
            all(
                m["status"] == "COMPLETE"
                and all(e["status"] == "PASS" for e in m["events"])
                for m in metrics["startup"]
            )
            if config["trim_startup"]
            else None
        )
        metrics["trim_gate_screen_pass"] = (
            all(
                m["status"] == "COMPLETE"
                and not any(dev["above_gate_screen"] for dev in m["trim_switch_stress"])
                for m in metrics["startup"]
            )
            if config["trim_startup"]
            else None
        )
        (attempt / "case_report.txt").write_text(
            waveform + "END_OF_TB10_RESISTOR_TRIM_RUN\n"
        )
        atomic_json(attempt / "metrics.json", metrics)
        if any(m["status"] != "COMPLETE" for m in metrics["startup"]):
            raise ValueError(
                "One or more TB10 startup analyses are unresolved; see metrics and attempt logs"
            )
        with gzip.open(case_directory / "waveforms.txt.gz", "wt") as f:
            f.write(waveform)
        result["status"] = "complete"
    except subprocess.TimeoutExpired:
        result.update(
            status="timeout", error="Exceeded case_timeout_seconds; no performance pass"
        )
    except Exception as exc:
        result["error"] = str(exc)
    result["elapsed_seconds"] = round(time.monotonic() - began, 3)
    atomic_json(attempt / "result.json", result)
    atomic_json(cache, result)
    return result


def summarize_trim(results):
    ok = [r for r in results if r["status"] == "complete"]
    evaluated = [r for r in results if r.get("metrics")]
    # A failed transient must not hide a completed DC sweep or the other supplies.
    # Bundle completion stays separate, and unresolved runs never count as passing.
    summary = dict(
        attempted=len(results),
        simulation_complete=len(ok),
        execution_failed_or_timed_out=len(results) - len(ok),
        scope="Configured PVT grid; 128 codes per supply; calibration at 25 C / 3.3 V held across temperature and supply; DVDD 3.3 V, digital-first",
        completed_DC_points=sum(r["dc_operating_points"] for r in evaluated),
        completed_calibration_DC_points=sum(
            r.get("calibration_dc_operating_points", 0) for r in evaluated
        ),
        completed_startup_runs=sum(
            s["status"] == "COMPLETE"
            for r in evaluated
            for s in r["metrics"]["startup"]
        ),
        unresolved_startup_runs=sum(
            s["status"] != "COMPLETE"
            for r in evaluated
            for s in r["metrics"]["startup"]
        ),
        calibration_codes=[r["metrics"]["calibration_code"] for r in evaluated],
        fixed_code_accuracy_pass_count=sum(
            r["metrics"]["fixed_code_accuracy_pass"] for r in evaluated
        ),
        startup_pass_count=sum(r["metrics"]["startup_pass"] is True for r in ok),
        startup_not_run_count=sum(r["metrics"]["startup_pass"] is None for r in ok),
        qualification_limits="Fixed corners only; no trim Monte Carlo, dense TC sweep, PSRR/noise, DVDD loss, AVDD-first sequencing or extracted layout",
        full_PVT_qualified=False,
    )
    for group in ("nominal", "stress"):
        held = [
            p
            for r in evaluated
            for p in r["metrics"]["fixed_code_results"]
            if p["range"] == group
        ]
        starts = [
            s for r in evaluated for s in r["metrics"]["startup"] if s["range"] == group
        ]
        summary[group] = dict(
            fixed_code_points=len(held),
            accuracy_pass_count=sum(p["accuracy_pass"] for p in held),
            self_sustaining_pass_count=sum(p["self_sustaining_op"] for p in held),
            startup_runs=len(starts),
            startup_unresolved_count=sum(s["status"] != "COMPLETE" for s in starts),
            startup_pass_count=sum(
                s["status"] == "COMPLETE"
                and all(e["status"] == "PASS" for e in s["events"])
                for s in starts
            ),
        )
    return summary


def run_fingerprint(
    source, runner_bytes, config, templates, executable_bytes, dependency_bytes
):
    """Hash every supplied simulation input; old or changed inputs cannot resume."""
    hasher = hashlib.sha256()
    for data in [
        source.encode(),
        runner_bytes,
        json.dumps(config, sort_keys=True).encode(),
        json.dumps(templates, sort_keys=True).encode(),
        executable_bytes,
    ]:
        hasher.update(data)
    for data in dependency_bytes:
        hasher.update(data)
    return hasher.hexdigest()


def parse_arguments():
    """Define CLI options and return the parser alongside parsed arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--configure",
        action="store_true",
        help="Set this folder and netlist paths in all ten schematics; no simulation",
    )
    parser.add_argument(
        "--netlist-dir",
        type=Path,
        default=Path.home() / ".xschem" / "simulations",
        help="Netlist directory for --configure",
    )
    parser.add_argument("--deck", type=Path)
    parser.add_argument("--test", choices=TESTS)
    parser.add_argument("--config", type=Path, default=HERE / "bandgap_config.json")
    parser.add_argument(
        "--profile",
        help="Override the configured per-test profile: full, smoke, loop_smoke, trim_nominal",
    )
    parser.add_argument(
        "--list-cases",
        action="store_true",
        help="Print coverage without netlisting or simulation",
    )
    parser.add_argument("--ngspice", default="ngspice")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--case-ids-file",
        type=Path,
        help="Run only the case IDs in this text file (one per line); full-grid coverage remains incomplete",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        help="Write under a separate results root, with the profile appended; preserves the original results",
    )
    parser.add_argument(
        "--max-cases", type=int, help="Debug only: report explicitly remains INCOMPLETE"
    )
    parser.add_argument(
        "--dc-only",
        action="store_true",
        help="TB10 only: omit selected-code startup/restart",
    )
    args = parser.parse_args()
    return parser, args


# Command-line orchestration.
def main():
    """Parse the CLI, validate inputs, execute selected cases and publish a report."""
    parser, args = parse_arguments()
    if args.dc_only and args.test != TRIM:
        parser.error("--dc-only is only supported for TB10")
    if args.configure:
        if args.deck or args.test:
            parser.error("--configure cannot be combined with --deck or --test")
        configure(HERE, args.netlist_dir.expanduser().resolve())
        return 0
    if args.test is None or (args.deck is None and not args.list_cases):
        parser.error(
            "simulation requires --deck and --test; use --configure for path setup"
        )
    config, templates = load_config(args.config, args.test, args.profile)
    if args.results_root is not None:
        config["results_directory"] = str(
            args.results_root.expanduser().resolve() / config["_profile"]
        )
    if args.dc_only:
        config["trim_startup"] = False
    validate_config(config)
    if args.max_cases is not None and args.max_cases < 1:
        parser.error("--max-cases must be positive")
    all_cases = list(cases(config, args.test))
    selected = select_cases(all_cases, args.case_ids_file, args.max_cases)
    if args.list_cases:
        print(
            json.dumps(
                {
                    "test": args.test,
                    "profile": config["_profile"],
                    "planned_cases": len(all_cases),
                    "selected_cases": len(selected),
                    "results_directory": config["results_directory"],
                    "first_case": selected[0],
                    "last_case": selected[-1],
                },
                indent=2,
            )
        )
        return 0
    source, model_paths = prepare_source(args.deck.resolve())
    if args.test == TRIM:
        validate_trim_source(source)
    model_paths = model_dependencies(model_paths)
    ngspice = shutil.which(args.ngspice)
    if ngspice is None:
        raise FileNotFoundError("ngspice executable not found: " + args.ngspice)
    ngspice = str(Path(ngspice).resolve())
    # Fail before the sweep for unavailable model sections.
    pdk_text = "\n".join(p.read_text(errors="replace") for p in model_paths)
    for key in ["mos_corners", "bjt_corners", "res_corners"]:
        for section in config[key]:
            if not re.search(
                r"^\s*\.lib\s+" + re.escape(section) + r"\s*$", pdk_text, re.M | re.I
            ):
                raise ValueError("PDK section not found: " + section)
    if args.test == "TB06_MONTE_CARLO":
        for section in ["statistical", "bjt_statistical", "res_statistical"]:
            if not re.search(
                r"^\s*\.lib\s+" + section + r"\s*$", pdk_text, re.M | re.I
            ):
                raise ValueError("PDK statistical section unavailable: " + section)
    if args.test == "TB08_POWER_DEVICE_LIMITS":
        config["_devices"] = mos_devices(source)
    # Every executable input must match before cached results can be reused.
    executable_bytes = Path(ngspice).read_bytes()
    dependency_bytes = [p.read_bytes() for p in model_paths]
    fingerprint = run_fingerprint(
        source,
        Path(__file__).read_bytes(),
        config,
        templates,
        executable_bytes,
        dependency_bytes,
    )
    results_root = Path(config["results_directory"]).expanduser().resolve()
    results_root.mkdir(parents=True, exist_ok=True)
    root = results_root / args.test
    root.mkdir(exist_ok=True)
    report = results_root / (args.test + "_UPLOAD_THIS.txt")
    with (root / "runner.lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(
                "This test is already running in the same results directory"
            )
        print(
            f"{args.test}: {len(all_cases)} planned cases; {len(selected)} selected. Report: {report}",
            flush=True,
        )
        results = []
        with report.open("w", buffering=1) as report_file:
            report_file.write(args.test + "_PVT_V2\n")
            report_file.write("PROFILE " + config["_profile"] + "\n")
            report_file.write("COVERAGE " + json.dumps(config, sort_keys=True) + "\n")
            if args.case_ids_file is not None:
                report_file.write(
                    "CASE_SELECTION "
                    + json.dumps(
                        dict(
                            case_ids_file=str(args.case_ids_file.resolve()),
                            selected_cases=len(selected),
                            full_grid_cases=len(all_cases),
                        )
                    )
                    + "\n"
                )
            report_file.write("RESUME_FINGERPRINT " + fingerprint + "\n")
            report_file.write(
                "TB08_GROUND_FIX_INTEGRATED_EXACT_FINGERPRINT_RESUME_ONLY\n"
            )
            report_file.write(
                "MIM_CHARGE_COMPATIBILITY_MODEL_NOT_UNMODIFIED_FOUNDRY_SUBCIRCUIT\n"
            )
            report_file.write(
                (
                    "PDK_STATISTICAL_MODELS_MIM_FIXED_NO_EXTRACTED_LAYOUT"
                    if args.test == "TB06_MONTE_CARLO"
                    else "FIXED_CORNERS_NO_MISMATCH_MONTE_CARLO_NO_EXTRACTED_LAYOUT"
                )
                + "\n"
            )
            report_file.write("SIMULATION_COMPLETE_IS_NOT_A_PERFORMANCE_PASS\n")
            with concurrent.futures.ThreadPoolExecutor(
                max_workers=int(config["parallel_jobs"])
            ) as pool:
                # Only keep at most jobs*2 pending futures, preserving interruption responsiveness.
                iterator = iter(selected)
                pending = {}

                def submit_next():
                    try:
                        case = next(iterator)
                    except StopIteration:
                        return False
                    future = pool.submit(
                        run_case,
                        case,
                        root=root,
                        source=source,
                        template=templates[args.test],
                        test=args.test,
                        config=config,
                        fingerprint=fingerprint,
                        ngspice=ngspice,
                        retry_failed=args.retry_failed,
                    )
                    pending[future] = case
                    return True

                for _ in range(int(config["parallel_jobs"]) * 2):
                    submit_next()
                while pending:
                    done, _ = concurrent.futures.wait(
                        pending, return_when=concurrent.futures.FIRST_COMPLETED
                    )
                    for future in done:
                        pending.pop(future)
                        result = future.result()
                        results.append(result)
                        report_file.write(
                            "CASE "
                            + json.dumps(result, sort_keys=True, allow_nan=False)
                            + "\n"
                        )
                        print(
                            f'{len(results)}/{len(selected)} {result["case_id"]} {result["status"]}'
                            + (" (resumed)" if result["resumed"] else ""),
                            flush=True,
                        )
                        submit_next()
            summary = summarize(results, args.test)
            summary["planned_cases"] = len(all_cases)
            summary["selected_cases"] = len(selected)
            summary["selected_cases_complete"] = (
                len(results) == len(selected)
                and not summary["execution_failed_or_timed_out"]
            )
            summary["full_grid_complete"] = (
                len(results) == len(all_cases) and summary["selected_cases_complete"]
            )
            if args.test == TRIM:
                summary["configured_grid_complete"] = (
                    len(results) == len(all_cases)
                    and not summary["execution_failed_or_timed_out"]
                )
            report_file.write("SUMMARY " + json.dumps(summary, sort_keys=True) + "\n")
            if len(results) == len(all_cases):
                report_file.write("END_OF_" + args.test + "_PVT_RUN\n")
                if summary["execution_failed_or_timed_out"]:
                    report_file.write("COVERAGE_HAS_FAILED_CASES_NOT_FULLY_VALIDATED\n")
                else:
                    report_file.write(
                        "ALL_PLANNED_SIMULATIONS_COMPLETED_CHECK_PERFORMANCE_METRICS\n"
                    )
            else:
                report_file.write(
                    "INCOMPLETE_FULL_GRID_SELECTED_CASES_ONLY\n"
                    if args.case_ids_file is not None
                    else "INCOMPLETE_DEBUG_LIMIT_NOT_FULL_COVERAGE\n"
                )
        print("UPLOAD_THIS_FILE " + str(report), flush=True)
    intended_selection = args.case_ids_file is not None and args.max_cases is None
    return (
        0
        if (len(results) == len(all_cases) or intended_selection)
        and summary["selected_cases_complete"]
        else 2
    )


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print(
            "Interrupted. Completed cases remain cached; re-run to resume.",
            file=sys.stderr,
        )
        sys.exit(130)
    except Exception as exc:
        print("PVT_RUNNER_ERROR: " + str(exc), file=sys.stderr)
        sys.exit(1)

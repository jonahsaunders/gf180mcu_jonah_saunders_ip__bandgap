"""Rerun native-symbol mirror/PSRR checks against the archived code-block results.

Select the pinned GF180MCU-D environment first. Run from the repository root:
  python layout/scripts/confirm_symbol_simulations.py --out layout/build/symbol-rerun

The output must be empty: this workflow never accepts cached simulation results.
It freshly netlists TB10 and retains the actual source, inputs, logs and samples.
The unchanged released RCC model is rerun as an extraction/simulator control.
"""

from collections import Counter
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys

import numpy as np

from layout_paths import REPOSITORY, SCRIPTS

RELEASE = REPOSITORY / "layout/release"
PAIRS = [
    ("M1", "M2"),
    ("M5", "M2"),
    ("PLOAD2", "PLOAD1"),
    ("MSINK2", "MSINK1"),
    ("MFOLD2", "MFOLD1"),
    ("PRMIR", "PREF"),
    ("M18", "M16"),
]
# Fixed before running: 1 micro-dB, 1 pA, 1 microvolt, and 1 ppm of ratio.
TOLERANCES = {
    "psrr_curve_dB": 1e-6,
    "device_current_uA": 1e-6,
    "voltage_V": 1e-6,
    "mirror_ratio_percentage_points": 1e-4,
}


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, folder, log):
    with (folder / log).open("w") as output:
        subprocess.run(
            command,
            cwd=REPOSITORY,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
        )


def core(text):
    start = text.index(".subckt Bandgap_Core_Res ")
    stop = text.index("\n.ends", start)
    return text[start:stop] + "\n.ends Bandgap_Core_Res\n"


def canonical(text):
    joined = re.sub(r"\n\+\s*", " ", core(text))
    return Counter(
        " ".join(line.split())
        for line in joined.splitlines()
        if line.strip() and not line.lstrip().startswith("*")
    )


def prepare(folder):
    evidence = read(RELEASE / "verification.json")
    # Check the fixed baseline before reading or copying any reference data.
    for name, expected in evidence["sha256"].items():
        assert digest(RELEASE / name) == expected, name
    schematic = REPOSITORY / "Bandgap_Core_Res.sch"
    native_proof = read(RELEASE / "schematic-symbols/verification.json")
    assert digest(schematic) == evidence["schematic_sha256"]
    assert digest(schematic) == native_proof["schematic_sha256"]
    for name in ["netlists", "postlayout", "pex", "source"]:
        (folder / name).mkdir()
    for name in ["Bandgap_Core_Res.sch", "TB10_RESISTOR_TRIM.sch"]:
        shutil.copy2(REPOSITORY / name, folder / "source" / name)
    run(
        [
            "xschem",
            "-n",
            "-x",
            "-q",
            "-r",
            "-s",
            "-o",
            str(folder / "netlists"),
            "-N",
            "TB10_RESISTOR_TRIM.spice",
            str(REPOSITORY / "TB10_RESISTOR_TRIM.sch"),
        ],
        folder,
        "xschem.log",
    )
    source = (folder / "netlists/TB10_RESISTOR_TRIM.spice").read_text()
    fresh = core(source)
    assert canonical(fresh) == canonical(
        (RELEASE / "mirror_bias/schematic_source.spice").read_text()
    ), "Fresh symbol circuit differs from the archived circuit"
    header = source.split("* TB_PVT_MODELS_BEGIN", 1)[1].split(
        "* TB_PVT_MODELS_END", 1
    )[0]
    assert header == (RELEASE / "mirror_bias/model_header.spice").read_text()
    (folder / "postlayout/schematic.spice").write_text(fresh)
    for mode in ["rc", "c", "magic_lvs", "rcc"]:
        name = f"pex/Bandgap_Core_Res_{mode}_simulation.spice"
        shutil.copy2(RELEASE / name, folder / name)
    for name in ["primitive_manifest.json", "routing_status.json"]:
        shutil.copy2(RELEASE / name, folder / name)
    # The mirror tool expects the build's GDS key; bind it to the released GDS.
    # This is a simulation workspace, not a new physical verification run.
    write(
        folder / "verification.json",
        {
            "schematic_sha256": digest(schematic),
            "sha256": {
                "filled_candidate.gds": evidence["sha256"]["Bandgap_Core_Res.gds"]
            },
            "physical_verification_source": "layout/release/verification.json",
        },
    )
    return evidence


def ratio(record, pair):
    first, second = pair
    return 100 * (
        record["devices"][first]["current_uA"] / record["devices"][second]["current_uA"]
        - 1
    )


def compare(folder):
    old_mirrors = read(RELEASE / "mirror_bias/summary.json")
    new_mirrors = read(folder / "mirror_bias/summary.json")
    old_psrr = read(RELEASE / "psrr/summary.json")
    new_psrr = read(folder / "psrr/summary.json")
    cases = []
    for mode, supply in product(["schematic", "rcc"], [3.3, 5.0]):
        previous = next(
            r for r in old_mirrors if r["mode"] == mode and r["AVDD_V"] == supply
        )
        current = next(
            r for r in new_mirrors if r["mode"] == mode and r["AVDD_V"] == supply
        )
        before = next(
            r for r in old_psrr if r["name"] == mode and r["supply"] == supply
        )
        after = next(r for r in new_psrr if r["name"] == mode and r["supply"] == supply)
        old_curve = np.loadtxt(
            RELEASE / f"psrr/{mode}_{supply:g}_revised.dat", skiprows=1
        )
        new_curve = np.loadtxt(folder / f"psrr/{mode}_{supply:g}/psrr.dat", skiprows=1)
        assert old_curve.shape == new_curve.shape == (481, 2)
        assert np.array_equal(old_curve[:, 0], new_curve[:, 0])
        assert np.isfinite(new_curve).all()
        assert previous["devices"].keys() == current["devices"].keys()
        currents = {}
        voltages = {}
        for name, old in previous["devices"].items():
            new = current["devices"][name]
            currents[name] = abs(new["current_uA"] - old["current_uA"])
            voltages[name] = max(
                float(np.max(np.abs(np.asarray(new[key]) - np.asarray(old[key]))))
                for key in old
                if key != "current_uA"
            )
        ratios = [
            {
                "pair": "/".join(pair),
                "previous_error_percent": ratio(previous, pair),
                "new_error_percent": ratio(current, pair),
                "change_percentage_points": ratio(current, pair)
                - ratio(previous, pair),
            }
            for pair in PAIRS
        ]
        deltas = {
            "psrr_curve_dB": float(np.max(np.abs(new_curve[:, 1] - old_curve[:, 1]))),
            "device_current_uA": max(currents.values()),
            "voltage_V": max(
                max(voltages.values()),
                abs(current["vref_V"] - previous["vref_V"]),
                abs(after["vref_V"] - before["vref_V"]),
            ),
            "mirror_ratio_percentage_points": max(
                abs(r["change_percentage_points"]) for r in ratios
            ),
        }
        cases.append(
            {
                "mode": mode,
                "AVDD_V": supply,
                "frequency_points": len(new_curve),
                "mos_instances_compared": len(currents),
                "dummy_instances_compared": sum(n.startswith("DUM_") for n in currents),
                "previous_minimum_psrr_dB": before["psrr_min_dB"],
                "new_minimum_psrr_dB": after["psrr_min_dB"],
                "previous_vref_V": before["vref_V"],
                "new_vref_V": after["vref_V"],
                "supply_current_change_uA": after["idd_uA"] - before["idd_uA"],
                "maximum_changes": deltas,
                "mirror_ratios": ratios,
                "device_current_changes_uA": currents,
                "device_bias_changes_V": voltages,
                "passed": all(
                    deltas[key] <= limit for key, limit in TOLERANCES.items()
                ),
            }
        )
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    folder = args.out.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    assert not any(
        folder.iterdir()
    ), "Choose an empty output folder to force fresh runs"
    started = datetime.now(timezone.utc).isoformat()
    evidence = prepare(folder)
    run(
        [
            sys.executable,
            str(SCRIPTS / "diagnose_psrr.py"),
            "--build",
            str(folder),
            "--output",
            str(folder / "psrr"),
            "--models",
            "schematic",
            "rcc",
        ],
        folder,
        "psrr_runs.log",
    )
    # Independently computed, unprobed operating points guard the drain probes.
    write(
        folder / "postlayout/nominal_summary.json",
        [
            {
                "mode": row["name"],
                "supply_V": row["supply"],
                "transient": False,
                "vref_V": row["vref_V"],
            }
            for row in read(folder / "psrr/summary.json")
        ],
    )
    run(
        [
            sys.executable,
            str(SCRIPTS / "screen_mirror_bias.py"),
            "--build",
            str(folder),
            "--out",
            str(folder / "mirror_bias"),
        ],
        folder,
        "mirror_runs.log",
    )
    cases = compare(folder)
    report = {
        "status": "PASS" if all(row["passed"] for row in cases) else "FAIL",
        "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "conditions": {
            "corners": "TT",
            "temperature_C": 25,
            "DVDD_V": 3.3,
            "trim_code": 72,
            "external_load": "none",
            "frequency_Hz": [1, 1e6],
        },
        "tolerances": TOLERANCES,
        "schematic_sha256": digest(REPOSITORY / "Bandgap_Core_Res.sch"),
        "gds_sha256": evidence["sha256"]["Bandgap_Core_Res.gds"],
        "canonical_circuit_equal": True,
        "model_header_equal": True,
        "cache_used": False,
        "fresh_ngspice_runs": 8,
        "scope": "Nominal DC matching and AC PSRR; no statistical mismatch rerun",
        "baseline_sha256": {
            name: evidence["sha256"][name]
            for name in [
                "mirror_bias/summary.json",
                "mirror_bias/schematic_source.spice",
                "psrr/summary.json",
                "schematic-symbols/verification.json",
            ]
        },
        "script_sha256": {
            name: digest(SCRIPTS / name)
            for name in [
                "confirm_symbol_simulations.py",
                "diagnose_psrr.py",
                "screen_mirror_bias.py",
            ]
        },
        "cases": cases,
    }
    write(folder / "comparison.json", report)
    write(
        folder / "file_hashes.json",
        {
            path.relative_to(folder).as_posix(): digest(path)
            for path in sorted(folder.rglob("*"))
            if path.is_file()
        },
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "cases": [
                    {
                        key: row[key]
                        for key in [
                            "mode",
                            "AVDD_V",
                            "new_minimum_psrr_dB",
                            "maximum_changes",
                        ]
                    }
                    for row in cases
                ],
            },
            indent=2,
        ),
        flush=True,
    )
    assert (
        report["status"] == "PASS"
    ), "Simulation changes exceed fixed comparison tolerances"


if __name__ == "__main__":
    main()

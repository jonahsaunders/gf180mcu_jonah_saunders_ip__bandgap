"""Measure nominal mirror currents and terminal biases with ideal drain probes.

Diagnostic copies only: zero-volt sources in series with MOS drains preserve
the DC circuit. This is not a PVT, mismatch, or complete-RC qualification.
Uses archived, hashed schematic/model inputs; checks a build netlist if present.
"""

from collections import Counter, defaultdict
from itertools import product
from pathlib import Path
import argparse
import hashlib
import json
import re
import subprocess

LAYOUT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--build", type=Path, help="Verified build directory for a new layout revision"
)
parser.add_argument("--out", type=Path)
parser.add_argument(
    "--reference-ac",
    type=Path,
    help="Independent diagnose_psrr output to validate the probed DC solution",
)
parser.add_argument("--release", type=Path, default=LAYOUT / "release")
parser.add_argument(
    "--schematic",
    type=Path,
    default=LAYOUT.parent / "Bandgap_Core_Res.sch",
    help="Exact source schematic associated with the selected build/release",
)
args = parser.parse_args()
ROOT = args.build or args.release
OUT = args.out or (ROOT / "mirror_bias" if args.build else LAYOUT / "build/mirror_bias")
OUT.mkdir(parents=True, exist_ok=True)
inventory = json.loads((ROOT / "primitive_manifest.json").read_text())
aliases = json.loads((ROOT / "routing_status.json").read_text())["aliases"]
devices = {d["name"]: d for d in inventory["devices"]}
release = json.loads((ROOT / "verification.json").read_text())
rcc_path = ROOT / "pex/Bandgap_Core_Res_rcc_simulation.spice"
schematic_hash = hashlib.sha256(args.schematic.read_bytes()).hexdigest()
assert schematic_hash == release["schematic_sha256"]
if args.build:
    source = (ROOT / "netlists/TB10_RESISTOR_TRIM.spice").read_text()
    header = source.split("* TB_PVT_MODELS_BEGIN", 1)[1].split(
        "* TB_PVT_MODELS_END", 1
    )[0]
    start = source.index(".subckt Bandgap_Core_Res ")
    stop = source.index("\n.ends", start)
    schematic = source[start:stop] + "\n.ends Bandgap_Core_Res\n"
    saved_schematic = ROOT / "postlayout/schematic.spice"
    if saved_schematic.exists():
        assert schematic == saved_schematic.read_text()
    (OUT / "model_header.spice").write_text(header)
    (OUT / "schematic_source.spice").write_text(schematic)
    provenance = {
        "schematic_sha256": schematic_hash,
        "rcc_sha256": hashlib.sha256(rcc_path.read_bytes()).hexdigest(),
        "gds_sha256": release["sha256"]["filled_candidate.gds"],
        "archived_input_sha256": {
            name: hashlib.sha256((OUT / name).read_bytes()).hexdigest()
            for name in ["model_header.spice", "schematic_source.spice"]
        },
    }
    (OUT / "input_manifest.json").write_text(json.dumps(provenance, indent=2) + "\n")
    nominal_path = ROOT / "postlayout/nominal_summary.json"
else:
    archived = ROOT / "mirror_bias"
    provenance = json.loads((archived / "input_manifest.json").read_text())
    assert schematic_hash == provenance["schematic_sha256"]
    assert (
        hashlib.sha256(rcc_path.read_bytes()).hexdigest()
        == release["sha256"]["pex/Bandgap_Core_Res_rcc_simulation.spice"]
        == provenance["rcc_sha256"]
    )
    for name, expected in provenance["archived_input_sha256"].items():
        assert (
            hashlib.sha256((archived / name).read_bytes()).hexdigest() == expected
        ), name
    header = (archived / "model_header.spice").read_text()
    schematic = (archived / "schematic_source.spice").read_text()
    nominal_path = ROOT / "nominal_screen.json"


def canonical(node):
    node = node.split(".", 1)[0]
    while node in aliases:
        node = aliases[node]
    return node


def unit_key(model, width, length, nodes):
    return (model, round(width, 4), round(length, 4), *map(canonical, nodes))


keys = {}
for name, device in devices.items():
    if "fet_" in device["model"]:
        key = unit_key(device["model"], device["w"], device["l"], device["nodes"])
        assert key not in keys, (name, keys.get(key))
        keys[key] = name


def diagnostic_circuit(text, mode):
    # Join SPICE continuation lines only; no expression evaluation is used.
    logical = []
    for line in text.splitlines():
        if line.startswith("+"):
            logical[-1] += " " + line[1:]
        else:
            logical.append(line)
    result, probes = [], []
    for line in logical:
        tokens = line.split()
        if not (
            len(tokens) > 6
            and tokens[0].lower().startswith("x")
            and "fet_" in tokens[5]
        ):
            result.append(line)
            continue
        nodes, model = tokens[1:5], tokens[5]
        if mode == "schematic":
            name = tokens[0][1:]
            assert name in devices, name
        else:
            width = float(re.search(r"\bw=([\d.]+)u\b", line, re.I)[1])
            length = float(re.search(r"\bl=([\d.]+)u\b", line, re.I)[1])
            key = unit_key(model, width, length, nodes)
            if key not in keys:
                swapped = [nodes[2], nodes[1], nodes[0], nodes[3]]
                key = unit_key(model, width, length, swapped)
                assert key in keys, (tokens[0], key)
                raise AssertionError(
                    "Swapped D/S needs explicit probe-direction handling"
                )
            name = keys[key]
        index = len(probes)
        probe = f"VAUD{index}"
        measured = f"audit_d{index}"
        result.append(f"{probe} {nodes[0]} {measured} 0")
        tokens[1] = measured
        result.append(" ".join(tokens))
        probes.append(
            {
                "index": index,
                "source": name,
                "instance": tokens[0],
                "nodes": [measured, *nodes[1:]],
                "voltage_probe": probe,
                "multiplicity": (
                    int(float(re.search(r"\bm=([\d.]+)", line, re.I)[1]))
                    if re.search(r"\bm=([\d.]+)", line, re.I)
                    else 1
                ),
            }
        )
    counts = defaultdict(int)
    for probe in probes:
        counts[probe["source"]] += 1 if mode == "schematic" else probe["multiplicity"]
    for name, device in devices.items():
        if "fet_" in device["model"]:
            assert counts[name] == (1 if mode == "schematic" else device["m"]), (
                name,
                counts[name],
            )
    return "\n".join(result) + "\n", probes


def node_voltage(node):
    # Ports are flattened to top-level names by ngspice.
    ports = {
        "avdd": "avdd",
        "avss": "0",
        "dvdd": "dvdd",
        "dvss": "0",
        "vref": "vref",
        **{f"b{i}": f"b{i}" for i in range(7)},
    }
    mapped = ports.get(node, f"xdut.{node}")
    return "0" if mapped == "0" else f"v({mapped})"


def run_case(mode, supply):
    folder = OUT / f"{mode}_{supply:g}V"
    folder.mkdir(exist_ok=True)
    original = schematic if mode == "schematic" else rcc_path.read_text()
    diagnostic, probes = diagnostic_circuit(original, mode)
    (folder / "diagnostic.spice").write_text(diagnostic)
    control = [
        ".control",
        "set noaskquit",
        "set numdgt=15",
        "op",
        "print v(vref) i(vdd)",
    ]
    for probe in probes:
        control += [
            f'echo DEVICE {probe["index"]}',
            f'let aid=i(v.xdut.{probe["voltage_probe"].lower()})',
        ]
        for label, node in zip(["avd", "avg", "avs", "avb"], probe["nodes"]):
            control.append(f"let {label}={node_voltage(node)}")
        control.append("print aid avd avg avs avb")
    control += ["quit", ".endc", ".end"]
    deck = [
        "* Diagnostic DC mirror currents; ideal series drain probes only.",
        header,
        ".options klu num_threads=1",
        ".include diagnostic.spice",
        "XDUT avdd 0 vref b0 b1 b2 b3 b4 b5 b6 0 dvdd Bandgap_Core_Res",
        f"VDD avdd 0 {supply}",
        "VDVDD dvdd 0 3.3",
        *[f"VB{i} b{i} 0 {3.3 if 72 & (1 << i) else 0}" for i in range(7)],
        *control,
    ]
    (folder / "test.spice").write_text("\n".join(deck) + "\n")
    with (folder / "ngspice.log").open("w") as log:
        process = subprocess.run(
            ["ngspice", "-b", "test.spice"],
            cwd=folder,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    log = (folder / "ngspice.log").read_text()
    assert process.returncode == 0 and not re.search(
        r"(?im)error|no such|not available|failed", log
    ), folder
    records = []
    for block in log.split("DEVICE ")[1:]:
        index = int(block.splitlines()[0])
        values = {
            key: float(value)
            for key, value in re.findall(
                r"(?m)^(aid|avd|avg|avs|avb)\s*=\s*([-+\d.eE]+)", block
            )
        }
        assert len(values) == 5, (index, block[:200])
        records.append({**probes[index], **values})
    assert len(records) == len(probes)
    summary = {}
    for name in sorted({probe["source"] for probe in probes}):
        rows = [r for r in records if r["source"] == name]
        units = sum(row["multiplicity"] for row in rows)

        def mean_difference(first, second):
            return (
                sum((row[first] - row[second]) * row["multiplicity"] for row in rows)
                / units
            )

        summary[name] = {
            "current_uA": abs(sum(row["aid"] for row in rows)) * 1e6,
            "vds_mean_V": mean_difference("avd", "avs"),
            "vgs_mean_V": mean_difference("avg", "avs"),
            "vsb_mean_V": mean_difference("avs", "avb"),
            "source_voltage_range_V": [
                min(row["avs"] for row in rows),
                max(row["avs"] for row in rows),
            ],
        }
    vref = float(re.search(r"(?m)^v\(vref\)\s*=\s*([-+\d.eE]+)", log)[1])
    if args.reference_ac:
        reference_folder = args.reference_ac / f"{mode}_{supply:g}"
        reference_text = (reference_folder / "dut.spice").read_text()
        reference = json.loads((reference_folder / "metrics.json").read_text())

        def circuit_records(text):
            rows = re.sub(r"\n\+\s*", " ", text).splitlines()
            return Counter(
                " ".join(row.split())
                for row in rows
                if row.strip()
                and not row.lstrip().startswith("*")
                and not (row[0] in "RC" and row.split()[1] == row.split()[2])
            )

        assert circuit_records(reference_text) == circuit_records(original)
        fingerprint = hashlib.sha256(
            (reference_text + header + repr(supply)).encode()
        ).hexdigest()
        assert reference["sha256"] == fingerprint
        expected = reference["vref_V"]
    else:
        expected = saved_nominal_voltage(mode, supply)
    assert abs(vref - expected) < 1e-6, (mode, supply, vref, expected)
    result = {
        "mode": mode,
        "AVDD_V": supply,
        "DVDD_V": 3.3,
        "trim_code": 72,
        "temperature_C": 25,
        "vref_V": vref,
        "vref_difference_from_saved_screen_V": vref - expected,
        "reference_ac": str(args.reference_ac) if args.reference_ac else None,
        "devices": summary,
        "unit_records": records,
        "original_circuit_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "deck_sha256": hashlib.sha256((folder / "test.spice").read_bytes()).hexdigest(),
    }
    (folder / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(mode, supply, "VREF", vref, "MOS probes", len(probes), flush=True)
    return result


def saved_nominal_voltage(mode, supply):
    nominal_records = (
        json.loads(nominal_path.read_text())
        if nominal_path.exists()
        else [
            json.loads(
                (
                    ROOT
                    / "postlayout"
                    / f"{mode}_typical_typical_typical_{supply:g}"
                    / "metrics.json"
                ).read_text()
            )
        ]
    )
    return next(
        row["vref_V"]
        for row in nominal_records
        if row["mode"] == mode and row["supply_V"] == supply and not row["transient"]
    )


if __name__ == "__main__":
    records = [
        run_case(mode, supply)
        for mode, supply in product(["schematic", "rcc"], [3.3, 5.0])
    ]
    (OUT / "summary.json").write_text(json.dumps(records, indent=2) + "\n")

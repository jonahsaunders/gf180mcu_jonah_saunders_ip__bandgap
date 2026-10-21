"""Measure nominal mirror currents and terminal biases with ideal drain probes.

Diagnostic copies only: zero-volt sources in series with MOS drains preserve
the DC circuit. This is not a PVT, mismatch, or complete-RC qualification.
Uses archived, hashed schematic/model inputs; checks a build netlist if present.
"""

from collections import defaultdict
from itertools import product
from pathlib import Path
import hashlib
import json
import re
import subprocess

LAYOUT = Path(__file__).resolve().parent.parent
OUT = LAYOUT / "reviews/2026-10-21-mirrors/bias"
OUT.mkdir(parents=True, exist_ok=True)
inventory = json.loads((LAYOUT / "release/primitive_manifest.json").read_text())
aliases = json.loads((LAYOUT / "release/routing_status.json").read_text())["aliases"]
devices = {d["name"]: d for d in inventory["devices"]}
provenance = json.loads((OUT / "input_manifest.json").read_text())
release = json.loads((LAYOUT / "release/verification.json").read_text())
for name, expected in provenance["archived_input_sha256"].items():
    assert hashlib.sha256((OUT / name).read_bytes()).hexdigest() == expected, name
assert (
    hashlib.sha256((LAYOUT.parent / "Bandgap_Core_Res.sch").read_bytes()).hexdigest()
    == release["schematic_sha256"]
    == provenance["schematic_sha256"]
)
rcc_path = LAYOUT / "release/pex/Bandgap_Core_Res_rcc_simulation.spice"
assert (
    hashlib.sha256(rcc_path.read_bytes()).hexdigest()
    == release["sha256"]["pex/Bandgap_Core_Res_rcc_simulation.spice"]
    == provenance["rcc_sha256"]
)
header = (OUT / "model_header.spice").read_text()
schematic = (OUT / "schematic_source.spice").read_text()
build_netlist = LAYOUT / "build/netlists/TB10_RESISTOR_TRIM.spice"
if build_netlist.exists():
    source = build_netlist.read_text()
    build_header = source.split("* TB_PVT_MODELS_BEGIN", 1)[1].split(
        "* TB_PVT_MODELS_END", 1
    )[0]
    start = source.index(".subckt Bandgap_Core_Res ")
    stop = source.index("\n.ends", start)
    build_schematic = source[start:stop] + "\n.ends Bandgap_Core_Res\n"
    assert header == build_header and schematic == build_schematic


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
            }
        )
    counts = defaultdict(int)
    for probe in probes:
        counts[probe["source"]] += 1
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
        summary[name] = {
            "current_uA": abs(sum(row["aid"] for row in rows)) * 1e6,
            "vds_mean_V": sum(row["avd"] - row["avs"] for row in rows) / len(rows),
            "vgs_mean_V": sum(row["avg"] - row["avs"] for row in rows) / len(rows),
            "vsb_mean_V": sum(row["avs"] - row["avb"] for row in rows) / len(rows),
            "source_voltage_range_V": [
                min(row["avs"] for row in rows),
                max(row["avs"] for row in rows),
            ],
        }
    vref = float(re.search(r"(?m)^v\(vref\)\s*=\s*([-+\d.eE]+)", log)[1])
    expected = next(
        row["vref_V"]
        for row in json.loads((LAYOUT / "release/nominal_screen.json").read_text())
        if row["mode"] == mode and row["supply_V"] == supply and not row["transient"]
    )
    assert abs(vref - expected) < 1e-6, (mode, supply, vref, expected)
    result = {
        "mode": mode,
        "AVDD_V": supply,
        "DVDD_V": 3.3,
        "trim_code": 72,
        "temperature_C": 25,
        "vref_V": vref,
        "vref_difference_from_saved_screen_V": vref - expected,
        "devices": summary,
        "unit_records": records,
        "original_circuit_sha256": hashlib.sha256(original.encode()).hexdigest(),
        "deck_sha256": hashlib.sha256((folder / "test.spice").read_bytes()).hexdigest(),
    }
    (folder / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(mode, supply, "VREF", vref, "MOS probes", len(probes), flush=True)
    return result


if __name__ == "__main__":
    records = [
        run_case(mode, supply)
        for mode, supply in product(["schematic", "rcc"], [3.3, 5.0])
    ]
    (OUT / "summary.json").write_text(json.dumps(records, indent=2) + "\n")

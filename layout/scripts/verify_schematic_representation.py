"""Verify a schematic-only refactor against the released circuit and GDS.

Run after selecting the GF180MCU-D environment. No devices are filtered from
LVS. The reference is generated from a fresh TB10 netlist, independent of the
saved layout placement. Ideal zero-volt probes become net aliases for LVS.
"""

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import sys

from layout_paths import BUILD_DIR, PDK, REPOSITORY
from layout_flow import lvs

RELEASE = REPOSITORY / "layout/release"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def circuit_text(text):
    match = re.search(r"(?ims)^\.subckt Bandgap_Core_Res .*?^\.ends[^\n]*", text)
    assert match, "Missing trim-inclusive core"
    return (
        re.sub(r"(?im)^\.ends[^\n]*$", ".ends Bandgap_Core_Res", match.group(0)) + "\n"
    )


def canonical_lines(text):
    joined = re.sub(r"\n\+\s*", " ", circuit_text(text))
    return Counter(
        " ".join(line.split())
        for line in joined.splitlines()
        if line.strip() and not line.lstrip().startswith("*")
    )


def run(arguments, log):
    with (BUILD_DIR / log).open("w") as output:
        subprocess.run(
            arguments,
            cwd=REPOSITORY,
            stdout=output,
            stderr=subprocess.STDOUT,
            check=True,
        )


def make_lvs_reference(inventory):
    """Expand source multiplicities and retain every native dummy instance."""
    aliases = {}

    def root(node):
        while node in aliases:
            node = aliases[node]
        return node

    for probe in inventory["probes"]:
        first, second = map(root, probe["nodes"])
        if first == second:
            continue
        # Keep public pin names if a zero-volt probe ever touches one.
        if first in inventory["pins"]:
            assert second not in inventory["pins"]
            aliases[second] = first
        else:
            aliases[first] = second
    lines = [
        "* Fresh Xschem netlist; all device units retained.",
        ".subckt Bandgap_Core_Res " + " ".join(inventory["pins"]),
    ]
    for device in inventory["devices"]:
        model = device["model"]
        prefix = (
            "M"
            if "fet_" in model
            else (
                "Q"
                if model.startswith("pnp")
                else "C" if model.startswith("cap_") else "R"
            )
        )
        for index in range(device["m"]):
            line = (
                f'{prefix}{device["name"]}_{index} '
                + " ".join(map(root, device["nodes"]))
                + f" {model}"
            )
            if prefix != "Q":
                line += f' W={device["w"]}u L={device["l"]}u'
            lines.append(line)
    lines.append(".ends Bandgap_Core_Res")
    reference = BUILD_DIR / "reference.spice"
    reference.write_text("\n".join(lines) + "\n")
    return reference


def main():
    netlists = BUILD_DIR / "netlists"
    netlists.mkdir(exist_ok=True)
    run(
        [
            "xschem",
            "-n",
            "-x",
            "-q",
            "-r",
            "-s",
            "-o",
            str(netlists),
            "-N",
            "TB10_RESISTOR_TRIM.spice",
            str(REPOSITORY / "TB10_RESISTOR_TRIM.sch"),
        ],
        "xschem.log",
    )
    netlist = netlists / "TB10_RESISTOR_TRIM.spice"
    fresh = circuit_text(netlist.read_text())
    archived_path = RELEASE / "mirror_bias/schematic_source.spice"
    assert canonical_lines(fresh) == canonical_lines(
        archived_path.read_text()
    ), "Generated circuit changed"
    (BUILD_DIR / "schematic_source.spice").write_text(fresh)
    run(
        [
            sys.executable,
            str(REPOSITORY / "layout/scripts/audit_circuit.py"),
            str(netlist),
        ],
        "audit_circuit.log",
    )
    inventory = json.loads((BUILD_DIR / "circuit.json").read_text())
    assert not inventory["off_grid"]
    old = json.loads((RELEASE / "primitive_manifest.json").read_text())
    fields = ["name", "model", "nodes", "w", "l", "m"]

    def devices(data):
        return sorted(
            [{key: row[key] for key in fields} for row in data["devices"]],
            key=lambda row: row["name"],
        )

    assert devices(inventory) == devices(old), "Device inventory changed"
    dummies = [row for row in inventory["devices"] if row["name"].startswith("DUM_")]
    assert len(dummies) == 13 and sum(row["m"] for row in dummies) == 261
    assert sum(row["m"] for row in inventory["devices"]) == 533
    source = REPOSITORY / "Bandgap_Core_Res.sch"
    source_text = source.read_text()
    assert "code_shown.sym" not in source_text
    assert (
        len(
            re.findall(r"C \{symbols/[pn]fet_05v0.sym\} [^\n]+\{name=DUM_", source_text)
        )
        == 13
    )
    reference = make_lvs_reference(inventory)
    gds = BUILD_DIR / "filled_candidate.gds"
    shutil.copy2(RELEASE / "Bandgap_Core_Res.gds", gds)
    assert (
        digest(gds)
        == json.loads((RELEASE / "verification.json").read_text())["sha256"][
            "Bandgap_Core_Res.gds"
        ]
    )
    print(
        "Netlist and all 533 device units unchanged; running full core LVS.", flush=True
    )
    report = BUILD_DIR / "filled.lvsdb"
    lvs(reference, report, "filled_lvs.log")
    (BUILD_DIR / "filled.lvsdb.gz").write_bytes(
        gzip.compress(report.read_bytes(), mtime=0)
    )
    result = {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "lvs_match": True,
        "no_devices_filtered": True,
        "canonical_spice_equal_to_archived_simulation_input": True,
        "all_models_parameters_nodes_multiplicities_unchanged": True,
        "native_dummy_symbols": 13,
        "dummy_device_units": 261,
        "functional_device_units": 272,
        "total_device_units": 533,
        "schematic_sha256": digest(source),
        "archived_simulation_source_sha256": digest(archived_path),
        "gds_sha256": digest(gds),
        "lvs_deck_sha256": digest(PDK / "libs.tech/klayout/tech/lvs/gf180mcu.lvs"),
        "lvsdb_uncompressed_sha256": digest(report),
        "sha256": {
            name: digest(BUILD_DIR / name)
            for name in [
                "schematic_source.spice",
                "reference.spice",
                "circuit.json",
                "filled_lvs.log",
                "filled.lvsdb.gz",
                "filled.cir",
                "xschem.log",
                "audit_circuit.log",
            ]
        },
        "note": "Native transistor symbols replace the dummy code block. Their instance format retains the original W/L/m-only SPICE parameter list and therefore the same PDK defaults. Existing simulation inputs and results are retained; GDS is unchanged.",
    }
    (BUILD_DIR / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        "PASS: native-symbol schematic matches the released GDS; all 261 dummy units included."
    )


if __name__ == "__main__":
    main()

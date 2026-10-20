"""Rebuild and verify the trim-inclusive core with explicit, inspectable stages.

All tool arguments are passed as lists. No shell command is constructed from
schematic text. Physical verification always uses the installed, unedited PDK.
"""

from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import klayout.db as k

from layout_paths import BUILD_DIR, PDK, REPOSITORY, SCRIPTS


def run(arguments, log_name, cwd=BUILD_DIR, input_file=None):
    """Run a tool with bounded output, preserving its complete log on disk."""
    print("Running", log_name, flush=True)
    log_path = BUILD_DIR / log_name
    with log_path.open("w") as log:
        if input_file is None:
            subprocess.run(
                arguments, cwd=cwd, stdout=log, stderr=subprocess.STDOUT, check=True
            )
        else:
            with input_file.open() as script:
                subprocess.run(
                    arguments,
                    cwd=cwd,
                    stdin=script,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=True,
                )


def stage(name):
    run([sys.executable, str(SCRIPTS / f"{name}.py")], f"{name}.log")


def hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_lvs_match(report):
    """The KLayout process can exit zero after a mismatch; inspect the database."""
    database = k.LayoutVsSchematic()
    database.read(str(report))
    cross_reference = database.xref()
    pairs = list(cross_reference.each_circuit_pair())
    if not pairs or any(pair.status() != cross_reference.Match for pair in pairs):
        raise RuntimeError(f"LVS did not match: {report}")


def lvs(schematic, report, log):
    arguments = [
        "klayout",
        "-b",
        "-r",
        str(PDK / "libs.tech/klayout/tech/lvs/gf180mcu.lvs"),
    ]
    parameters = {
        "input": BUILD_DIR / "filled_candidate.gds",
        "schematic": schematic,
        "report": report,
        "target_netlist": report.with_suffix(".cir"),
        "topcell": "Bandgap_Core_Res",
        "metal_top": "11K",
        "metal_level": "5LM",
        "mim_option": "B",
        "poly_res": "1k",
        "mim_cap": "2",
        "run_mode": "flat",
        "implicit_connect": "false",
        "top_lvl_pins": "true",
        "combine": "true",
    }
    for name, value in parameters.items():
        arguments += ["-rd", f"{name}={value}"]
    run(arguments, log)
    require_lvs_match(report)


def verify():
    drc_report = BUILD_DIR / "filled_drc.lyrdb"
    run(
        [
            "klayout",
            "-b",
            "-r",
            str(PDK / "libs.tech/klayout/tech/drc/gf180mcu.drc"),
            "-rd",
            f'input={BUILD_DIR / "filled_candidate.gds"}',
            "-rd",
            f"report={drc_report}",
            "-rd",
            "variant=D",
            "-rd",
            "run_mode=flat",
            "-rd",
            "workers=4",
            "-rd",
            "threads=1",
        ],
        "filled_drc.log",
    )
    violations = len(ET.parse(drc_report).findall(".//items/item"))
    log = (BUILD_DIR / "filled_drc.log").read_text()
    if violations or "DRC RESULT: SUCCESS (0 violations)" not in log or "ERROR:" in log:
        raise RuntimeError(
            f"DRC failed: {violations} reported violations; inspect filled_drc.log"
        )
    lvs(BUILD_DIR / "reference.spice", BUILD_DIR / "filled.lvsdb", "filled_lvs.log")
    inventory = json.loads((BUILD_DIR / "primitive_manifest.json").read_text())
    placement = json.loads((BUILD_DIR / "placement.json").read_text())
    routing = json.loads((BUILD_DIR / "routing_status.json").read_text())
    assert not placement["unplaced"] and not routing["failed"]
    assert len(placement["placed"]) == sum(d["m"] for d in inventory["devices"])
    physical = k.Layout()
    physical.read(str(BUILD_DIR / "filled_candidate.gds"))
    boundary = physical.top_cell().dbbox()
    files = [
        "filled_candidate.gds",
        "reference.spice",
        "filled_drc.lyrdb",
        "filled.lvsdb",
    ]
    evidence = {
        "status": "CORE_DRC_LVS_PASS_NOT_TAPEOUT_QUALIFIED",
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "drc_violations": violations,
        "lvs_match": True,
        "expanded_device_units": len(placement["placed"]),
        "bounds_um": [boundary.left, boundary.bottom, boundary.right, boundary.top],
        "sha256": {name: hash_file(BUILD_DIR / name) for name in files},
        "schematic_sha256": hash_file(REPOSITORY / "Bandgap_Core_Res.sch"),
        "pdk_version": "f6eeac7dad085ffcc829ccfd721f7b4ce39edcf7",
        "container": "hpretl/iic-osic-tools@sha256:5d6adf1f437cd0f2f8f8614488ec3c247ba8c768f4663a25d5e997b30ccb13b0",
        "drc_deck_sha256": hash_file(PDK / "libs.tech/klayout/tech/drc/gf180mcu.drc"),
        "lvs_deck_sha256": hash_file(PDK / "libs.tech/klayout/tech/lvs/gf180mcu.lvs"),
        "open_items": [
            "Assigned wrapper and dimensions",
            "Complete RC including ground network",
            "Matched-route symmetry and edge dummies",
            "EM/IR and full analog PVT qualification",
        ],
    }
    (BUILD_DIR / "verification.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print("Core DRC: 0 violations. Core LVS: matched.", flush=True)


def build():
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
        cwd=REPOSITORY,
    )
    run(
        [
            sys.executable,
            str(SCRIPTS / "audit_circuit.py"),
            str(netlists / "TB10_RESISTOR_TRIM.spice"),
        ],
        "audit_circuit.log",
    )
    inventory = json.loads((BUILD_DIR / "circuit.json").read_text())
    if inventory["off_grid"]:
        raise RuntimeError("Schematic contains off-grid device dimensions")
    stage("generate_primitives")
    primitives = BUILD_DIR / "primitives"
    primitives.mkdir(exist_ok=True)
    # Magic never opens stale read-only primitive files from a previous build.
    with tempfile.TemporaryDirectory(prefix="primitive-", dir=BUILD_DIR) as scratch:
        run(
            [
                "magic",
                "-dnull",
                "-noconsole",
                "-rcfile",
                str(PDK / "libs.tech/magic/gf180mcuD.magicrc"),
            ],
            "magic_primitives.log",
            cwd=scratch,
            input_file=BUILD_DIR / "generate_primitives.tcl",
        )
        for path in Path(scratch).glob("bg_primitive_*"):
            shutil.copy2(path, primitives / path.name)
    for name in [
        "place_layout",
        "route_physical",
        "strengthen_power",
        "add_density_fill",
    ]:
        stage(name)
    verify()
    stage("draw_layout")


def extract():
    stage("prepare_pex_view")
    with tempfile.TemporaryDirectory(prefix="extraction-", dir=BUILD_DIR) as scratch:
        run(
            [
                "magic",
                "-dnull",
                "-noconsole",
                "-rcfile",
                str(PDK / "libs.tech/magic/gf180mcuD.magicrc"),
            ],
            "extract_parasitics.log",
            cwd=scratch,
            input_file=SCRIPTS / "extract_parasitics.tcl",
        )
        for path in Path(scratch).glob("Bandgap_Core_Res*"):
            if path.suffix in (".spice", ".ext"):
                shutil.copy2(path, BUILD_DIR / "pex" / path.name)
    stage("audit_pex_topology")
    for name in ["magic_lvs", "rc"]:
        lvs(
            BUILD_DIR / f"pex/audit_{name}.spice",
            BUILD_DIR / f"pex/audit_{name}.lvsdb",
            f"audit_{name}.log",
        )
    print(
        "Extraction topology matches; ground resistance is explicitly omitted.",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["build", "verify", "extract", "screen", "pvt", "all"]
    )
    args = parser.parse_args()
    os.environ["BANDGAP_LAYOUT_BUILD"] = str(BUILD_DIR)
    os.environ["OMP_NUM_THREADS"] = "1"
    if args.command in ("build", "all"):
        build()
    if args.command == "verify":
        verify()
    if args.command in ("extract", "all"):
        extract()
    if args.command in ("screen", "all"):
        stage("screen_extracted")
        stage("screen_power_sequence")
    if args.command in ("pvt", "all"):
        stage("screen_pvt_dc")


if __name__ == "__main__":
    main()

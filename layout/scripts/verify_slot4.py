"""Verify gds/slot_4.gds.gz with the installed, unedited GF180MCU-D decks.

Run inside the pinned IIC-OSIC-TOOLS image (layout/verify_slot4.sh):

1. rebuild the slot in memory and require it to equal the committed GDS,
2. full foundry DRC on top cell slot_4 (same options as the core release),
3. the foundry antenna deck, when the installed PDK ships one,
4. foundry LVS of slot_4 against layout/slot4/slot_4.spice.

Every check runs even if an earlier one fails; the summary printed at the end
and written to slot4_verification.json is the single source of the verdict.
"""

from collections import Counter
import gzip
import hashlib
import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

import klayout.db as k

from layout_paths import BUILD_DIR, PDK, REPOSITORY, SCRIPTS

OUT = BUILD_DIR / "slot4"
GDS_GZ = REPOSITORY / "gds/slot_4.gds.gz"
NETLIST = REPOSITORY / "layout/slot4/slot_4.spice"
CORE_GDS = REPOSITORY / "layout/release/Bandgap_Core_Res.gds"
DRC_DIR = PDK / "libs.tech/klayout/tech/drc"
LVS_DECK = PDK / "libs.tech/klayout/tech/lvs/gf180mcu.lvs"
TOP = "slot_4"


def run(arguments, log_name):
    """Run a tool, keep its whole log on disk, return (exit status, log text)."""
    print("Running", log_name, flush=True)
    log_path = OUT / log_name
    with log_path.open("w") as log:
        status = subprocess.run(
            arguments, cwd=OUT, stdout=log, stderr=subprocess.STDOUT, check=False
        ).returncode
    return status, log_path.read_text(errors="replace")


def klayout(deck, parameters, log_name):
    arguments = ["klayout", "-b", "-r", str(deck)]
    for name, value in parameters.items():
        arguments += ["-rd", f"{name}={value}"]
    return run(arguments, log_name)


def drc_items(report):
    """Violation count per rule from a KLayout report database."""
    if not report.exists():
        return None
    counts = Counter()
    for item in ET.parse(report).findall(".//items/item"):
        counts[(item.findtext("category") or "?").strip("'")] += 1
    return dict(sorted(counts.items()))


def drc_examples(report, limit=6):
    """A few marker coordinates per violated rule, for the job log."""
    if not report.exists():
        return
    seen = Counter()
    for item in ET.parse(report).findall(".//items/item"):
        rule = (item.findtext("category") or "?").strip("'")
        seen[rule] += 1
        if seen[rule] <= limit:
            values = [v.text for v in item.findall("values/value") if v.text]
            print(f"  {rule}: {' '.join(values)[:300]}")


def deck_text(pattern):
    """Print the installed deck's own definition of the rules that fired."""
    expression = re.compile(pattern)
    files = sorted(f for f in DRC_DIR.rglob("*") if f.is_file())
    print("Deck files:", [str(f.relative_to(DRC_DIR)) for f in files][:60])
    for deck in files:
        lines = deck.read_text(errors="replace").splitlines()
        for number, line in enumerate(lines):
            if expression.search(line):
                print(f"--- {deck.relative_to(DRC_DIR)}:{number + 1}")
                for context in lines[max(0, number - 9) : number + 2]:
                    print("   ", context[:330])


def lvs_result(report):
    """Circuit-pair verdicts plus the first few mismatching objects."""
    if not report.exists():
        return {"match": False, "detail": ["no LVS database written"]}
    database = k.LayoutVsSchematic()
    database.read(str(report))
    xref = database.xref()
    pairs = list(xref.each_circuit_pair())
    detail = []

    def name(item):
        if item is None:
            return "-"
        for attribute in ("expanded_name", "name"):
            value = getattr(item, attribute, None)
            if callable(value):
                return str(value())
            if value is not None:
                return str(value)
        return str(item)

    for pair in pairs:
        if pair.status() == xref.Match:
            continue
        for kind, iterator in (
            ("net", xref.each_net_pair),
            ("device", xref.each_device_pair),
            ("pin", xref.each_pin_pair),
        ):
            bad = [p for p in iterator(pair) if p.status() != xref.Match]
            detail.append(f"{kind}: {len(bad)} not matched")
            for item in bad[:25]:
                detail.append(
                    f"  {kind} layout={name(item.first())} "
                    f"schematic={name(item.second())} status={item.status()}"
                )

    def census(netlist):
        counts = Counter()
        for circuit in netlist.each_circuit():
            for device in circuit.each_device():
                counts[device.device_class().name] += 1
        return dict(sorted(counts.items()))

    def top_pins(netlist):
        return sorted(
            pin.name() for c in netlist.each_circuit_top_down() for pin in c.each_pin()
        )[:60]

    layout_netlist, schematic = database.netlist(), database.reference()
    return {
        "match": bool(pairs) and all(p.status() == xref.Match for p in pairs),
        "circuit_pairs": len(pairs),
        "layout_devices_after_combination": census(layout_netlist),
        "schematic_devices_after_combination": census(schematic),
        "layout_top_pins": top_pins(layout_netlist),
        "detail": detail,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"top_cell": TOP}

    status, log = run(
        [sys.executable, str(SCRIPTS / "build_slot4.py"), "--check"], "rebuild.log"
    )
    summary["rebuild_matches_committed_gds"] = status == 0
    print(log.strip()[-600:])

    gds = OUT / "slot_4.gds"
    gds.write_bytes(gzip.decompress(GDS_GZ.read_bytes()))
    summary["gds_sha256_uncompressed"] = hashlib.sha256(gds.read_bytes()).hexdigest()
    summary["gds_gz_sha256"] = hashlib.sha256(GDS_GZ.read_bytes()).hexdigest()
    layout = k.Layout()
    layout.read(str(gds))
    tops = [cell.name for cell in layout.top_cells()]
    summary["top_cells"] = tops

    print("Installed DRC decks:", sorted(p.name for p in DRC_DIR.glob("*.drc")))
    drc_report = OUT / "slot4_drc.lyrdb"
    status, log = klayout(
        DRC_DIR / "gf180mcu.drc",
        {
            "input": gds,
            "report": drc_report,
            "topcell": TOP,
            "variant": "D",
            "run_mode": "flat",
            "workers": 4,
            "threads": 1,
        },
        "slot4_drc.log",
    )
    items = drc_items(drc_report)
    summary["drc"] = {
        "exit_status": status,
        "violations": None if items is None else sum(items.values()),
        "by_rule": items,
        "deck_reports_success": "DRC RESULT: SUCCESS (0 violations)" in log,
        "errors_in_log": log.count("ERROR:"),
        "rules_executed": log.count("Executing rule"),
        "density_percent_reported_by_deck": {
            name.lower(): round(float(value), 3)
            for name, value in re.findall(
                r"(COMP|Poly2|Metal\d) ratio: ([0-9.]+) %", log
            )
        },
    }
    summary["drc"]["clean"] = (
        status == 0
        and items is not None
        and not items
        and summary["drc"]["deck_reports_success"]
        and not summary["drc"]["errors_in_log"]
    )
    if not summary["drc"]["clean"]:
        print(log[-3000:])
        drc_examples(drc_report)
        for name in os.environ.get("SLOT4_SHOW_FILES", "").split():
            print(f"=== {name}")
            for number, line in enumerate(
                (DRC_DIR / name).read_text(errors="replace").splitlines()[:420]
            ):
                print(f"{number + 1:4d} {line[:300]}")
        if os.environ.get("SLOT4_SHOW_RULES"):
            deck_text(os.environ["SLOT4_SHOW_RULES"])
            deck_text("|".join(re.escape(rule) for rule in items))

    antenna_deck = DRC_DIR / "gf180mcu_antenna.drc"
    antenna_in_main = sum("ANT." in line for line in log.splitlines())
    summary["antenna"] = {"rules_in_main_deck_log": antenna_in_main}
    if antenna_deck.exists():
        # The released core is checked alone as well, so that anything the
        # slot wiring adds can be told apart from what the core already had.
        results = {}
        for label, source in (("core", CORE_GDS), ("slot", gds)):
            report = OUT / f"{label}_antenna.lyrdb"
            status, alog = klayout(
                antenna_deck,
                {
                    "input": source,
                    "report": report,
                    "topcell": TOP if label == "slot" else "Bandgap_Core_Res",
                    "metal_top": "11K",
                    "metal_level": "5LM",
                    "mim_option": "B",
                    "run_mode": "flat",
                    "thr": 4,
                },
                f"{label}_antenna.log",
            )
            items = drc_items(report)
            results[label] = {
                "exit_status": status,
                "violations": None if items is None else sum(items.values()),
                "by_rule": items,
                "rules_executed": alog.count("Executing rule"),
            }
            if status != 0 or items is None:
                print(alog[-3000:])
        core_rules = results["core"]["by_rule"] or {}
        slot_rules = results["slot"]["by_rule"]
        added = (
            None
            if slot_rules is None
            else {
                rule: count - core_rules.get(rule, 0)
                for rule, count in slot_rules.items()
                if count > core_rules.get(rule, 0)
            }
        )
        summary["antenna"].update(
            {
                "separate_deck": antenna_deck.name,
                "core_alone": results["core"],
                "slot": results["slot"],
                "added_by_slot_wiring": added,
                "clean": results["slot"]["exit_status"] == 0 and added == {},
            }
        )
    else:
        summary["antenna"]["separate_deck"] = None

    lvs_report = OUT / "slot4_lvs.lvsdb"
    status, log = klayout(
        LVS_DECK,
        {
            "input": gds,
            "schematic": NETLIST,
            "report": lvs_report,
            "target_netlist": OUT / "slot4_lvs.cir",
            "topcell": TOP,
            "metal_top": "11K",
            "metal_level": "5LM",
            "mim_option": "B",
            "poly_res": "1k",
            "mim_cap": "2",
            "run_mode": "flat",
            "implicit_connect": "false",
            "top_lvl_pins": "true",
            "combine": "true",
        },
        "slot4_lvs.log",
    )
    summary["lvs"] = {"exit_status": status, **lvs_result(lvs_report)}
    if not summary["lvs"]["match"]:
        print(log[-3000:])

    summary["pass"] = bool(
        summary["rebuild_matches_committed_gds"]
        and tops == [TOP]
        and summary["drc"]["clean"]
        and summary["antenna"].get("clean", True)
        and summary["lvs"]["match"]
    )
    text = json.dumps(summary, indent=2)
    (OUT / "slot4_verification.json").write_text(text + "\n")
    print("SLOT4_VERIFICATION_BEGIN")
    print(text)
    print("SLOT4_VERIFICATION_END")
    return 0 if summary["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())

"""Cross-corner DC screen at fixed trim 72; not full analog PVT sign-off."""

from concurrent.futures import ThreadPoolExecutor
from itertools import product
from pathlib import Path
import json
import re
import subprocess
import screen_extracted as nominal

ROOT = nominal.ROOT
OUT = ROOT / "postlayout/pvt_dc"
OUT.mkdir(parents=True, exist_ok=True)


def run_case(case):
    mos, bjt, res, supply = case
    name = f"{mos}_{bjt}_{res}_{supply:g}"
    folder = OUT / name
    folder.mkdir(exist_ok=True)
    header = nominal.HEADER.replace("ngspice typical", f"ngspice {mos}")
    header = header.replace("bjt_typical", f"bjt_{bjt}").replace(
        "res_typical", f"res_{res}"
    )
    lines = [
        f"* Fixed-code DC screen {name}",
        header,
        ".options klu num_threads=1",
        f'.include {nominal.circuit_file("rc")}',
        "XDUT avdd 0 vref b0 b1 b2 b3 b4 b5 b6 0 dvdd Bandgap_Core_Res",
        f"VDD avdd 0 {supply}",
        "VDVDD dvdd 0 3.3",
    ]
    lines += [f"VB{i} b{i} 0 {3.3 if 72 & (1 << i) else 0}" for i in range(7)]
    lines += [
        ".control",
        "set noaskquit",
        "set num_threads=1",
        "set wr_singlescale",
        "set numdgt=15",
        "save v(vref) i(vdd)",
        "unset appendwrite",
    ]
    for temperature in [-40, 25, 110, 125]:
        lines += [
            f"set temp={temperature}",
            "op",
            f"let temperature={temperature}",
            "wrdata dc.dat temperature v(vref) i(vdd)",
            "set appendwrite",
        ]
    lines += ["quit", ".endc", ".end"]
    (folder / "test.spice").write_text("\n".join(lines) + "\n")
    with (folder / "ngspice.log").open("w") as log:
        result = subprocess.run(
            ["ngspice", "-b", "test.spice"],
            cwd=folder,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=120,
        )
    log = (folder / "ngspice.log").read_text()
    errors = [
        line
        for line in log.splitlines()
        if re.search(r"(?i)error|failed|singular matrix", line)
    ]
    values = []
    if (folder / "dc.dat").exists():
        for row in (folder / "dc.dat").read_text().splitlines():
            if not re.match(r"^\s*[-+\d.]", row):
                continue
            _, temperature, vref, current = map(float, row.split())
            values.append(
                {"temperature_C": temperature, "vref_V": vref, "idd_uA": -current * 1e6}
            )
    record = dict(
        mos=mos,
        bjt=bjt,
        resistor=res,
        supply_V=supply,
        trim_code=72,
        exit_code=result.returncode,
        errors=errors,
        points=values,
    )
    print(name, len(values), "points", len(errors), "diagnostics", flush=True)
    return record


with ThreadPoolExecutor(max_workers=4) as pool:
    records = list(
        pool.map(
            run_case,
            product(
                ["typical", "ff", "ss", "fs", "sf"],
                ["typical", "ff", "ss"],
                ["typical", "ff", "ss"],
                [3.3, 5.0],
            ),
        )
    )
(OUT / "summary.json").write_text(json.dumps(records, indent=2) + "\n")
assert all(
    r["exit_code"] == 0 and len(r["points"]) == 4 and not r["errors"] for r in records
)

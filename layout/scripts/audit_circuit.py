"""Read the generated Xschem subcircuit; never evaluate SPICE expressions."""

import json
import re
import sys
from collections import Counter
from pathlib import Path

from layout_paths import BUILD_DIR as ROOT

netlist = (
    Path(sys.argv[1])
    if len(sys.argv) > 1
    else ROOT / "netlists/TB10_RESISTOR_TRIM.spice"
)
text = netlist.read_text()
body = re.search(
    r"(?im)^\.subckt Bandgap_Core_Res (.*?)\n(.*?)^\.ends", text, re.S | re.M
).groups()
lines = re.sub(r"\n\+\s*", " ", body[1]).splitlines()
devices = []
probes = []
for line in lines:
    if not line or line.startswith("*"):
        continue
    words = line.split()
    if words[0].startswith("V"):
        assert words[-1] == "0", line
        probes.append(dict(name=words[0], nodes=words[1:3]))
        continue
    assert words[0].startswith("X"), line
    model_index = (
        next(i - 1 for i, w in enumerate(words) if "=" in w)
        if "=" in line
        else len(words) - 1
    )
    params = dict(re.findall(r"\b(\w+)\s*=\s*([\d.eE+-]+u?)\b", line))

    def um(key):
        value = params.get(key)
        return (
            None
            if value is None
            else float(value[:-1]) if value.endswith("u") else float(value) * 1e6
        )

    devices.append(
        dict(
            name=words[0][1:],
            model=words[model_index],
            nodes=words[1:model_index],
            w=um("W") or um("r_width") or um("c_width"),
            l=um("L") or um("r_length") or um("c_length"),
            m=int(params.get("m", "1")),
            source=line,
        )
    )
off_grid = []
for d in devices:
    for key in ("w", "l"):
        if d[key] is not None and abs(d[key] / 0.005 - round(d[key] / 0.005)) > 1e-6:
            off_grid.append(
                dict(
                    device=d["name"],
                    dimension=key,
                    um=d[key],
                    nearest_um=round(d[key] / 0.005) * 0.005,
                )
            )
inventory = dict(
    top="Bandgap_Core_Res",
    pins=body[0].split(),
    probes=probes,
    devices=devices,
    off_grid=off_grid,
    counts=dict(Counter(d["model"] for d in devices)),
)
(ROOT / "circuit.json").write_text(json.dumps(inventory, indent=2) + "\n")
print(json.dumps({k: v for k, v in inventory.items() if k != "devices"}, indent=2))
for d in devices:
    print(
        d["name"], d["model"], f"W={d['w']} L={d['l']} m={d['m']}", " ".join(d["nodes"])
    )
print("Total devices with multiplicity:", sum(d["m"] for d in devices))
print(
    "MOS channel area:",
    sum(d["w"] * d["l"] * d["m"] for d in devices if "fet_" in d["model"]),
)
print(
    "Cap plate area:",
    sum(d["w"] * d["l"] * d["m"] for d in devices if "cap_" in d["model"]),
)

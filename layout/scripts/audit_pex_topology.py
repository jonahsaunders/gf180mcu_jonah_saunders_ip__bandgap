"""Create independent LVS inputs for Magic extraction and collapsed RC topology."""

from pathlib import Path
import json
import re

from layout_paths import BUILD_DIR as root

manifest = json.loads((root / "primitive_manifest.json").read_text())
models = {d["model"] for d in manifest["devices"]}
cap_alias = "cap_mim_2f0_m4m5_noshield"
models.add(cap_alias)
ports = manifest["pins"]
extraction_ports = json.loads((root / "pex/port_manifest.json").read_text())[
    "extraction_ports"
]

for suffix in ("magic_lvs", "c", "rc"):
    text = (root / f"pex/Bandgap_Core_Res_{suffix}.spice").read_text()
    logical = re.sub(r"\n\+\s*", " ", text).splitlines()
    declaration = next(
        line.split()
        for line in logical
        if line.lower().startswith(".subckt bandgap_core_res ")
    )
    assert declaration[2:] == extraction_ports, (
        suffix,
        "Extracted port order differs from the schematic",
        declaration[2:],
        extraction_ports,
    )

for suffix in ("magic_lvs", "rc"):
    source = (root / f"pex/Bandgap_Core_Res_{suffix}.spice").read_text()
    lines = re.sub(r"\n\+\s*", " ", source).splitlines()
    parent = {}

    def find(name):
        while name in parent:
            name = parent[name]
        return name

    def rank(name):
        return (name not in ports, name.count("."), len(name), name)

    if suffix == "rc":
        for line in lines:
            if line.startswith("R"):
                _, a, b, value = line.split()
                float(value)
                a, b = find(a), find(b)
                if a != b:
                    keep, other = sorted([a, b], key=rank)
                    parent[other] = keep
    print("Port aliases", suffix, {p: find(p) for p in ports})
    assert len({find(p) for p in ports}) == len(ports), "PEX shorts external pins"
    output = [
        "* Independent topology audit of the extraction output.",
        ".subckt Bandgap_Core_Res " + " ".join(ports),
    ]
    count = 0
    for line in lines:
        if not line.startswith("X"):
            continue
        words = line.split()
        idx = next(i for i, w in enumerate(words) if w in models)
        model = words[idx]
        if model == cap_alias:
            model = "cap_mim_2f0fF"
        nodes = [find(n) for n in words[1:idx]]
        params = dict(re.findall(r"\b(\w+)=([^\s]+)", line))
        prefix = (
            "M"
            if "fet_" in model
            else (
                "Q"
                if model.startswith("pnp")
                else "C" if model.startswith("cap_") else "R"
            )
        )
        out = f"{prefix}{words[0]} " + " ".join(nodes) + " " + model
        if prefix != "Q":
            w = params.get("w", params.get("r_width", params.get("c_width")))
            l = params.get("l", params.get("r_length", params.get("c_length")))
            assert w and l, line
            out += f" W={w} L={l}"
        if "M" in params:
            out += f' m={params["M"]}'
        if "m" in params:
            out += f' m={params["m"]}'
        output.append(out)
        count += 1
    output.append(".ends Bandgap_Core_Res")
    (root / f"pex/audit_{suffix}.spice").write_text("\n".join(output) + "\n")
    print(suffix, "device records", count, "collapsed RC nodes", len(parent))

# Keep raw exports. Simulation copies hide extraction-only internal ports;
# only the subcircuit declaration changes, never nodes or circuit elements.
# The documented MIM model-name adapter leaves geometry parameters unchanged.
for suffix in ("magic_lvs", "c", "rc"):
    source = (root / f"pex/Bandgap_Core_Res_{suffix}.spice").read_text()
    source, replacements = re.subn(
        r"(?mi)^\.subckt Bandgap_Core_Res [^\n]*(?:\n\+[^\n]*)*",
        ".subckt Bandgap_Core_Res " + " ".join(ports),
        source,
    )
    assert replacements == 1
    source = source.replace(cap_alias, "cap_mim_2f0fF")
    (root / f"pex/Bandgap_Core_Res_{suffix}_simulation.spice").write_text(source)

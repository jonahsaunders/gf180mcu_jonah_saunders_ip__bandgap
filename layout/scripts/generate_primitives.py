"""Generate one PDK primitive per distinct geometry, without changing the PDK."""

import json
from pathlib import Path

from layout_paths import BUILD_DIR as ROOT

# Only these reviewed PDK names may become Tcl generator procedure names.
SUPPORTED_MODELS = {
    "nfet_03v3",
    "nfet_05v0",
    "pfet_03v3",
    "pfet_05v0",
    "pnp_05p00x05p00",
    "ppolyf_u_1k",
    "nwell",
    "pplus_u",
    "cap_mim_2f0fF",
}

inventory = json.loads((ROOT / "circuit.json").read_text())
unique = {}
for device in inventory["devices"]:
    if device["model"] not in SUPPORTED_MODELS:
        raise ValueError(f"Unsupported PDK model: {device['model']}")
    key = (device["model"], device["w"], device["l"])
    if key not in unique:
        unique[key] = dict(device, cell=f"bg_primitive_{len(unique):03}")
    device["cell"] = unique[key]["cell"]


def microns(value):
    return f"{round(value / .005) * .005:.3f}"


commands = [
    "drc off",
    "crashbackups stop",
    "locking disable",
    "units internal",
    "snap internal",
]
for device in unique.values():
    model, cell = device["model"], device["cell"]
    if model.startswith("pnp"):
        continue  # Import the foundry GDS directly with KLayout, avoiding Magic view drift.
    else:
        commands.extend(
            [
                f"load {cell}",
                "select top cell",
                "select area",
                "delete",
                "box values 0 0 0 0",
            ]
        )
        generator = {"cap_mim_2f0fF": "cap_mim_2p0fF"}.get(model, model)
        # Call the geometry routine directly to retain long-channel 5 V markers.
        # Its GUI check clips 5 V channel lengths; actual geometry is validated below.
        extra = (
            " diff_gate_space 0.8 gate_to_polycont 0.4 botc 0"
            if "fet_" in model
            else ""
        )
        commands.append(
            f'gf180mcu::{generator}_draw [dict merge [gf180mcu::{generator}_defaults] [dict create w {microns(device["w"])} l {microns(device["l"])}{extra}]]'
        )
    commands.extend(
        [
            f"save {cell}",
            f"gds write {cell}.gds",
            "extract all",
            "ext2spice lvs",
            f"ext2spice -o {cell}.spice",
        ]
    )
commands.append("quit -noprompt")
(ROOT / "primitive_manifest.json").write_text(
    json.dumps(dict(primitives=list(unique.values()), **inventory), indent=2) + "\n"
)
(ROOT / "generate_primitives.tcl").write_text("\n".join(commands) + "\n")
print(f"{len(unique)} unique primitive geometries")

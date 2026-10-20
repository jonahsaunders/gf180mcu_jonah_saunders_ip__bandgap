"""Build an explicitly approximate RCC model that conserves the extracted C matrix.

Magic extresist converts coupling capacitance into substrate capacitance in
its rnode records. Its RC SPICE export also retains coupling capacitors, so
collapsing its wire resistances does not recover the capacitance-only model.

This adapter keeps all raw RC devices and resistances. For each pair of
original nets it rescales the distributed parasitic capacitors to the total
from the capacitance-only extraction of the same GDS. Existing placement of
capacitance along a wire supplies relative weights, not independently verified
field-solver locations. This is a bounded engineering model, not signoff PEX.
The original raw exports are always retained, and no device model is changed.
"""

from collections import defaultdict
from pathlib import Path
import argparse
import hashlib
import json
import re


def read_records(path):
    text = re.sub(r"\n\+\s*", " ", path.read_text())
    return [line.split() for line in text.splitlines() if line.strip()]


def capacitance(value):
    factors = {"f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6}
    if value[-1] in factors:
        return float(value[:-1]) * factors[value[-1]]
    return float(value)


def original_nets(records, ports):
    parent = {}

    def find(name):
        while name in parent:
            name = parent[name]
        return name

    def rank(name):
        return name not in ports, name.count("."), len(name), name

    for row in records:
        if row[0].startswith("R"):
            first, second = find(row[1]), find(row[2])
            if first != second:
                keep, other = sorted([first, second], key=rank)
                parent[other] = keep
    assert len({find(p) for p in ports}) == len(ports), "RC shorts external ports"
    return {name: find(name) for name in parent}


def build_model(build):
    directory = build / "pex"
    cc_path = directory / "Bandgap_Core_Res_c_simulation.spice"
    rc_path = directory / "Bandgap_Core_Res_rc_simulation.spice"
    cc, rc = read_records(cc_path), read_records(rc_path)
    ports = next(row[2:] for row in rc if row[0].lower() == ".subckt")
    aliases = original_nets(rc, ports)

    def pair(row):
        return tuple(sorted(aliases.get(n, n) for n in row[1:3]))

    target = defaultdict(float)
    distributed = defaultdict(list)
    for row in cc:
        if row[0].startswith("C"):
            target[pair(row)] += capacitance(row[3])
    for row in rc:
        if row[0].startswith("C"):
            distributed[pair(row)].append(row)

    result = [
        "* Capacitance-conserving approximation; see conserve_pex_capacitance.py.",
        "* Raw Magic exports remain available for independent review.",
    ]
    result += [
        " ".join(row)
        for row in rc
        if not row[0].startswith("C") and row[0].lower() != ".ends"
    ]
    count = 0
    evidence = []
    for nets in sorted(set(target) | set(distributed)):
        wanted = target.get(nets, 0.0)
        existing = distributed.get(nets, [])
        raw_total = sum(capacitance(row[3]) for row in existing)
        assert wanted >= 0 and raw_total >= 0
        evidence.append(
            dict(nets=nets, c_only_fF=wanted * 1e15, raw_rc_fF=raw_total * 1e15)
        )
        if not wanted or nets[0] == nets[1]:
            continue
        # An absent distributed pair uses its original named nodes. Those nodes
        # are kept by the RC export for lumped coupling capacitors.
        if not raw_total:
            existing = [["C", *nets, "1"]]
            raw_total = 1.0
        total = 0.0
        for row in existing:
            value = wanted * capacitance(row[3]) / raw_total
            result.append(f"Cconserve{count} {row[1]} {row[2]} {value:.14g}")
            count += 1
            total += value
        assert abs(total - wanted) <= max(1e-24, wanted * 1e-10)
    result.append(".ends Bandgap_Core_Res")
    output = directory / "Bandgap_Core_Res_rcc_simulation.spice"
    output.write_text("\n".join(result) + "\n")
    # Check the serialized values, not only the in-memory calculation.
    actual = defaultdict(float)
    for row in read_records(output):
        if row[0].startswith("C"):
            actual[pair(row)] += capacitance(row[3])
    for nets in set(target) | set(actual):
        assert abs(actual[nets] - target[nets]) <= max(
            1e-24, target[nets] * 1e-10
        ), nets
    report = dict(
        method="Conserve the C-only net-pair matrix; retain raw RC devices and resistors",
        limitation="Relative distribution is approximate; ground resistance is omitted",
        capacitance_matrix_conserved=True,
        input_sha256={
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [cc_path, rc_path]
        },
        output_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
        pairs=evidence,
    )
    (directory / "capacitance_conservation.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    print(f"Conserved {len(target)} capacitance pairs in {output.name}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path)
    args = parser.parse_args()
    if args.build is None:
        from layout_paths import BUILD_DIR

        args.build = BUILD_DIR
    build_model(args.build)

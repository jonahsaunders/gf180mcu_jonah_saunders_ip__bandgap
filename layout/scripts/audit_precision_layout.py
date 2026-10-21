"""Audit PNP/resistor surroundings and source-path resistance from actual evidence.

This is a geometry and nominal interconnect audit, not a mismatch-yield model.
The optional resistance check uses the extracted resistor network without
inventing sheet resistance values or interpreting nominal ratios as statistics.
"""

from collections import defaultdict
from pathlib import Path
import argparse
import hashlib
import json
import math
import re

import klayout.db as k
import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import splu


def read(path):
    return json.loads(path.read_text())


def audit_geometry(root):
    gds = root / "filled_candidate.gds"
    if not gds.exists():
        gds = root / "Bandgap_Core_Res.gds"
    placement = read(root / "placement.json")
    routing = read(root / "routing_status.json")
    layout = k.Layout()
    layout.read(str(gds))
    top, dbu = layout.top_cell(), layout.dbu

    def region(number, datatype=0):
        return k.Region(top.begin_shapes_rec(layout.layer(number, datatype))).merged()

    def box(bounds):
        return k.Region(k.DBox(*bounds).to_itype(dbu))

    def canonical(name):
        while name in routing["aliases"]:
            name = routing["aliases"][name]
        return name

    ex = k.LayoutToNetlist(k.RecursiveShapeIterator(layout, top, []))
    chain = [34, 35, 36, 38, 42, 40, 46, 41, 81]
    layers = {n: ex.make_layer(layout.layer(n, 0), f"L{n}") for n in chain}
    layers[41] -= ex.make_layer(layout.layer(75, 0), "FuseTop")
    ex.register(layers[41], "Via4OutsideCapacitor")
    for layer in layers.values():
        ex.connect(layer)
    for first, second in zip(chain, chain[1:]):
        ex.connect(layers[first], layers[second])
    ex.extract_netlist()
    nets = {}
    for device in placement["placed"]:
        for node, point in zip(device["nodes"], device["pins"]):
            name = canonical(node)
            net = ex.probe_net(layers[36], k.DPoint(*point))
            assert net is not None
            assert name not in nets or nets[name].cluster_id == net.cluster_id
            nets[name] = net
    shapes = {
        name: {
            n: ex.shapes_of_net(net, layers[n], True).merged()
            for n in [34, 36, 42, 46, 81]
        }
        for name, net in nets.items()
    }
    poly, contacts = region(30), region(33)
    emitters = region(22) & region(31) & region(21)
    records = []
    for device in placement["placed"]:
        if device["model"] not in ("pnp_05p00x05p00", "ppolyf_u_1k"):
            continue
        window = box(device["bounds"])
        related = {canonical(n) for n in device["nodes"]}
        record = {
            "instance": device["name"],
            "source": device["source"],
            "model": device["model"],
            "rotation": device["rotation"],
        }
        if device["model"].startswith("pnp"):
            origin = k.DPoint(device["x"], device["y"]).to_itype(dbu)
            candidates = [
                p for p in (emitters & window).merged().each() if p.inside(origin)
            ]
            assert len(candidates) == 1, device["name"]
            sensitive = k.Region(candidates[0])
            b = sensitive.bbox().to_dtype(dbu)
            record.update(
                emitter_center_um=[b.center().x, b.center().y],
                emitter_area_um2=sensitive.area() * dbu**2,
            )
        else:
            sensitive = poly & window
            local_contacts = (contacts & sensitive).transformed(
                k.ICplxTrans(
                    1,
                    device["rotation"],
                    False,
                    round(device["x"] / dbu),
                    round(device["y"] / dbu),
                ).inverted()
            )
            halves = [[], []]
            for cut in local_contacts.each():
                halves[cut.bbox().center().y > 0].append(cut.area() * dbu**2)
            record["end_contact_counts"] = [len(cuts) for cuts in halves]
            record["end_contact_area_um2"] = [sum(cuts) for cuts in halves]
            record["equal_end_contacts"] = bool(halves[0]) and np.allclose(
                record["end_contact_area_um2"],
                record["end_contact_area_um2"][::-1],
                atol=1e-9,
            )
            assert record["equal_end_contacts"], device["name"]
        foreign = []
        for name, metal in shapes.items():
            if name in related:
                continue
            overlap = {
                f"M{i+1}": (sensitive & metal[n]).area() * dbu**2
                for i, n in enumerate([34, 36, 42, 46, 81])
            }
            overlap = {key: value for key, value in overlap.items() if value > 1e-6}
            if overlap:
                foreign.append({"net": name, "overlap_um2": overlap})
        record["unrelated_routes_over_device"] = foreign
        if device["model"].startswith("pnp"):
            array_nets = {
                canonical(node)
                for unit in placement["placed"]
                if unit["model"].startswith("pnp")
                for node in unit["nodes"]
            }
            record["routes_external_to_PNP_array"] = [
                row for row in foreign if row["net"] not in array_nets
            ]
        record["metal_fill_overlap_um2"] = {
            f"M{i+1}": (sensitive & region(n, 4)).area() * dbu**2
            for i, n in enumerate([34, 36, 42, 46, 81])
        }
        records.append(record)
    pnp = defaultdict(list)
    for row in records:
        if "emitter_center_um" in row:
            pnp[row["source"]].append(row)
    centroid = {
        name: np.mean([r["emitter_center_um"] for r in units], axis=0).tolist()
        for name, units in pnp.items()
    }
    assert len(pnp["Q1"]) == 1 and len(pnp["Q2"]) == 8
    assert math.dist(centroid["Q1"], centroid["Q2"]) < 1e-6
    assert (
        len({round(r["emitter_area_um2"], 6) for units in pnp.values() for r in units})
        == 1
    )
    return {
        "gds_sha256": hashlib.sha256(gds.read_bytes()).hexdigest(),
        "PNP_centroids_um": centroid,
        "Q1_Q2_centroid_separation_um": 0.0,
        "devices": records,
        "limitations": "Centroids, contact geometry and conductor overlap do not establish thermal/stress matching or statistical yield.",
    }


def audit_source_resistance(root):
    path = root / "pex/Bandgap_Core_Res_rcc_simulation.spice"
    text = re.sub(r"\n\+\s*", " ", path.read_text())
    graph = defaultdict(list)
    resistors = []
    for line in text.splitlines():
        words = line.split()
        if not words or not words[0].startswith("R"):
            continue
        a, b, resistance = words[1], words[2], float(words[3])
        assert resistance > 0
        graph[a].append(b)
        graph[b].append(a)
        resistors.append((a, b, resistance))
    mirror = next(
        r
        for r in read(root / "mirror_bias/summary.json")
        if r["mode"] == "rcc" and r["AVDD_V"] == 3.3
    )
    output = {}
    for rail in ["avdd", "avss"]:
        component, pending = {rail}, [rail]
        while pending:
            for node in graph[pending.pop()]:
                if node not in component:
                    component.add(node)
                    pending.append(node)
        assert not ({"avdd", "avss", "dvdd", "dvss"} - {rail}) & component
        selected = [
            p
            for p in mirror["unit_records"]
            if p["nodes"][2] in component and not p["source"].startswith("DUM_")
        ]
        nodes = {n: i for i, n in enumerate(sorted(component - {rail}))}
        row, col, values = [], [], []
        for a, b, resistance in resistors:
            if a not in component:
                continue
            conductance = 1 / resistance
            for n in [a, b]:
                if n != rail:
                    row.append(nodes[n])
                    col.append(nodes[n])
                    values.append(conductance)
            if a != rail and b != rail:
                row.extend([nodes[a], nodes[b]])
                col.extend([nodes[b], nodes[a]])
                values.extend([-conductance, -conductance])
        matrix = coo_matrix(
            (values, (row, col)), shape=(len(nodes), len(nodes))
        ).tocsc()
        factor = splu(matrix)
        effective = {rail: 0.0}
        for node in sorted({p["nodes"][2] for p in selected} - {rail}):
            excitation = np.zeros(len(nodes))
            excitation[nodes[node]] = 1
            voltage = factor.solve(excitation)
            assert np.max(np.abs(matrix @ voltage - excitation)) < 1e-7
            effective[node] = float(voltage[nodes[node]])
        groups = defaultdict(list)
        for probe in selected:
            groups[probe["source"]].append(effective[probe["nodes"][2]])
        output[rail] = {
            name: {
                "source_units": len(values),
                "mean_individual_source_to_port_ohm": float(np.mean(values)),
                "range_ohm": [min(values), max(values)],
            }
            for name, values in sorted(groups.items())
        }
    return {
        "rcc_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "rails": output,
        "method": "Driving-point R from each source terminal to its supply port; all other interconnect terminals open. Includes shared paths; not a summed operating voltage drop.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--geometry-only", action="store_true")
    args = parser.parse_args()
    result = audit_geometry(args.build)
    if not args.geometry_only:
        result["source_resistance"] = audit_source_resistance(args.build)
    target = args.out or args.build / "precision_layout_audit.json"
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            {
                "Q1_Q2_centroid_separation_um": result["Q1_Q2_centroid_separation_um"],
                "devices": [
                    {
                        "name": r["instance"],
                        "foreign_routes": len(r["unrelated_routes_over_device"]),
                        "contacts": r.get("end_contact_counts"),
                    }
                    for r in result["devices"]
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

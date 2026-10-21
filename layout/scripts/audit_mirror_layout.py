"""Read-only matching review of released GDS, placement, and circuit inventory.

This measures geometry and connectivity. It does not predict mismatch yield,
thermal gradients, well-proximity shifts, or distributed interconnect errors.
Run with KLayout's Python module; no PDK or release file is modified.
"""

from collections import defaultdict
from itertools import combinations
from pathlib import Path
import argparse
import hashlib
import json
import math

import klayout.db as k
import numpy as np
from matched_routes import core_drain_routes, core_supply_routes


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def geometric_metrics(units):
    points = np.array([unit["channel_center_um"] for unit in units])
    center = points.mean(axis=0)
    delta = points - center
    return {
        "units": len(units),
        "centroid_um": center.tolist(),
        "second_moment_xx_xy_yy_um2": [
            float(np.mean(delta[:, 0] ** 2)),
            float(np.mean(delta[:, 0] * delta[:, 1])),
            float(np.mean(delta[:, 1] ** 2)),
        ],
        "rotation_degrees": sorted({unit["rotation"] for unit in units}),
    }


def analyze(release, output):
    manifest = json.loads((release / "verification.json").read_text())
    gds_name = (
        "Bandgap_Core_Res.gds"
        if (release / "Bandgap_Core_Res.gds").exists()
        else "filled_candidate.gds"
    )
    inputs = [
        gds_name,
        "placement.json",
        "primitive_manifest.json",
        "routing_status.json",
    ]
    hashes = {name: digest(release / name) for name in inputs}
    for name, value in hashes.items():
        assert manifest["sha256"][name] == value, f"Release hash changed: {name}"
    placement = json.loads((release / "placement.json").read_text())
    inventory = json.loads((release / "primitive_manifest.json").read_text())
    routing = json.loads((release / "routing_status.json").read_text())
    devices = {device["name"]: device for device in inventory["devices"]}

    def canonical(name):
        while name in routing["aliases"]:
            name = routing["aliases"][name]
        return name

    layout = k.Layout()
    layout.read(str(release / gds_name))
    top = layout.top_cell()
    dbu = layout.dbu

    def region(number, purpose=0):
        return k.Region(top.begin_shapes_rec(layout.layer(number, purpose))).merged()

    def rectangle(bounds):
        return k.Region(k.DBox(*bounds).to_itype(dbu))

    # Recover actual gate channels independently of the saved group-centroid file.
    channels = region(22) & region(30)
    by_source = defaultdict(list)
    all_mos = []
    for placed in placement["placed"]:
        if "fet_" not in placed["model"]:
            continue
        unit = dict(placed)
        original = devices[unit["source"]]
        channel = (channels & rectangle(unit["bounds"])).merged()
        assert channel.count() == 1, (unit["name"], channel.count())
        box = channel.bbox().to_dtype(dbu)
        assert abs(box.width() - original["l"]) < 0.0051, unit["name"]
        assert abs(box.height() - original["w"]) < 0.0051, unit["name"]
        unit["channel_center_um"] = [box.center().x, box.center().y]
        unit["channel_bounds_um"] = [box.left, box.bottom, box.right, box.top]
        unit["channel_area_um2"] = channel.area() * dbu**2
        by_source[unit["source"]].append(unit)
        all_mos.append(unit)
    assert len(all_mos) == sum(d["m"] for d in devices.values() if "fet_" in d["model"])
    for name, units in by_source.items():
        assert len(units) == devices[name]["m"], name

    # Extract conducting metal components. FuseTop via4 is a capacitor marker,
    # not a galvanic connection between its plates.
    ex = k.LayoutToNetlist(k.RecursiveShapeIterator(layout, top, []))
    metals = [34, 36, 42, 46, 81]
    chain = [34, 35, 36, 38, 42, 40, 46, 41, 81]
    layers = {
        number: ex.make_layer(layout.layer(number, 0), f"L{number}") for number in chain
    }
    fuse = ex.make_layer(layout.layer(75, 0), "FuseTop")
    layers[41] = layers[41] - fuse
    ex.register(layers[41], "Via4OutsideCapacitor")
    for layer in layers.values():
        ex.connect(layer)
    for first, second in zip(chain, chain[1:]):
        ex.connect(layers[first], layers[second])
    ex.extract_netlist()
    nets = {}
    cluster_names = {}
    for placed in placement["placed"]:
        for name, point in zip(placed["nodes"], placed["pins"]):
            name = canonical(name)
            net = ex.probe_net(layers[36], k.DPoint(*point))
            assert net is not None, (placed["name"], name)
            cluster = net.cluster_id
            assert cluster not in cluster_names or cluster_names[cluster] == name, (
                name,
                cluster_names.get(cluster),
            )
            assert name not in nets or nets[name].cluster_id == cluster, (
                placed["name"],
                name,
            )
            cluster_names[cluster] = name
            nets[name] = net
    net_shapes = {
        name: {
            number: ex.shapes_of_net(net, layers[number], True).merged()
            for number in chain
        }
        for name, net in nets.items()
    }
    fill = {number: region(number, 4) for number in metals}
    well = region(21)
    capacitor_area = region(75)
    aggregate = {name: geometric_metrics(units) for name, units in by_source.items()}

    group_records = []
    for group in placement["groups"]:
        names = group["names"]
        if (len(names) < 2 and not group.get("matching")) or not all(
            name in by_source for name in names
        ):
            continue
        x, y, width, height = [group[key] for key in ["x", "y", "width", "height"]]
        bounds = rectangle([x, y, x + width, y + height])
        units = [unit for name in names for unit in by_source[name]]
        positions = np.array([unit["channel_center_um"] for unit in units])
        lo, hi = positions.min(axis=0), positions.max(axis=0)
        related = {canonical(node) for name in names for node in devices[name]["nodes"]}
        channel_region = k.Region()
        for unit in units:
            channel_region += rectangle(unit["channel_bounds_um"])
        foreign = []
        for net_name, shapes in net_shapes.items():
            if net_name in related:
                continue
            areas = {
                f"M{index+1}": (channel_region & shapes[number]).area() * dbu**2
                for index, number in enumerate(metals)
            }
            areas = {key: value for key, value in areas.items() if value > 1e-6}
            if areas:
                foreign.append({"net": net_name, "channel_overlap_um2": areas})
        unit_metrics = {}
        for name in names:
            record = dict(aggregate[name])
            record["unit_model_W_L"] = [
                devices[name][key] for key in ["model", "w", "l"]
            ]
            record["edge_columns_count"] = sum(
                math.isclose(unit["channel_center_um"][0], lo[0], abs_tol=1e-6)
                or math.isclose(unit["channel_center_um"][0], hi[0], abs_tol=1e-6)
                for unit in by_source[name]
            )
            # Local well area is a limited geometry comparison; it does not
            # establish equal well-edge distances, tap resistance, or stress.
            signatures = []
            for unit in by_source[name]:
                local = well & rectangle(unit["bounds"])
                signatures.append(round(local.area() * dbu**2, 6))
            record["local_nwell_area_um2"] = sorted(set(signatures))
            unit_metrics[name] = record
        centers = [record["centroid_um"] for record in unit_metrics.values()]
        dummy_units = by_source.get(group.get("dummy_source"), [])
        dummy_record = None
        if dummy_units:
            dummy_device = devices[group["dummy_source"]]
            assert len({canonical(n) for n in dummy_device["nodes"]}) == 1
            assert canonical(dummy_device["nodes"][0]) == (
                "avdd" if dummy_device["model"].startswith("pfet") else "avss"
            )
            sites = {
                tuple(round(v, 6) for v in u["channel_center_um"]): u
                for u in units + dummy_units
            }
            xs = sorted({p[0] for p in sites})
            ys = sorted({p[1] for p in sites})
            perimeter = {
                (x, y)
                for x in xs
                for y in ys
                if x in (xs[0], xs[-1]) or y in (ys[0], ys[-1])
            }
            assert len(sites) == len(xs) * len(ys)
            assert all(sites[p]["source"] == group["dummy_source"] for p in perimeter)
            dummy_record = {
                "count": len(dummy_units),
                "rail": canonical(dummy_device["nodes"][0]),
                "complete_perimeter": True,
                "perimeter_units": len(perimeter),
                "internal_units": len(dummy_units) - len(perimeter),
            }
        group_records.append(
            {
                "names": names,
                "bounds_um": [x, y, x + width, y + height],
                "max_centroid_separation_um": (
                    max(math.dist(a, b) for a, b in combinations(centers, 2))
                    if len(centers) > 1
                    else 0.0
                ),
                "devices": unit_metrics,
                "dummy_verification": dummy_record,
                "foreign_routes_over_channels": foreign,
                "metal_fill_overlap_group_um2": {
                    f"M{i+1}": (bounds & fill[n]).area() * dbu**2
                    for i, n in enumerate(metals)
                },
                "mim_overlap_channels_um2": (channel_region & capacitor_area).area()
                * dbu**2,
            }
        )

    # Enumerate gate/source/bulk families from electrical connectivity, rather
    # than assuming the hand-selected matching groups cover every current mirror.
    families = defaultdict(list)
    for name, device in devices.items():
        if name not in by_source or name.startswith("DUM_"):
            continue
        if name.startswith("M") and name[1:].isdigit() and int(name[1:]) >= 24:
            continue  # Trim transmission gates and inverters.
        drain, gate, source, bulk = map(canonical, device["nodes"])
        families[(device["model"], gate, source, bulk)].append(name)
    family_records = []
    for (model, gate, source, bulk), names in families.items():
        if len(names) < 2:
            continue
        family_records.append(
            {
                "model": model,
                "gate": gate,
                "source": source,
                "bulk": bulk,
                "members": {
                    name: {
                        **aggregate[name],
                        "w_um": devices[name]["w"],
                        "l_um": devices[name]["l"],
                    }
                    for name in names
                },
                "diode_connected_members": [
                    name
                    for name in names
                    if canonical(devices[name]["nodes"][0]) == gate
                ],
                "max_centroid_separation_um": max(
                    math.dist(aggregate[a]["centroid_um"], aggregate[b]["centroid_um"])
                    for a, b in combinations(names, 2)
                ),
            }
        )
    named_pairs = [
        ("M16", "M18"),
        ("MFOLD1", "MFOLD2"),
        ("M3", "M4"),
        ("VBNSINK", "MSINK1"),
        ("VBNSINK", "MSINK2"),
        ("PREF", "MPSINK"),
        ("PREF", "MPFOLD"),
        ("N1", "NTAIL"),
        ("N1", "N3"),
    ]
    pairs = [
        {
            "names": [a, b],
            "centroid_separation_um": math.dist(
                aggregate[a]["centroid_um"], aggregate[b]["centroid_um"]
            ),
        }
        for a, b in named_pairs
    ]
    net_metrics = {
        name: {
            "metal_area_um2": {
                f"M{i+1}": shapes[n].area() * dbu**2 for i, n in enumerate(metals)
            },
            "via_counts": {
                f"V{i+1}": shapes[n].count() for i, n in enumerate([35, 38, 40, 41])
            },
            "router_tree_length_um": routing["route_lengths_um"].get(name),
        }
        for name, shapes in net_shapes.items()
    }
    local_validation = []
    local_path = release / "matched_local_routes.json"
    if local_path.exists() and all(
        "reserved_bounds_um" in r for r in json.loads(local_path.read_text())
    ):
        for plan in core_drain_routes(placement, dbu, 0.8):
            name = canonical(plan["net"])
            window = rectangle(plan["reserved_bounds"])
            areas, counts = [], []
            for number, expected in zip([36, 42, 46], plan["metal"]):
                actual = net_shapes[name][number] & window
                assert (actual ^ (expected & window)).is_empty(), (
                    name,
                    number,
                    "local route modified",
                )
                areas.append(actual.area() * dbu**2)
            for number, expected in zip([38, 40], plan["vias"]):
                actual = net_shapes[name][number] & window
                assert (actual ^ (expected & window)).is_empty(), (
                    name,
                    number,
                    "local vias modified",
                )
                counts.append(actual.count())
            local_validation.append(
                {
                    "source": plan["source"],
                    "net": name,
                    "actual_local_metal_area_um2": areas,
                    "actual_local_via_counts": counts,
                    "geometry_xor_um2": 0.0,
                }
            )
        for record in local_validation[1:]:
            assert (
                record["actual_local_metal_area_um2"]
                == local_validation[0]["actual_local_metal_area_um2"]
            )
            assert (
                record["actual_local_via_counts"]
                == local_validation[0]["actual_local_via_counts"]
            )
    source_plan = core_supply_routes(placement, dbu, 0.8)
    for number, expected in zip([36, 42, 46], source_plan["metal"]):
        assert (expected - net_shapes["avdd"][number]).is_empty(), (
            number,
            "source bus missing",
        )
    for number, expected in zip([38, 40], source_plan["vias"]):
        assert (expected - net_shapes["avdd"][number]).is_empty(), (
            number,
            "source bus via missing",
        )
    additional_buses = []
    bus_path = release / "matched_source_buses.json"
    if bus_path.exists():
        for bus in json.loads(bus_path.read_text()):
            plan = core_supply_routes(placement, dbu, 0.8, bus["sources"])
            rail = canonical(plan["net"])
            for number, expected in zip(
                [36, 42, 46, 38, 40], plan["metal"] + plan["vias"]
            ):
                assert (expected - net_shapes[rail][number]).is_empty(), (
                    bus["sources"],
                    number,
                    "matched source bus missing from GDS",
                )
            additional_buses.append({**bus, "verified_in_gds": True})
    record = {
        "scope": "Read-only geometry/connectivity audit; not mismatch or electrical qualification",
        "release_sha256": hashes,
        "mos_units_verified_against_gds": len(all_mos),
        "metal_nets_verified_against_placement": len(nets),
        "matched_groups": group_records,
        "gate_source_bulk_families": family_records,
        "additional_pair_distances": pairs,
        "net_geometry": net_metrics,
        "balanced_local_routes_verified_in_gds": local_validation,
        "matched_source_buses": additional_buses,
        "main_mirror_source_bus": {
            "verified_in_gds": True,
            "source_terminals": len(source_plan["unit_sources"]),
            "M2_escape_length_um": 2.4,
            "M3_row_bus_width_um": 2.0,
            "M4_common_trunk_width_um": 2.0,
            "global_branch_RC_equality_claimed": False,
        },
        "units": all_mos,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "mirror_geometry_audit.json").write_text(
        json.dumps(record, indent=2) + "\n"
    )
    print(
        json.dumps(
            {
                "MOS_units": len(all_mos),
                "nets": len(nets),
                "matched_groups": len(group_records),
                "pairs": pairs,
            },
            indent=2,
        )
    )
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    layout_dir = Path(__file__).resolve().parent.parent
    parser.add_argument("--release", type=Path, default=layout_dir / "release")
    parser.add_argument("--out", type=Path, default=layout_dir / "build/mirror_review")
    args = parser.parse_args()
    analyze(args.release, args.out)

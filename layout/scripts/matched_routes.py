"""Construct equal local drain access for the cyclic M1/M2/M5 unit array.

Each branch gets the same M2 escape length, M3 bus length, M4 trunk length,
wire width, and via count. The global router connects one access per branch;
it must not add alternative paths between the already connected unit drains.
"""

import klayout.db as k


def core_drain_routes(placement, dbu, pitch):
    sources = ["M1", "M2", "M5"]
    units = [d for d in placement["placed"] if d["source"] in sources]
    assert len(units) == 18
    rows = sorted({round(d["pins"][0][1], 6) for d in units})
    assert len(rows) == 3
    group = next(g for g in placement["groups"] if set(g["names"]) == set(sources))
    bounds = [
        group["x"],
        group["y"],
        group["x"] + group["width"],
        group["y"] + group["height"],
    ]
    middle_x = sum(d["pins"][0][0] for d in units) / len(units)
    middle_x = round(middle_x / pitch) * pitch
    bottom = round((group["y"] - 3.2) / pitch) * pitch
    upper = rows[-1] + 4.0

    def pad(point, radius=0.2):
        x, y = point
        return k.Region(
            k.DBox(x - radius, y - radius, x + radius, y + radius).to_itype(dbu)
        )

    def wire(a, b):
        return k.Region(
            k.DPath([k.DPoint(*a), k.DPoint(*b)], 0.4, 0.2, 0.2).to_itype(dbu).polygon()
        )

    result = []
    for index, source in enumerate(sources):
        own = [d for d in units if d["source"] == source]
        net = own[0]["nodes"][0]
        assert all(d["nodes"][0] == net for d in own)
        trunk_x = middle_x + (index - 1) * pitch
        anchor = (trunk_x, bottom)
        metal = [pad(anchor), pad(anchor), wire(anchor, (trunk_x, upper))]
        vias = [pad(anchor, 0.13), pad(anchor, 0.13)]
        lengths = [0.0, 0.0, upper - bottom]
        for row_index, y in enumerate(rows):
            drains = sorted(
                tuple(d["pins"][0]) for d in own if abs(d["pins"][0][1] - y) < 1e-5
            )
            assert len(drains) == 2
            bus_y = y + pitch * (1 + (index + row_index) % 3)
            assert drains[0][0] < trunk_x < drains[1][0]
            metal[1] += wire((drains[0][0], bus_y), (drains[1][0], bus_y))
            lengths[1] += drains[1][0] - drains[0][0]
            for point in drains:
                landing = (point[0], bus_y)
                metal[0] += wire(point, landing)
                vias[0] += pad(landing, 0.13)
                lengths[0] += bus_y - y
            vias[1] += pad((trunk_x, bus_y), 0.13)
        result.append(
            {
                "source": source,
                "net": net,
                "anchor": anchor,
                "reserved_bounds": bounds,
                "unit_drains": [tuple(d["pins"][0]) for d in own],
                "metal": [r.merged() for r in metal],
                "vias": [r.merged() for r in vias],
                "lengths_um": lengths,
            }
        )
    for plan in result[1:]:
        assert all(
            abs(a - b) < 1e-6
            for a, b in zip(result[0]["lengths_um"], plan["lengths_um"])
        )
        assert [r.area() for r in plan["metal"]] == [
            r.area() for r in result[0]["metal"]
        ]
        assert [r.count() for r in plan["vias"]] == [7, 4]
    return result


def core_supply_routes(placement, dbu, pitch, sources=("M1", "M2", "M5")):
    """Low-resistance row buses for all 18 main-mirror source terminals.

    Every source has the same short M2 escape. Wide M3 row buses feed a
    common M4 trunk at the left dummy column. Each branch contributes two
    units to each row, reducing differential source-voltage drop.
    """
    units = [d for d in placement["placed"] if d["source"] in sources]
    group = next(g for g in placement["groups"] if set(g["names"]) == set(sources))
    rails = {d["nodes"][2] for d in units}
    assert len(rails) == 1, (sources, "Source buses require a shared electrical net")
    rows = sorted({round(d["pins"][2][1], 6) for d in units})
    x = round((group["x"] + 2.4) / pitch) * pitch
    bottom = round((group["y"] - 3.2) / pitch) * pitch

    def line(a, b, width):
        return k.Region(
            k.DPath([k.DPoint(*a), k.DPoint(*b)], width, width / 2, width / 2)
            .to_itype(dbu)
            .polygon()
        )

    def pad(a, radius):
        return k.Region(
            k.DBox(a[0] - radius, a[1] - radius, a[0] + radius, a[1] + radius).to_itype(
                dbu
            )
        )

    anchor = (x, bottom)
    metal = [pad(anchor, 0.2), pad(anchor, 0.6), line(anchor, (x, rows[-1] - 2.4), 2)]
    vias = [pad(anchor, 0.13), pad(anchor, 0.13)]
    for y in rows:
        sources = [
            tuple(d["pins"][2]) for d in units if abs(d["pins"][2][1] - y) < 1e-5
        ]
        bus_y = y - 2.4
        metal[1] += line((x, bus_y), (max(p[0] for p in sources), bus_y), 2)
        for source in sources:
            landing = (source[0], bus_y)
            metal[0] += line(source, landing, 0.4)
            vias[0] += pad(landing, 0.13)
        for dx in [-0.26, 0.26]:
            for dy in [-0.26, 0.26]:
                vias[1] += pad((x + dx, bus_y + dy), 0.13)
    return {
        "sources": list(sources),
        "net": rails.pop(),
        "anchor": anchor,
        "metal": [r.merged() for r in metal],
        "vias": [r.merged() for r in vias],
        "unit_sources": [tuple(d["pins"][2]) for d in units],
    }

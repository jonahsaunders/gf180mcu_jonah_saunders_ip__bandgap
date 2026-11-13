"""Explicit supply floorplan for the trim-inclusive core.

Each entry is one straight rectangle with a stated purpose. Coordinates are
micrometres in the placement frame. Rails over matched arrays are derived
from the placed dummy units, so they always sit on the tied-off perimeter
sites and never on a functional channel. Channel rails use fixed coordinates;
strengthen_power.py checks every rectangle against the actual routed metal
and stops the build when a clearance is not met.

Principles
----------
* M5 carries the supplies as wide, straight rails; no maze-routed supply wire.
* AVDD and AVSS never cross on M5. Where a rail has to pass a signal corridor
  or the other supply it drops to a short, wide M4 bridge.
* AVDD keeps the existing clearance from the sensitive amplifier nets.
* The corridor between the MPSINK and MPFOLD arrays stays open on M5 for the
  reference strap; the two AVDD rings join below it on seven M4 bridges.
"""

TRUNK_WIDTH = 10.0
INSET = 0.5


def _group(placement, first):
    return next(g for g in placement["groups"] if g["names"][0] == first)


def ring(placement, first, inset=INSET):
    """Strips covering the outer dummy rows and columns of a matched array."""
    group = _group(placement, first)
    dummies = [d for d in placement["placed"] if d["source"] == group["dummy_source"]]
    xs = sorted({round(d["bounds"][0], 3) for d in dummies})
    ys = sorted({round(d["bounds"][1], 3) for d in dummies})
    width = dummies[0]["bounds"][2] - dummies[0]["bounds"][0]
    height = dummies[0]["bounds"][3] - dummies[0]["bounds"][1]
    left, right = xs[0] + inset, xs[-1] + width - inset
    bottom, upper = ys[0] + inset, ys[-1] + height - inset
    sides = {
        "W": (left, bottom, xs[0] + width - inset, upper),
        "E": (xs[-1] + inset, bottom, right, upper),
        "S": (left, bottom, right, ys[0] + height - inset),
        "N": (left, ys[-1] + inset, right, upper),
    }
    return {side: tuple(round(v, 3) for v in box) for side, box in sides.items()}


def column_origins(placement, first):
    group = _group(placement, first)
    return sorted(
        {
            round(d["bounds"][0], 3)
            for d in placement["placed"]
            if d["source"] == group["dummy_source"]
        }
    )


def supply_plan(placement):
    """Return the planned rectangles as dictionaries (net, layer, box, role)."""
    plan = []

    def add(net, layer, box, role):
        box = tuple(round(v, 3) for v in box)
        assert box[0] < box[2] and box[1] < box[3], (role, box)
        plan.append({"net": net, "layer": layer, "box": box, "role": role})

    # ------------------------------------------------------------- AVDD
    pref = ring(placement, "PREF")
    fold = ring(placement, "MPFOLD")
    sink = ring(placement, "MPSINK")
    for name, sides in (("PREF", pref), ("MPFOLD", fold), ("MPSINK", sink)):
        for side, box in sides.items():
            add("avdd", "M5", box, f"{name} dummy-ring rail {side}")
    add(
        "avdd",
        "M5",
        (pref["E"][0], fold["N"][1], fold["W"][2], pref["N"][3]),
        "PREF to MPFOLD upper link",
    )
    add(
        "avdd",
        "M5",
        (pref["E"][0], pref["S"][1], fold["W"][2], fold["S"][3]),
        "PREF to MPFOLD lower link",
    )
    # The reference strap uses the M5 corridor between the two arrays. The
    # rings join underneath on M4, between facing dummy pads. The last column
    # is left out because vbn_fold already uses M4 there.
    corridor_bottom, corridor_top = sink["N"][3], fold["S"][1]
    for index, origin in enumerate(column_origins(placement, "MPSINK")[:7]):
        right = origin + 9.4 if index != 1 else origin + 9.1  # vbp_i at 202.2
        add(
            "avdd",
            "M4",
            (origin + 1.4, corridor_bottom - 2.65, right, corridor_top + 2.5),
            "MPSINK to MPFOLD bridge under the reference corridor",
        )
    # East rail above the amplifier, right-hand drop, and the output-load ring.
    add("avdd", "M5", (sink["E"][0], 204.0, 679.5, 214.0), "east rail")
    add("avdd", "M5", (669.5, 2.0, 679.5, 214.0), "right-hand drop")
    load = ring(placement, "PLOAD1")
    add("avdd", "M5", (576.5, 88.6, 679.5, load["N"][3]), "PLOAD dummy-ring rail N")
    add(
        "avdd",
        "M5",
        (load["E"][0], 10.5, load["E"][2], load["N"][3]),
        "PLOAD dummy-ring rail E",
    )
    add("avdd", "M5", (586.5, 10.5, 679.5, 31.5), "PLOAD dummy-ring rail S")
    add("avdd", "M5", (569.7, 2.0, 679.5, 6.4), "PLOAD source-bus feed")
    add("avdd", "M5", (569.7, 2.0, 573.0, 9.6), "PLOAD source-bus anchor")
    # Core mirrors: one spine over the facing dummy columns of M11-M13 and
    # M1/M2/M5, directly above the M1/M2/M5 source-bus trunk.
    add("avdd", "M5", (59.0, 4.0, 75.3, 115.6), "core-mirror spine")
    add("avdd", "M5", (59.0, 115.6, 70.5, 180.0), "core-mirror spine, upper part")
    # The trim and reference straps cross between the switches and the ladder,
    # so the spine reaches the PREF ring on M4 and leaves M5 free there.
    add("avdd", "M4", (60.5, 171.2, 70.5, 233.3), "spine to PREF link, lower part")
    add("avdd", "M4", (60.5, 235.5, 70.5, 321.0), "spine to PREF link, upper part")
    add("avdd", "M5", (60.5, 229.5, 70.5, 239.3), "link jumper over the vq3 M4 wire")

    # ------------------------------------------------------------- AVSS
    add("avss", "M5", (123.5, 1.7, 566.0, 6.0), "bottom rail")
    # The M3/M4 drains (v1, v2) leave their array to the north-west on M5,
    # past the lifted vgn2 route, so that side stays open: one rail on the
    # south dummy row and a narrow neck up the west dummy column, backed by
    # the dummy units' own M4. North of the array the PNP-array return widens
    # and turns along the south edge of the array, clear of v1, v2, vq3, R1.
    m3 = ring(placement, "M3")
    west = m3["W"]
    add("avss", "M5", (west[0], 1.7, 159.0, m3["S"][3]), "M3/M4 dummy-ring rail S")
    add("avss", "M5", (west[0], m3["S"][3], 125.7, 66.0), "core return, neck")
    add("avss", "M4", (124.4, 22.0, 128.4, 31.0), "core return, dummy-pad bridge")
    add("avss", "M5", (west[0], 66.0, west[2], 123.2), "core return")
    add("avss", "M5", (83.5, 117.2, west[2], 123.2), "PNP-array return rail")
    fold_n = ring(placement, "MFOLD1")
    add(
        "avss",
        "M5",
        (fold_n["S"][0], 1.7, fold_n["S"][2], 28.5),
        "MFOLD dummy-ring rail S",
    )
    add("avss", "M5", (fold_n["E"][0], 1.7, 544.7, 122.4), "MFOLD rail E and leg")
    add("avss", "M5", (497.6, 32.3, 507.6, 122.4), "MFOLD rail W and leg")
    add(
        "avss",
        "M5",
        (497.6, fold_n["N"][1], 544.7, fold_n["N"][3]),
        "MFOLD dummy-ring rail N",
    )
    bias = ring(placement, "VBNSINK")
    sinks = ring(placement, "MSINK1")
    add("avss", "M5", (bias["W"][0], 117.2, sinks["W"][2], 122.4), "mid rail")
    add(
        "avss",
        "M5",
        (bias["S"][0], 117.2, bias["S"][2], bias["S"][3]),
        "VBNSINK dummy-ring rail S",
    )
    for side in "NEW":
        add("avss", "M5", bias[side], f"VBNSINK dummy-ring rail {side}")
    add(
        "avss",
        "M5",
        (560.95, 117.2, sinks["W"][2], sinks["W"][3]),
        "C1 bottom-plate strip and MSINK rail W",
    )
    for side in "NSE":
        add("avss", "M5", sinks[side], f"MSINK dummy-ring rail {side}")
    # The capacitor-bank return passes under the AVDD east rail on M4.
    add("avss", "M4", (595.2, 190.0, 615.6, 237.0), "underpass to capacitor rail A")
    add("avss", "M4", (620.0, 190.0, 640.4, 237.0), "underpass to capacitor rail B")
    add("avss", "M5", (286.0, 231.0, 668.4, 241.0), "capacitor-bank rail")
    capacitors = {
        d["source"]: d for d in placement["placed"] if d["model"].startswith("cap_")
    }
    for name in ("C2", "C3", "C4", "C5"):
        strip = capacitors[name]["pins"][1][0] - 2.75  # bottom-plate strip edge
        add("avss", "M5", (strip, 231.0, strip + 4.85, 391.6), "capacitor finger")
    strip = capacitors["C6"]["pins"][1][0] - 2.75
    add("avss", "M5", (strip, 231.0, strip + 3.05, 391.6), "capacitor finger, edge")

    # ------------------------------------------------------------- VREF
    # Each lower/upper capacitor pair shares its top plate across the row gap,
    # so the fingers above do not isolate the lower row from the output strap.
    for name in ("C2", "C3", "C4", "C5", "C6"):
        centre = capacitors[name]["pins"][0][0]
        add("vref", "M5", (centre - 15, 313.0, centre + 15, 322.0), "capacitor tie")
    return plan

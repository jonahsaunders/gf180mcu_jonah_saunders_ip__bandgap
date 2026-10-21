"""Explicit unit patterns, quiet dummy ties, and placement constraints.

Functional W/L and multiplicities stay in the schematic. Dummy multiplicities
are also explicit schematic instances; placement must consume each exactly.
"""

# None sites become tied-off dummy units, including the full outer perimeter.
ARRAYS = [
    (("M1", "M2", "M5"), ["ABCCBA", "BCAACB", "CABBAC"]),
    (("M11", "M12", "M13"), [".AB.C", ".CBA.", ".C.AB", "BC.A.", "A.BC."]),
    (("MINP", "MINN"), ["BABAA.BBAAB"]),
    (("PCAS1", "PCAS2"), ["ABBAAB", "BAABBA"]),
    (("PLOAD1", "PLOAD2"), ["ABBAAB", "BAABBA"]),
    (("MSINK1", "MSINK2"), ["ABBAAB", "BAABBA"]),
    (("PREF", "PRMIR"), ["BABAA.BBAAB"]),
    (("M3", "M4"), ["ABBA"]),
    (("MFOLD1", "MFOLD2"), ["AB"]),
    (("M16", "M18"), ["AB"]),
    (("MPSINK",), ["AAAAAA", "AAAAAA", "AAAAA."]),
    (("MPFOLD",), ["AAAAAA", "AAAAAA"]),
    (("VBNSINK",), ["AA"]),
]


def padded_pattern(rows):
    width = len(rows[0])
    assert all(len(row) == width for row in rows)
    return ["." * (width + 2), *["." + row + "." for row in rows], "." * (width + 2)]


def dummy_name(names):
    return "DUM_" + names[0]


def dummy_count(rows):
    return sum(row.count(".") for row in padded_pattern(rows))


# Provisional 1270 x 410 um harness area; the core uses only what placement needs.
CAP_X, CAP_Y = 285.0, 243.0
ROUTE_WIDTH, ROUTE_HEIGHT = 688.0, 408.0


def routing_exclusions(placement, net, aliases):
    """Block unrelated conductors over whole matched arrays, including dummies."""

    def canonical(name):
        while name in aliases:
            name = aliases[name]
        return name

    for group in placement["groups"]:
        if not group.get("matching") and "Q1" not in group["names"]:
            continue
        related = {
            canonical(node)
            for d in placement["placed"]
            if d["source"] in group["names"]
            for node in d["nodes"]
        }
        if canonical(net) not in related:
            x, y = group["x"], group["y"]
            yield (x, y, x + group["width"], y + group["height"])
    # Keep startup and bias wires off the PTAT resistor. Its own terminals
    # remain accessible, and the PNP array above is treated as one circuit.
    for device in placement["placed"]:
        if device["source"] == "R1":
            related = {canonical(node) for node in device["nodes"]}
            if canonical(net) not in related:
                yield tuple(device["bounds"])

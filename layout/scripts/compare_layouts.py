"""Compare every GDS polygon and text label, ignoring file timestamps."""

import argparse
import hashlib
import json
from pathlib import Path

import klayout.db as k


def compare(first, second, output):
    layouts = []
    for path in (first, second):
        layout = k.Layout()
        layout.read(str(path))
        layouts.append(layout)
    assert layouts[0].dbu == layouts[1].dbu
    names = {
        layout.get_info(index).to_s()
        for layout in layouts
        for index in layout.layer_indexes()
    }
    differences = []
    for name in sorted(names):
        regions, labels = [], []
        for layout in layouts:
            region, texts = k.Region(), []
            for index in layout.layer_indexes():
                if layout.get_info(index).to_s() != name:
                    continue
                iterator = layout.top_cell().begin_shapes_rec(index)
                region += k.Region(iterator)
                iterator = layout.top_cell().begin_shapes_rec(index)
                while not iterator.at_end():
                    if iterator.shape().is_text():
                        texts.append(
                            str(iterator.shape().text.transformed(iterator.trans()))
                        )
                    iterator.next()
            regions.append(region.merged())
            labels.append(sorted(texts))
        if not (regions[0] ^ regions[1]).is_empty() or labels[0] != labels[1]:
            differences.append(name)
    record = {
        "layers_compared": len(names),
        "different_layers": differences,
        "geometry_and_text_equal": not differences,
        "input_sha256": {
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (first, second)
        },
    }
    output.write_text(json.dumps(record, indent=2) + "\n")
    assert not differences, differences
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    compare(args.first, args.second, args.out)

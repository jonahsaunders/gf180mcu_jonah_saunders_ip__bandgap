"""Rerun only unsupported noise cases with Sparse; preserve every first attempt."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys

from review_power_rails import run_case, sha

root = Path(sys.argv[1]).resolve()
output = root / "power_review"
path = output / "comparison.json"
report = json.loads(path.read_text())
assert len(report["additional_screens"]) == 12
assert all(row["functional"] for row in report["additional_screens"]
           if row["layout"] == "candidate" and row["kind"] != "dc_noise")
old = [row for row in report["additional_screens"] if row["kind"] == "dc_noise"]
assert len(old) == 4
assert all(any("not (yet) supported" in error for error in row["errors"]) for row in old)
report["superseded_noise_attempts"] = old
cases = []
for row in old:
    source = root / "baseline" if row["layout"] == "baseline" else root
    assert sha(source / "pex/Bandgap_Core_Res_rcc_simulation.spice") == row["rcc_sha256"]
    cases.append((row["layout"], source, row["supply_V"], "dc_noise", output / "sparse_retry", 600))
with ThreadPoolExecutor(max_workers=4) as pool:
    new = list(pool.map(run_case, cases))
key = lambda row: (row["layout"], row["kind"], row["supply_V"])
replacements = {key(row): row for row in new}
report["additional_screens"] = [replacements.get(key(row), row) for row in report["additional_screens"]]
report["incomplete_screens"] = sum(row["status"] != "COMPLETE" for row in report["additional_screens"])
report["startup_functional_failures"] = [row for row in report["additional_screens"] if row.get("functional") is False]
path.write_text(json.dumps(report, indent=2)+"\n")
assert all(row["status"] == "COMPLETE" for row in new), new
print("Four Sparse line/load/noise cases completed; original attempts retained.")

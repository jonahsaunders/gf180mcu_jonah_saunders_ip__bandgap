"""Package fresh candidate evidence without mixing in earlier release files."""

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path, required=True)
    args = parser.parse_args()
    root = args.build
    out = root / "review-package"
    out.mkdir(exist_ok=False)
    names = ["filled_candidate.gds", "filled_drc.log", "filled_drc.lyrdb", "filled.lvsdb",
             "filled_lvs.log", "filled.cir", "reference.spice", "placement.json",
             "primitive_manifest.json", "routing_status.json", "power_summary.json", "power_plan.json",
             "fill_summary.json", "matching_audit.json", "matched_local_routes.json", "matched_source_buses.json",
             "sensitive_routes.json", "extractor_version.json", "extract_parasitics.log", "precision_layout_audit.json"]
    for name in names:
        source = root / name
        target = out / ("Bandgap_Core_Res.gds" if name == "filled_candidate.gds" else name)
        assert source.is_file(), name
        shutil.copy2(source, target)
    for folder in ["pex", "mirror_bias", "mirror_review", "psrr_diagnostics", "postlayout", "power_review", "model"]:
        for source in (root / folder).rglob("*"):
            if source.is_file() and source.suffix in [".json", ".spice", ".log", ".dat", ".png", ".glb", ".lvsdb"]:
                target = out / source.relative_to(root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    for name in ["audit_magic_lvs.log", "audit_rc.log"]:
        shutil.copy2(root / name, out / "pex" / name)
    shutil.copy2(root / "postlayout/nominal_dc_summary.json", out / "nominal_screen.json")
    shutil.copy2(root / "postlayout/pvt_dc/summary.json", out / "pvt_dc_screen.json")
    for source in list(out.rglob("*")):
        if source.suffix in [".lvsdb", ".dat"]:
            with source.with_suffix(source.suffix+".gz").open("wb") as file:
                with gzip.GzipFile(filename="", mode="wb", fileobj=file, mtime=0) as zipped:
                    zipped.write(source.read_bytes())
            source.unlink()
    verification = json.loads((root / "verification.json").read_text())
    comparison = json.loads((root / "power_review/comparison.json").read_text())
    verification["sha256"] = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in sorted(out.rglob("*")) if p.is_file()}
    verification["baseline_commit"] = comparison["baseline_commit"]
    verification["rail_commit"] = comparison["rail_commit"]
    verification["completed_screens"] = {"nominal_DC_AC_cases": 10, "fixed_code_corner_DC_points": 360,
                                          "additional_screens": len(comparison["additional_screens"]),
                                          "incomplete_additional_screens": comparison["incomplete_screens"],
                                          "startup_functional_failures": len(comparison["startup_functional_failures"])}
    verification["extraction_model"] = "Magic RC and capacitance-conserving RCC approximation; wells/substrate lumped"
    (out / "verification.json").write_text(json.dumps(verification, indent=2)+"\n")
    print("Packaged", len(verification["sha256"]), "hashed files", flush=True)


if __name__ == "__main__":
    main()

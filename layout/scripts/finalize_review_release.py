"""Check saved noise spectra without changing simulator or model values.

Native integrated totals remain NaN and the original cases remain INCOMPLETE.
Finite spectral density is integrated independently with explicit methods.
"""
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

from PIL import Image

root = Path(sys.argv[1]).resolve()
manifest_path = root / "verification.json"
manifest = json.loads(manifest_path.read_text())
digest = lambda p: hashlib.sha256(p.read_bytes()).hexdigest()
for name, expected in manifest["sha256"].items():
    assert digest(root / name) == expected, name

rows = []
for layout in ["baseline", "candidate"]:
    for supply in [3.3, 5]:
        relative = f"power_review/sparse_retry/{layout}/dc_noise_{supply:g}V/noise_spectrum.dat.gz"
        text = gzip.decompress((root / relative).read_bytes()).decode()
        points = [list(map(float, line.split())) for line in text.splitlines()[1:] if line.strip()]
        assert len(points) == 241
        assert all(len(p) == 2 and all(math.isfinite(x) for x in p) and p[1] > 0 for p in points)
        assert abs(points[0][0]-1) < 1e-9 and abs(points[-1][0]-1e6) < 1e-5
        trapezoid = powerlaw = 0.0
        for (x, y), (xx, yy) in zip(points, points[1:]):
            assert xx > x
            trapezoid += (xx-x)*(y*y+yy*yy)/2
            slope = math.log((yy/y)**2)/math.log(xx/x)
            powerlaw += y*y*x*(math.log(xx/x) if abs(slope+1) < 1e-10 else
                              ((xx/x)**(slope+1)-1)/(slope+1))
        row = {"layout": layout, "supply_V": supply, "source": relative,
               "compressed_sha256": digest(root / relative), "frequency_points": 241,
               "native_integrated_noise_status": "INVALID_NAN_RETAINED",
               "trapezoid_rms_V": math.sqrt(trapezoid),
               "piecewise_powerlaw_rms_V": math.sqrt(powerlaw)}
        for frequency in [10, 1000]:
            match = [p for p in points if abs(p[0]-frequency) < 1e-6]
            assert len(match) == 1
            row[f"noise_{frequency:g}Hz_V_rtHz"] = match[0][1]
        rows.append(row)

report = {"status": "FINITE_SPECTRA_NUMERICALLY_INTEGRATED_NOT_NATIVE_NOISE_PASS",
          "conditions": "RCC, TT, 25 C, trim 72, DVDD 3.3 V, no external load, Sparse solver",
          "band_Hz": [1, 1e6],
          "method": "Square saved amplitude spectral density, trapezoid-integrate PSD over frequency, take square root",
          "cross_check": "Piecewise power-law PSD integration; difference estimates quadrature sensitivity, not model uncertainty",
          "limitations": "Native ngspice onoise_total is NaN for both layouts; full noise model/integrator qualification remains open",
          "cases": rows}
(root / "power_review/noise_spectrum_check.json").write_text(json.dumps(report, indent=2)+"\n")

preview = root / "model/preview.png"
with Image.open(root / "model/bandgap-design.png") as image:
    image.thumbnail((1600, 1000), Image.Resampling.LANCZOS)
    image.save(preview, optimize=True)
assert preview.stat().st_size < 1000000
model_path = root / "model/provenance.json"
model = json.loads(model_path.read_text())
model["sha256"]["preview.png"] = digest(preview)
model["preview_note"] = "Reduced-resolution QA preview; original geometry/model unchanged"
model_path.write_text(json.dumps(model, indent=2)+"\n")
manifest["noise_spectrum_review"] = {
    "finite_spectra": 4, "frequency_points_each": 241,
    "numerical_rms_estimates": 4, "native_integrated_totals": "INVALID_NAN_RETAINED"}
manifest["sha256"] = {str(p.relative_to(root)): digest(p)
                      for p in sorted(root.rglob("*"))
                      if p.is_file() and ".git" not in p.relative_to(root).parts and p != manifest_path}
manifest_path.write_text(json.dumps(manifest, indent=2)+"\n")
for name, expected in manifest["sha256"].items():
    assert digest(root / name) == expected, name
print(json.dumps(report, indent=2), flush=True)
print("Validated", len(manifest["sha256"]), "release hashes; preview bytes", preview.stat().st_size)

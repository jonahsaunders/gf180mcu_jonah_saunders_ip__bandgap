"""Shared paths for the public layout build; no machine-specific workspace paths."""

from pathlib import Path
import os

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPTS = Path(__file__).resolve().parent
BUILD_DIR = Path(
    os.environ.get("BANDGAP_LAYOUT_BUILD", REPOSITORY / "layout/build")
).resolve()
PDK = Path("/foss/pdks/gf180mcuD")
BUILD_DIR.mkdir(parents=True, exist_ok=True)

#!/usr/bin/env bash
# Compare an immutable main snapshot with the candidate in one toolchain.
set -eo pipefail
source /foss/tools/sak/sak-pdk-script.sh gf180mcuD >/dev/null
set -u
cd /design
export OMP_NUM_THREADS=1
export BANDGAP_MAGIC=/design/layout/build/toolchain/magic/bin/magic
export BANDGAP_LAYOUT_BUILD=/design/layout/build/baseline
python3 .review-baseline/layout/scripts/layout_flow.py build
python3 .review-baseline/layout/scripts/layout_flow.py extract
python3 .review-baseline/layout/scripts/screen_extracted.py --dc-only
python3 .review-baseline/layout/scripts/screen_pvt_dc.py
python3 .review-baseline/layout/scripts/diagnose_psrr.py --build "$BANDGAP_LAYOUT_BUILD" --output "$BANDGAP_LAYOUT_BUILD/psrr_diagnostics" --supply-coupling
python3 .review-baseline/layout/scripts/screen_mirror_bias.py --build "$BANDGAP_LAYOUT_BUILD" --reference-ac "$BANDGAP_LAYOUT_BUILD/psrr_diagnostics"
python3 .review-baseline/layout/scripts/audit_precision_layout.py --build "$BANDGAP_LAYOUT_BUILD"
python3 .review-baseline/layout/scripts/audit_mirror_layout.py --release "$BANDGAP_LAYOUT_BUILD" --out "$BANDGAP_LAYOUT_BUILD/mirror_review"
python3 layout/scripts/review_power_rails.py --baseline "$BANDGAP_LAYOUT_BUILD" --candidate /design/layout/build --output /design/layout/build/power_review

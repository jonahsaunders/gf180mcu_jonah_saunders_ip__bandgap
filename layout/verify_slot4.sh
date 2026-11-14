#!/usr/bin/env bash
# Run inside the pinned IIC-OSIC-TOOLS image documented in README.txt.
# Rebuilds and verifies the Chipalooza slot-4 integration (gds/slot_4.gds.gz).
set -eo pipefail
source /foss/tools/sak/sak-pdk-script.sh gf180mcuD >/dev/null
set -u
layout_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
export BANDGAP_LAYOUT_BUILD="${BANDGAP_LAYOUT_BUILD:-$layout_directory/build}"
export OMP_NUM_THREADS=1
python3 "$layout_directory/scripts/verify_slot4.py"

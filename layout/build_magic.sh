#!/usr/bin/env bash
# Run in the documented EDA image as root. Dependencies affect only that
# disposable container; source and installation stay in layout/build/toolchain.
set -euo pipefail
layout_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
toolchain="$layout_directory/build/toolchain"
revision=bed8d4ff84333767aa923d469d339cd05c56a00a
mkdir -p "$toolchain"
apt-get update
apt-get install -y tcl8.6-dev tk8.6-dev zlib1g-dev libreadline-dev
if [[ ! -d "$toolchain/magic-source/.git" ]]; then
    git -c core.autocrlf=false clone --no-checkout \
        https://github.com/RTimothyEdwards/magic.git "$toolchain/magic-source"
fi
git -C "$toolchain/magic-source" -c core.autocrlf=false checkout --detach "$revision"
cd "$toolchain/magic-source"
./configure --prefix="$toolchain/magic" --without-x --without-opengl --disable-magic-builddate
make -j4
make install
"$toolchain/magic/bin/magic" --version
printf 'For extraction set BANDGAP_MAGIC=%s\n' "$toolchain/magic/bin/magic"

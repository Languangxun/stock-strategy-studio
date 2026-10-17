#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p ../core/native
rustc --edition=2021 -O -C target-cpu=native --crate-type cdylib backtest_core.rs -o ../core/native/libbtcore.so
echo "built $(pwd)/../core/native/libbtcore.so"

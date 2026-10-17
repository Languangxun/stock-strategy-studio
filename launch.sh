#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  python3 -m venv --system-site-packages .venv
  .venv/bin/python -m pip install -r requirements.txt
fi

if [ ! -f core/native/libbtcore.so ] && command -v rustc >/dev/null 2>&1; then
  ./rust/build.sh || echo "Rust 加速构建失败，将使用 Python 回测引擎"
fi

exec .venv/bin/python app.py

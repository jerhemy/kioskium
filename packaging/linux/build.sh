#!/usr/bin/env bash
# Build the one-folder Linux distribution (x86_64 desktop / test rigs) with PyInstaller.
#
# Uses .venv in the repository root and the pinned versions in requirements/linux.txt.
# The result links against this machine's glibc; PySide6 6.12 needs glibc >= 2.39
# (Ubuntu 24.04 or newer). For older or embedded targets use packaging/linux-arm64
# (glibc >= 2.31).
set -euo pipefail
root="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$root"

if [ ! -x .venv/bin/python ]; then
    "${PYTHON:-python3}" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip --quiet
.venv/bin/python -m pip install --quiet -r requirements/linux.txt
.venv/bin/python -m pip install --quiet --no-deps -e .

out=dist/linux-x86_64
[ "${1:-}" = "--clean" ] && rm -rf build/linux-x86_64 "$out"
.venv/bin/python -m PyInstaller packaging/pyinstaller/kioskium.spec --noconfirm --clean \
    --distpath "$out" --workpath build/linux-x86_64
tar -C "$out" -czf "$out/kiosk-linux-x86_64.tar.gz" kiosk

echo
echo "Built: $root/$out/kiosk/kiosk  (archive: $out/kiosk-linux-x86_64.tar.gz)"
echo "Try:   $out/kiosk/kiosk --windowed kioskium:test"

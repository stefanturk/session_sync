#!/usr/bin/env bash
# Build SessionSync.app — a double-clickable Mac app with no Python needed.
#
#   scripts/build_app.sh
#
# The result is dist/SessionSync.app. Drag it to /Applications.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

python="${PYTHON:-$root/.venv/bin/python}"
if [ ! -x "$python" ]; then
    echo "No interpreter at $python — create the venv first:" >&2
    echo "    python3.12 -m venv .venv && .venv/bin/pip install -e . pyinstaller" >&2
    exit 1
fi

if ! "$python" -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller…"
    "$python" -m pip install --quiet pyinstaller
fi

echo "Building SessionSync.app…"
rm -rf build dist
"$python" -m PyInstaller \
    --noconfirm \
    --clean \
    --distpath dist \
    --workpath build \
    packaging/SessionSync.spec

app="$root/dist/SessionSync.app"
if [ ! -d "$app" ]; then
    echo "Build finished but $app is missing." >&2
    exit 1
fi

# An unsigned app is quarantined on first launch. Signing it locally (ad-hoc)
# is enough for the Mac that built it and avoids the "damaged" dialog.
codesign --force --deep --sign - "$app" 2>/dev/null || \
    echo "Note: could not ad-hoc sign; right-click → Open on first launch."

echo
echo "Built: $app  ($(du -sh "$app" | cut -f1))"
echo "Try it:  open '$app'"

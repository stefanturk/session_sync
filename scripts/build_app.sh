#!/usr/bin/env bash
# Build SessionSync.app — a double-clickable Mac app with no Python needed.
#
#   scripts/build_app.sh              -> installs into ~/Applications
#   scripts/build_app.sh /some/dir    -> somewhere else
#
# It installs to ~/Applications rather than leaving the bundle in dist/,
# because the Dock pins whichever bundle you dragged from. dist/ is deleted and
# rebuilt on every run, so a Dock icon pointing there would break the first time
# you rebuilt — silently, since the Dock keeps showing the icon either way.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

dest="${1:-$HOME/Applications}"
app="$dest/SessionSync.app"

python="${PYTHON:-$root/.venv/bin/python}"
if [ ! -x "$python" ]; then
    echo "No interpreter at $python — create the venv first:" >&2
    echo "    python3.12 -m venv .venv && .venv/bin/pip install -e '.[package]'" >&2
    exit 1
fi

if ! "$python" -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller…"
    "$python" -m pip install --quiet pyinstaller
fi

# A second copy anywhere else is how the Dock goes stale: it stays pinned to
# the bundle it was dragged from while only this one gets rebuilt.
for stray in "/Applications/SessionSync.app" "$HOME/Applications/SessionSync.app"; do
    if [ "$stray" != "$app" ] && [ -e "$stray" ]; then
        echo "Removing a stray copy at $stray — the Dock should only point at $app."
        rm -rf "$stray"
    fi
done

# --- the icon --------------------------------------------------------------
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
iconset="$work/SessionSync.iconset"
mkdir -p "$iconset"

if "$python" "$root/scripts/render_icon.py" "$work/master.png" 1024 2>/dev/null; then
    for size in 16 32 128 256 512; do
        sips -z $size $size "$work/master.png" \
            --out "$iconset/icon_${size}x${size}.png" >/dev/null 2>&1
        sips -z $((size * 2)) $((size * 2)) "$work/master.png" \
            --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null 2>&1
    done
    if iconutil -c icns "$iconset" -o "$work/SessionSync.icns" 2>/dev/null; then
        export SESSIONSYNC_ICNS="$work/SessionSync.icns"
    fi
fi
[ -n "${SESSIONSYNC_ICNS:-}" ] || echo "Warning: no icon rendered; using the generic one." >&2

# --- the bundle ------------------------------------------------------------
echo "Building SessionSync.app…"
rm -rf build dist
"$python" -m PyInstaller \
    --noconfirm --clean \
    --distpath dist --workpath build \
    packaging/SessionSync.spec

[ -d "dist/SessionSync.app" ] || { echo "Build produced no bundle." >&2; exit 1; }

mkdir -p "$dest"
rm -rf "$app"
mv "dist/SessionSync.app" "$app"
rm -rf build dist

# An unsigned app is quarantined on first launch. Signing ad-hoc is enough for
# the Mac that built it and avoids the "damaged" dialog.
codesign --force --deep --sign - "$app" 2>/dev/null ||
    echo "Note: could not ad-hoc sign; right-click → Open on first launch." >&2

# Tell the Dock and Spotlight the bundle changed, or the old icon lingers.
touch "$app"
/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
    -f "$app" >/dev/null 2>&1 || true

echo
echo "Built $app  ($(du -sh "$app" | cut -f1))"
echo "Open it:            open '$app'"
echo "Keep it in the Dock: launch it, then right-click its Dock icon → Options → Keep in Dock"
echo
echo "Logs: ~/Library/Logs/SessionSync.log"

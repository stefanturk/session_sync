#!/usr/bin/env bash
# Build SessionSync.app — a double-clickable Mac app with no Python needed.
#
#   scripts/build_app.sh              -> installs into /Applications (~/Applications if that isn't writable)
#   scripts/build_app.sh /some/dir    -> somewhere else
#
# It installs to an Applications folder rather than leaving the bundle in dist/,
# because the Dock pins whichever bundle you dragged from. dist/ is deleted and
# rebuilt on every run, so a Dock icon pointing there would break the first time
# you rebuilt — silently, since the Dock keeps showing the icon either way.
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"

if [ -n "${1:-}" ]; then dest="$1"
elif [ -w /Applications ]; then dest="/Applications"      # where people look for apps
else dest="$HOME/Applications"; fi
app="$dest/SessionSync.app"

# An existing venv always wins over $PYTHON: Steezy Studio sets PYTHON to its own engine Python on
# every build, and that has none of this app's packages (PySide6), so using it would build a broken
# app with a generic icon on a machine that has a working venv.
python="$root/.venv/bin/python"
[ -x "$python" ] || python="${PYTHON:-$python}"
# No venv here? Use the Steezy Studio engine's Python (3.12) if it is installed
# and this app's packages install into it. An existing venv is always preferred.
engine_py="${STEEZY_ENGINE_DIR:-$HOME/Library/Application Support/Steezy Studio/engine}/python/bin/python3"
if [ ! -x "$python" ] && [ -x "$engine_py" ] \
    && "$engine_py" -c 'import sys; assert sys.version_info >= (3, 12)' >/dev/null 2>&1 \
    && "$engine_py" -m pip install --quiet --prefer-binary -e ".[package]" >/dev/null 2>&1; then
    python="$engine_py"
fi
if [ ! -x "$python" ]; then
    echo "No interpreter at $python — create the venv first:" >&2
    echo "    python3.12 -m venv .venv && .venv/bin/pip install -e '.[package]'" >&2
    exit 1
fi

if ! "$python" -c "import PyInstaller" 2>/dev/null; then
    echo "Installing PyInstaller…"
    "$python" -m pip install --quiet pyinstaller
fi

# Shared build helpers (swap an app in with no gap; move a Dock pin before an old copy goes) live in
# Steezy Studio. Optional: without them this still builds, the plain way.
_build="${STEEZY_BUILD:-$root/../steezy_studio/build}"

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
stage="$dest/.SessionSync.app.building"
rm -rf "$stage"
mv "dist/SessionSync.app" "$stage"
rm -rf build dist
if [ -f "$_build/appswap.py" ]; then /usr/bin/python3 "$_build/appswap.py" "$stage" "$app"
else rm -rf "$app"; mv "$stage" "$app"; fi

# A second copy anywhere else is how the Dock goes stale: it stays pinned to the bundle it was dragged
# from while only this one gets rebuilt. Move its pin to this one first (a pin to a deleted app turns
# into a question mark), then remove it.
for stray in "/Applications/SessionSync.app" "$HOME/Applications/SessionSync.app"; do
    if [ "$stray" != "$app" ] && [ -e "$stray" ]; then
        echo "Moving $stray to $app - the Dock should only point at one."
        [ -f "$_build/dock_repin.py" ] && /usr/bin/python3 "$_build/dock_repin.py" "$stray" "$app" || true
        rm -rf "$stray"
    fi
done

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

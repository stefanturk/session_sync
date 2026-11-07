# PyInstaller spec for SessionSync: the Mac app and the Windows program.
#
# Build with:  scripts/build_app.sh (Mac)  or  scripts/build_windows.ps1 (Windows)
#
# PySide6 pulls in a great deal that a four-button window has no use for
# (WebEngine alone is hundreds of megabytes), so the unused modules are excluded
# rather than shipped.

import os
import sys

block_cipher = None
WINDOWS = sys.platform == "win32"

# Rendered by scripts/render_icon.py during the build; absent if that failed,
# in which case the app gets the generic bundle icon rather than no app.
# Windows takes a PNG (PyInstaller converts it to .ico when Pillow is installed).
ICON = os.environ.get("SESSIONSYNC_ICO" if WINDOWS else "SESSIONSYNC_ICNS") or None
if ICON and not os.path.exists(ICON):
    ICON = None

UNUSED_QT = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtUiTools",
]

a = Analysis(
    ["entry.py"],
    pathex=[".."],
    binaries=[],
    datas=[],
    hiddenimports=["sessionsync.adapters.ableton.adapter"],
    hookspath=[],
    runtime_hooks=[],
    excludes=UNUSED_QT + ["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="SessionSync",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON if WINDOWS else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SessionSync",
)

if not WINDOWS:
  app = BUNDLE(
    coll,
    name="SessionSync.app",
    icon=ICON,
    bundle_identifier="com.stefan.sessionsync",
    info_plist={
        "CFBundleName": "SessionSync",
        "CFBundleDisplayName": "SessionSync",
        "CFBundleShortVersionString": "0.2.0",
        "CFBundleVersion": "0.2.0",
        # "▶ Open in Live" in a shared song folder is a sessionsync:// link
        # (see sessionsync/core/launcher.py); this is what makes it open the app.
        "CFBundleURLTypes": [
            {
                "CFBundleURLName": "com.stefan.sessionsync.open",
                "CFBundleURLSchemes": ["sessionsync"],
            }
        ],
        "NSHighResolutionCapable": True,
        # A real app with a Dock icon and a menu bar, not a background agent.
        "LSUIElement": False,
        "LSMinimumSystemVersion": "12.0",
        # Ableton projects live in exactly the folders macOS gates behind a
        # permission prompt. Without these keys the prompt appears with no
        # explanation, which reads like something is wrong with the app.
        "NSDesktopFolderUsageDescription":
            "SessionSync needs access to open Ableton projects kept on your Desktop.",
        "NSDocumentsFolderUsageDescription":
            "SessionSync needs access to open Ableton projects kept in Documents.",
        "NSDownloadsFolderUsageDescription":
            "SessionSync needs access to open Ableton projects kept in Downloads.",
        "NSRemovableVolumesUsageDescription":
            "SessionSync needs access to sync projects to an external drive.",
        "NSNetworkVolumesUsageDescription":
            "SessionSync needs access to sync projects to a NAS or shared folder.",
    },
)

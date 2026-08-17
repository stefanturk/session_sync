# PyInstaller spec for the SessionSync Mac app.
#
# Build with:  scripts/build_app.sh
#
# PySide6 pulls in a great deal that a four-button window has no use for
# (WebEngine alone is hundreds of megabytes), so the unused modules are excluded
# rather than shipped.

block_cipher = None

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
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="SessionSync",
)

app = BUNDLE(
    coll,
    name="SessionSync.app",
    icon=None,
    bundle_identifier="com.sessionsync.app",
    info_plist={
        "CFBundleName": "SessionSync",
        "CFBundleDisplayName": "SessionSync",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleVersion": "0.1.0",
        "NSHighResolutionCapable": True,
        # Nothing here is a document-based app, and a background agent would
        # hide the window — this is an ordinary foreground app.
        "LSUIElement": False,
        "LSMinimumSystemVersion": "12.0",
    },
)

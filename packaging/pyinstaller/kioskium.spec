# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for kioskium (Windows and Linux).

Build from the repository root:
    python -m PyInstaller packaging/pyinstaller/kioskium.spec --noconfirm
Output: dist/kiosk/kiosk[.exe] (+ kioskctl[.exe]) (one-folder build); the build scripts pass
--distpath dist/<platform> (windows-x86_64, linux-x86_64, linux-arm64). QtWebEngineProcess, the Chromium
resources and locales are collected by PyInstaller's PySide6 hooks.

Environment knobs:
    KIOSKIUM_CONSOLE=1   Windows: build a console variant that prints logs to the terminal.
    KIOSKIUM_EXTRA_LIBS  Linux: ':'-separated shared libraries to copy next to the bundled ones
                             (used for the libglvnd libGL on i.MX, see packaging/linux-arm64).
"""
import os
import sys
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parents[1]
CONSOLE = os.environ.get("KIOSKIUM_CONSOLE", "0") == "1"
IS_WIN = sys.platform.startswith("win")

a = Analysis(
    [str(Path(SPECPATH) / "kiosk_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # Licenses travel with every bundle: Kioskium's own, plus the LGPL/GPL texts and the notice for
    # the bundled Qt / PySide6 / Chromium (the PyPI wheels do not ship the LGPL text themselves).
    datas=[(str(ROOT / "LICENSE"), "licenses"), (str(ROOT / "licenses"), "licenses"),
           (str(ROOT / "src" / "kioskium" / "pages"), "kioskium/pages"),  # built-in pages (kioskium:test, ...)
           (str(ROOT / "src" / "kioskium" / "assets"), "kioskium/assets")],  # window icon
    hiddenimports=[
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtNetwork",
        # Imported by QtWebEngineWidgets at module load time (shiboken dependency modules).
        "PySide6.QtWebChannel",
        "PySide6.QtPrintSupport",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DInput", "PySide6.Qt3DLogic",
        "PySide6.Qt3DAnimation", "PySide6.Qt3DExtras", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "PySide6.QtGraphs", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtPdf",
        "PySide6.QtPdfWidgets", "PySide6.QtQuick3D", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
        "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtSerialPort", "PySide6.QtSpatialAudio",
        "PySide6.QtSql", "PySide6.QtSvgWidgets", "PySide6.QtTest", "PySide6.QtTextToSpeech",
        "PySide6.QtUiTools", "PySide6.QtXml", "PySide6.QtBluetooth", "PySide6.QtNfc",
        "PySide6.QtLocation", "PySide6.QtPositioning", "PySide6.QtDesigner", "PySide6.QtHelp",
        "PySide6.QtHttpServer", "PySide6.QtNetworkAuth", "PySide6.QtStateMachine", "PySide6.QtWebSockets",
        "PySide6.QtAsyncio", "PySide6.QtVirtualKeyboard", "tkinter", "unittest", "pydoc",
    ],
    noarchive=False,
)

# ---------------------------------------------------------------------------------------------
# Trim Qt modules the kiosk never uses but that the PySide6 hooks pull in through dependency
# scanning (~150 MB on Windows, ~250 MB on Linux). Kept: Core, Gui, Widgets, Network, OpenGL,
# Qml*, Quick, QuickWidgets, WebEngineCore/Widgets, WebChannel, Positioning, PrintSupport, Svg, DBus (Qt6Gui links it on Linux),
# Wayland client libs, the platform/imageformat/tls plugins, Chromium resources and locales.
# ---------------------------------------------------------------------------------------------
_DROP_MODULES = (
    "Quick3D", "QuickControls2", "QuickTemplates2", "QuickDialogs2", "QuickTimeline",
    "QuickParticles", "QuickShapes", "QuickEffects", "QuickTest", "QuickVectorImage", "QuickNativeStyle",
    "Pdf", "Multimedia", "SpatialAudio", "Charts", "DataVisualization", "Graphs", "3D", "ShaderTools",
    "Sql", "Bluetooth", "Nfc", "SerialPort", "SerialBus", "Sensors", "Location", "Designer", "Help",
    "RemoteObjects", "Scxml", "StateMachine", "TextToSpeech", "WebSockets", "HttpServer", "Test", "Xml",
    "Labs", "UiTools", "NetworkAuth", "WebView", "WebEngineQuick", "Concurrent",
    "ExampleIcons", "SvgWidgets", "OpenGLWidgets", "QmlXmlListModel", "QmlNetwork",
    "QmlCompiler", "LanguageServer", "JsonRpc", "Protobuf", "Grpc", "QmlAssetDownloader",
    "EglFsKmsSupport", "EglFsKmsGbmSupport", "EglFSDeviceIntegration", "XcbQpa",
    "VirtualKeyboard",  # GPL-3.0: never shipped
)
_DROP_PY_MODULES = (
    "Qt3D", "QtCharts", "QtDataVisualization", "QtGraphs", "QtMultimedia", "QtPdf", "QtQuick3D",
    "QtRemoteObjects", "QtScxml", "QtSensors", "QtSerial", "QtSpatialAudio", "QtSql", "QtSvg", "QtTest",
    "QtTextToSpeech", "QtUiTools", "QtXml", "QtBluetooth", "QtNfc", "QtLocation", "QtPositioning",
    "QtDesigner", "QtHelp", "QtHttpServer", "QtNetworkAuth", "QtStateMachine", "QtWebSockets",
    "QtOpenGLWidgets", "QtConcurrent", "QtQuickControls2", "QtQuickTest",
    "QtWebEngineQuick", "QtVirtualKeyboard",
)
_DROP_TOOLS = (
    "assistant", "designer", "linguist", "lupdate", "lrelease", "qmlls", "qmllint", "qmlformat",
    "qmlimportscanner", "qsb", "balsam", "balsamui", "svgtoqml", "qml2puppet", "qmltyperegistrar",
    "qmlcachegen", "uic", "rcc", "lconvert",
)
_DROP_PLUGIN_DIRS = ("platforminputcontexts", "qmltooling", "sqldrivers", "sceneparsers", "geometryloaders", "designer", "multimedia",
                     "position", "sensors", "canbus", "texttospeech", "qmllint", "renderers", "renderplugins",
                     "assetimporters", "webview", "egldeviceintegrations", "xcbglintegrations")
_DROP_PLUGIN_FILES = ("libqxcb.so", "libqpdf", "qpdf",
                      "libqeglfs", "qeglfs", "libqlinuxfb", "libqvnc", "libqvkkhrdisplay", "qvnc", "qdirect2d")


def _keep(entry):
    dest = entry[0].replace("\\", "/")
    base = dest.rsplit("/", 1)[-1]
    stem = base[3:] if base.startswith("lib") else base  # libQt6Foo.so.6 / Qt6Foo.dll -> Qt6Foo...
    # Applies everywhere: on Linux PyInstaller also emits top-level symlinks for every Qt library.
    if stem.startswith("Qt6") and any(stem[3:].startswith(m) for m in _DROP_MODULES):
        return False
    if not dest.startswith("PySide6/"):
        return True
    if "/qml/" in dest:  # no QML imports are used (the widgets-based WebEngine needs none)
        return False
    if "/translations/" in dest and "qtwebengine_locales" not in dest:
        return False
    if "/qtwebengine_locales/" in dest and base not in ("en-US.pak", "en-GB.pak"):
        return False
    if "/plugins/" in dest:
        parts = dest.split("/plugins/", 1)[1].split("/")
        if parts[0] in _DROP_PLUGIN_DIRS:
            return False
        if any(base.startswith(p) for p in _DROP_PLUGIN_FILES):
            return False
    if (base.endswith(".pyd") or ".abi3.so" in base) and any(base.startswith(p) for p in _DROP_PY_MODULES):
        return False
    if dest.count("/") == 1 and (base in _DROP_TOOLS or base.split(".")[0] in _DROP_TOOLS):
        return False
    if "/libexec/" in dest and base != "QtWebEngineProcess":
        return False
    return True


# Linux: never bundle the GPU/display stack or the C++ runtime. Vendor EGL/GLES drivers (Vivante on
# i.MX) need their own libgbm/libdrm/wayland-egl and may need a newer libstdc++ than the build
# container has; the target image always provides these.
_LINUX_SYSTEM_LIBS = ("libgbm", "libdrm", "libEGL", "libGLESv2", "libwayland-egl", "libwayland-client",
                      "libwayland-cursor", "libudev", "libstdc++", "libgcc_s")


def _keep_system(entry):
    base = entry[0].replace("\\", "/").rsplit("/", 1)[-1]
    return IS_WIN or not base.startswith(_LINUX_SYSTEM_LIBS)


a.binaries = [b for b in a.binaries if _keep(b) and _keep_system(b)]
a.datas = [d for d in a.datas if _keep(d)]

# Extra shared libraries placed next to the bundled ones (Linux). The one-folder bootloader puts
# the bundle folder first on LD_LIBRARY_PATH, so these take precedence over system copies.
for lib in filter(None, os.environ.get("KIOSKIUM_EXTRA_LIBS", "").split(":")):
    p = Path(lib)
    if p.is_file():
        a.binaries.append((p.name, str(p), "BINARY"))

# Flat one-folder layout: libraries and data sit next to the executables instead of in _internal/.
# Both executables must use the same value since they share one folder.
CONTENTS_DIR = "."

# Executable icon (Windows; ignored elsewhere). Regenerate with packaging/icons/make_icons.py.
EXE_ICON = str(ROOT / "packaging" / "windows" / "kioskium.ico")

pyz = PYZ(a.pure)

# Second, console-mode executable for the control client (kioskctl). It shares the
# bundle folder; on Windows the kiosk exe is windowless so `ctl` needs its own console binary.
ctl_a = Analysis([str(Path(SPECPATH) / "kioskctl_entry.py")], pathex=[str(ROOT / "src")], binaries=[], datas=[], hiddenimports=[],
                 excludes=["PySide6", "shiboken6", "tkinter", "unittest", "pydoc"], noarchive=False)
ctl_pyz = PYZ(ctl_a.pure)
ctl_exe = EXE(ctl_pyz, ctl_a.scripts, [], exclude_binaries=True, name="kioskctl", debug=False,
              contents_directory=CONTENTS_DIR,
              bootloader_ignore_signals=False, strip=False, upx=False, console=True, icon=EXE_ICON)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="kiosk",
    contents_directory=CONTENTS_DIR,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=(CONSOLE if IS_WIN else True),
    disable_windowed_traceback=False,
    icon=EXE_ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    ctl_exe,
    ctl_a.binaries,
    ctl_a.datas,
    strip=False,
    upx=False,
    name="kiosk",
)

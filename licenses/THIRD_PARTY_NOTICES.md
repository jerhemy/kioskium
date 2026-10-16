# Third-party software in Kioskium binary releases

Kioskium itself is MIT licensed (`LICENSE`). Its binary bundles (the Windows zip and the Linux
tarballs) also contain the following components, unmodified, as separate dynamically loaded
libraries. They remain under their own licenses.

| Component | License | Source |
|---|---|---|
| Qt 6 (Core, Gui, Widgets, Network, Qml, Quick, WebChannel, Wayland, ...) | LGPL-3.0-only (`LGPL-3.0.txt`, which builds on `GPL-3.0.txt`) | https://download.qt.io/official_releases/qt/ · https://code.qt.io/ |
| Qt WebEngine, including Chromium and its third-party components | LGPL-3.0-only for Qt WebEngine; Chromium code under BSD-3-Clause and the licenses of its bundled components | https://code.qt.io/cgit/qt/qtwebengine.git/ · https://doc.qt.io/qt-6/qtwebengine-licensing.html |
| PySide6 and Shiboken6 (Qt for Python) | LGPL-3.0-only | https://code.qt.io/cgit/pyside/pyside-setup.git/ · https://pypi.org/project/PySide6/ |
| CPython (the bundled interpreter) | PSF-2.0 | https://www.python.org/downloads/source/ |
| PyInstaller bootloader | GPL-2.0-or-later with the PyInstaller bootloader exception (permits distribution with any program) | https://github.com/pyinstaller/pyinstaller |

The exact versions are printed by `kiosk --version`. The Linux arm64 bundle may also contain
`libGL.so.1`, `libGLX.so.0` and `libGLdispatch.so.0` from libglvnd (MIT-style license,
https://gitlab.freedesktop.org/glvnd/libglvnd).

## Your rights under the LGPL

The LGPL components are shipped as separate shared libraries in the bundle folder, unmodified
from the upstream PySide6 wheels on PyPI. You may replace them with your own modified builds of the
same versions. The corresponding source code is available at the links above; on request, the
maintainers will also point you to the exact source revision used for a given release.

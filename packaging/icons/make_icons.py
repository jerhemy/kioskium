"""Render the app icon from docs/images/kioskium-icon.svg.

    python packaging/icons/make_icons.py

Writes
    packaging/windows/kioskium.ico         Windows executable icon (16-256 px, PNG-compressed entries)
    src/kioskium/assets/kioskium-icon.png  window / taskbar icon used at runtime (256 px)

The artwork is not square (1000x760), so it is centred on a transparent square canvas. Run it again
whenever the SVG changes and commit the results; the builds use the generated files, not the SVG.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs" / "images" / "kioskium-icon.svg"
ICO = ROOT / "packaging" / "windows" / "kioskium.ico"
PNG = ROOT / "src" / "kioskium" / "assets" / "kioskium-icon.png"
ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
MARGIN = 0.04  # share of the canvas left empty on each side


def render(renderer: QSvgRenderer, size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    view = renderer.viewBoxF()
    box = size * (1 - 2 * MARGIN)
    scale = box / max(view.width(), view.height())
    w, h = view.width() * scale, view.height() * scale
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    renderer.render(painter, QRectF((size - w) / 2, (size - h) / 2, w, h))
    painter.end()
    return img


def png_bytes(img: QImage) -> bytes:
    data = QByteArray()
    buf = QBuffer(data)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    return bytes(data)


def write_ico(path: Path, images: list[QImage]) -> None:
    """ICO container with PNG-compressed entries (supported since Windows Vista)."""
    blobs = [png_bytes(img) for img in images]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = len(header) + 16 * len(blobs)
    entries = b""
    for img, blob in zip(images, blobs):
        dim = img.width() if img.width() < 256 else 0  # 0 means 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> int:
    app = QGuiApplication(sys.argv)  # noqa: F841 - QPainter needs a GUI application
    renderer = QSvgRenderer(str(SOURCE))
    if not renderer.isValid():
        print(f"cannot read {SOURCE}", file=sys.stderr)
        return 1
    ICO.parent.mkdir(parents=True, exist_ok=True)
    PNG.parent.mkdir(parents=True, exist_ok=True)
    write_ico(ICO, [render(renderer, s) for s in ICO_SIZES])
    render(renderer, 256).save(str(PNG), "PNG")
    print(f"wrote {ICO.relative_to(ROOT)} ({', '.join(map(str, ICO_SIZES))} px) and {PNG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

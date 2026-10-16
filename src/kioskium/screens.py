"""Screen enumeration that works without QtWebEngine (used by --list-screens and the browser)."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication


def _rect_str(r) -> str:
    return f"{r.width()}x{r.height()}+{r.x()}+{r.y()}"


def describe_screens() -> list[str]:
    lines = []
    primary = QGuiApplication.primaryScreen()
    for i, s in enumerate(QGuiApplication.screens()):
        tag = " (primary)" if s is primary else ""
        lines.append(
            f"[{i}] {s.name()}{tag} {_rect_str(s.geometry())} dpr={s.devicePixelRatio():g} "
            f"{s.manufacturer()} {s.model()} {s.serialNumber()}".rstrip()
        )
    return lines

"""Pages built into Kioskium, addressed with ``kioskium:NAME`` wherever a URL is accepted
(command line, ``kioskctl navigate``, the control API, ``split``):

    kioskium:idle    the loading screen with nothing to load: waits until a URL is sent
                     (what the browser shows when started without a URL)
    kioskium:test    diagnostics page: GPU / WebGL, frame rate, display, kiosk lock-down checks
    kioskium:input   form fields and the input events they produce

The HTML files ship inside the package (``kioskium/pages``), so they work offline and in the bundles.
"""

from __future__ import annotations

from pathlib import Path

SCHEME = "kioskium:"
IDLE = "kioskium:idle"
PAGES_DIR = Path(__file__).with_name("pages")
FILES = {"test": "test-page.html", "input": "input-page.html"}
NAMES = ("idle", *FILES)


def builtin_name(text: str) -> str | None:
    """``"test"`` for ``"kioskium:test"`` (any case, optional slashes), None for ordinary URLs.
    Raises ValueError for an unknown built-in page."""
    t = (text or "").strip()
    if not t.lower().startswith(SCHEME):
        return None
    name = t[len(SCHEME):].strip("/").lower()
    if name not in NAMES:
        raise ValueError(f"unknown built-in page {t!r}; available: " + ", ".join(SCHEME + n for n in NAMES))
    return name


def builtin_path(name: str) -> Path:
    return PAGES_DIR / FILES[name]


def resolve_url(text: str, cwd: str):
    """QUrl for a command-line / API URL: built-in pages, the idle screen for an empty value, else
    ``QUrl.fromUserInput`` (so plain host names and local file paths keep working)."""
    from PySide6.QtCore import QUrl

    name = builtin_name(text) if (text or "").strip() else "idle"
    if name == "idle":
        return QUrl(IDLE)
    if name:
        return QUrl.fromLocalFile(str(builtin_path(name)))
    return QUrl.fromUserInput(str(text), cwd)


def is_idle(url) -> bool:
    return url.toString() == IDLE

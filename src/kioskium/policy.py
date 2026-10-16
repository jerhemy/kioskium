"""Navigation policy helpers (pure Python, no Qt dependency so they are unit-testable)."""

from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlsplit

# Schemes that are never subject to the host allow-list.
_UNRESTRICTED_SCHEMES = {"about", "data", "blob", "chrome", "qrc", "file", "javascript", "chrome-error", "chrome-extension"}


def normalize_host_pattern(pattern: str) -> str:
    """Accept either a bare host pattern ('*.example.com') or a full URL and return a host pattern."""
    pattern = pattern.strip().lower()
    if "://" in pattern:
        host = urlsplit(pattern).hostname or ""
        return host
    # strip any accidental port / path
    return pattern.split("/")[0].split(":")[0]


def host_allowed(url: str, patterns: list[str]) -> bool:
    """Return True if the main-frame navigation to ``url`` is allowed by ``patterns``.

    An empty pattern list allows everything. Patterns are fnmatch-style host globs
    ('example.com', '*.example.com', '10.0.*'). Non-network schemes are always allowed.
    """
    if not patterns:
        return True
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme in _UNRESTRICTED_SCHEMES:
        return True
    host = (parts.hostname or "").lower()
    if not host:
        return False
    for raw in patterns:
        pat = normalize_host_pattern(raw)
        if pat and fnmatch.fnmatchcase(host, pat):
            return True
    return False


_GEOMETRY_RE = re.compile(r"^\s*(\d+)x(\d+)(?:([+-]\d+)([+-]\d+))?\s*$")


def parse_geometry(spec: str) -> tuple[int, int, int | None, int | None]:
    """Parse 'WxH' or 'WxH+X+Y' (X/Y may be negative, e.g. 1920x1080-1920+0)."""
    m = _GEOMETRY_RE.match(spec or "")
    if not m:
        raise ValueError(f"invalid geometry '{spec}', expected WxH or WxH+X+Y")
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise ValueError(f"invalid geometry '{spec}', size must be positive")
    if m.group(3) is None:
        return w, h, None, None
    return w, h, int(m.group(3)), int(m.group(4))

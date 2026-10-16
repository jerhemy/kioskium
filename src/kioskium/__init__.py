"""Kioskium: a kiosk-mode web browser built on PySide6 / Qt WebEngine."""

try:
    from kioskium._version import __version__  # generated from the git tags (setuptools-scm)
except ImportError:  # running from a checkout that was never installed
    __version__ = "0.0.0"

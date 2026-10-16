"""Process entry points: ``kiosk`` (the browser) and ``kioskctl`` (its control client)."""

from __future__ import annotations

import sys


def _report_crash(exc: BaseException) -> None:
    """Never leave a kiosk stuck on a modal 'unhandled exception' box: log and exit instead."""
    import logging
    import os
    import traceback
    from pathlib import Path

    text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    logging.getLogger("kioskium").critical("fatal error:\n%s", text)
    if sys.stderr is None:  # windowed build, no console
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME") or str(Path.home())
        try:
            path = Path(base) / "kioskium" / "crash.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass


def kiosk() -> None:
    """The kiosk browser (``kiosk ctl ...`` also reaches the control client)."""
    try:
        from kioskium.app import main

        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 - last line of defence for an unattended kiosk
        _report_crash(exc)
        sys.exit(1)


def kioskctl() -> None:
    """The control client for running instances."""
    from kioskium.control import ctl_main

    sys.exit(ctl_main(sys.argv[1:]))

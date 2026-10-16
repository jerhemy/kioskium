"""Application entry point: environment setup, QApplication creation and main loop."""

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path

from kioskium.cli import parse_args, version_string

log = logging.getLogger("kioskium")

# Chromium flags that are always applied for kiosk use.
_BASE_CHROMIUM_FLAGS = [
    "--autoplay-policy=no-user-gesture-required",
]
# Hardware acceleration: these keep the GPU path on even when Chromium's blocklist
# would otherwise fall back to software (embedded GPUs are frequently blocklisted).
_GPU_CHROMIUM_FLAGS = [
    "--ignore-gpu-blocklist",
    "--enable-zero-copy",
    "--enable-accelerated-2d-canvas",
]
_NO_GPU_CHROMIUM_FLAGS = ["--disable-gpu"]


def setup_logging(args) -> None:
    level = getattr(logging, args.log_level.upper(), logging.INFO)
    fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    log_file = args.log_file
    if log_file is None and sys.stderr is None:
        # Windowed (no console) build: keep a log file so problems are diagnosable.
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_STATE_HOME") or str(Path.home())
        log_file = str(Path(base) / "kioskium" / "kioskium.log")
    from logging.handlers import RotatingFileHandler

    max_bytes = max(1, int(getattr(args, "log_max_size", 5))) * 1024 * 1024
    backups = max(0, int(getattr(args, "log_backups", 3)))
    if log_file:
        Path(log_file).expanduser().parent.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(Path(log_file).expanduser(), maxBytes=max_bytes, backupCount=backups, encoding="utf-8"))
    logging.basicConfig(level=level, format=fmt, handlers=handlers or [logging.NullHandler()])
    page_log = getattr(args, "page_log", None)
    if page_log:
        # Dedicated rotating file for everything the page reports (console, load failures, crashes).
        Path(page_log).expanduser().parent.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(Path(page_log).expanduser(), maxBytes=max_bytes, backupCount=backups, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
        h.setLevel(logging.DEBUG)
        page_logger = logging.getLogger("kioskium.page")
        page_logger.setLevel(logging.DEBUG)
        page_logger.addHandler(h)


def _setenv_default(name: str, value: str) -> None:
    if not os.environ.get(name):
        os.environ[name] = value


def configure_environment(args) -> list[str]:
    """Set Qt/Chromium environment variables. Must run before QApplication is created.

    Returns the argv list to hand to QApplication.
    """
    flags: list[str] = []
    existing = os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "").strip()
    if existing:
        flags.append(existing)
    flags += _BASE_CHROMIUM_FLAGS
    flags += _NO_GPU_CHROMIUM_FLAGS if args.no_gpu else _GPU_CHROMIUM_FLAGS
    if not args.no_gpu:
        # Off by default: GPU tile rasterization exhausts small GPU memory pools (Vivante on i.MX: black,
        # flickering tiles once several browsers share it). Tiles are then drawn by the CPU, still composited on the GPU.
        flags.append("--enable-gpu-rasterization" if args.gpu_rasterization else "--disable-gpu-rasterization")
    # explicit both ways: Chromium's own default turns touch on whenever a touchscreen is present
    flags.append("--touch-events=enabled" if args.touch else "--touch-events=disabled")
    flags.append("--disable-pinch")  # pinch zoom is never allowed: it would let users zoom the kiosk out of its layout
    flags += args.chromium_flag
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(flags)

    if args.no_sandbox:
        os.environ["QTWEBENGINE_DISABLE_SANDBOX"] = "1"
    if args.remote_debugging_port:
        os.environ["QTWEBENGINE_REMOTE_DEBUGGING"] = str(args.remote_debugging_port)

    if sys.platform.startswith("win"):
        # Launchers written for Linux devices often set QT_QPA_PLATFORM=wayland unconditionally;
        # a Linux-only platform plugin can never load on Windows, so ignore it instead of failing.
        qpa = os.environ.get("QT_QPA_PLATFORM", "")
        if qpa and qpa.split(":")[0] not in ("windows", "offscreen", "minimal", "direct2d"):
            log.info("ignoring QT_QPA_PLATFORM=%s on Windows", qpa)
            del os.environ["QT_QPA_PLATFORM"]
    if sys.platform.startswith("linux"):
        if not args.platform and not os.environ.get("QT_QPA_PLATFORM") and os.environ.get("WAYLAND_DISPLAY"):
            os.environ["QT_QPA_PLATFORM"] = "wayland"
        if args.kiosk:
            # Our window is frameless anyway; make sure Qt never draws client-side decorations.
            _setenv_default("QT_WAYLAND_DISABLE_WINDOWDECORATION", "1")
        if not args.no_gpu:
            # Qt WebEngine shares Qt's rendering context; keep the Qt scene graph on OpenGL(ES)
            # so the Chromium compositor output is uploaded as GPU textures, not copied through RAM.
            _setenv_default("QSG_RHI_BACKEND", "opengl")

    _configure_frozen_paths()

    qt_argv = [sys.argv[0] if sys.argv else "kioskium"]
    if args.platform:
        qt_argv += ["-platform", args.platform]
    qt_argv += args.qt_arg
    return qt_argv


def _emit(text: str) -> None:
    """Print to stdout, or to the log when there is no console (windowed Windows build)."""
    if sys.stdout is not None:
        print(text)
    else:
        for line in text.splitlines():
            log.info(line)


def _configure_frozen_paths() -> None:
    """In a PyInstaller bundle, tell QtWebEngineProcess where the Chromium resources live.

    The subprocess locates its .pak/ICU files relative to the Qt prefix, which does not exist in a
    frozen layout, so it aborts ("The following paths were searched for Qt WebEngine resources").
    """
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return
    root = Path(base) / "PySide6"
    for sub in ("Qt", "."):
        res = root / sub / "resources"
        loc = root / sub / "translations" / "qtwebengine_locales"
        if res.is_dir():
            _setenv_default("QTWEBENGINE_RESOURCES_PATH", str(res.resolve()))
            if loc.is_dir():
                _setenv_default("QTWEBENGINE_LOCALES_PATH", str(loc.resolve()))
            log.debug("frozen bundle: QtWebEngine resources at %s", res)
            return


# Options that pick the browser (or this launch's own behaviour) rather than configure it: not "ignored" on hand-over.
_HANDOVER_QUIET = {"name", "app_id", "config", "no_config", "control_token", "log_level", "log_file"}


def _hand_over(args, argv: list[str]) -> int | None:
    """A browser with this name already runs: give it this launch's URL and return 0, or 3 when that is
    not possible or --fail-if-running is set. None when no browser of that name is running."""
    from kioskium.control import list_instances, request
    from kioskium.daemon import option_dest
    from kioskium.pages import IDLE

    inst = next((i for i in list_instances(ping=True, timeout=2.0) if i.get("name") == args.name and not i.get("pruned")), None)
    if not inst:
        return None
    name, pid = args.name, inst.get("pid")
    if not inst.get("running"):  # the process lives but its API did not answer: busy, never start a second one over it
        log.error("%s is already running (pid %s) but its control channel did not answer; try again", name, pid)
        return 3
    if args.fail_if_running:
        log.error("a browser named %s is already running (pid %s)", name, pid)
        return 3
    if args.url == IDLE:  # parse_args turns 'no URL' into kioskium:idle
        log.info("%s is already running (pid %s); no URL given, so it is left as it is", name, pid)
        return 0
    if not (inst.get("port") or inst.get("socket")):
        log.error("%s is already running (pid %s) without a control channel (--no-control), so it cannot be given %s",
                  name, pid, args.url)
        return 3
    ignored = sorted({t.split("=", 1)[0] for t in argv if option_dest(t) and option_dest(t) not in _HANDOVER_QUIET})
    if ignored:
        log.warning("%s is already running and keeps its own settings; ignored (they only apply when a browser starts): %s",
                    name, ", ".join(ignored))
    try:
        reply = request("POST", "/api/navigate", {"url": args.url}, port=inst.get("port"), socket_path=inst.get("socket"),
                        token=inst.get("token") or args.control_token, timeout=5)
    except OSError as exc:
        log.error("%s is already running (pid %s) but did not answer: %s", name, pid, exc)
        return 3
    if not reply.get("ok"):
        log.error("%s is already running (pid %s) and refused %s: %s", name, pid, args.url, reply.get("error"))
        return 3
    log.info("%s is already running (pid %s): it now shows %s", name, pid, args.url)
    return 0


def _default_control_channel(args) -> None:
    """Every browser can be reached unless --no-control: a private socket (or a free loopback port on
    Windows) with a random token, both recorded in the registry."""
    import secrets

    from kioskium.control import default_socket_path

    if args.no_control or args.control_port is not None or args.control_socket:
        return
    if sys.platform.startswith("win"):
        args.control_port = 0
    else:
        args.control_socket = default_socket_path(args.name)
    if not args.control_token:
        args.control_token = secrets.token_urlsafe(24)


def _install_signal_handlers(app, window) -> object:
    from PySide6.QtCore import QTimer

    def _quit(signum, _frame):
        log.info("received signal %s, quitting", signum)
        window._quit()

    def _reload(signum, _frame):
        log.info("received signal %s, reloading", signum)
        window.reload()

    signal.signal(signal.SIGINT, _quit)
    signal.signal(signal.SIGTERM, _quit)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _reload)
    # Python signal handlers only run on the main thread between bytecodes; wake the
    # interpreter periodically so a signal is handled while the Qt event loop is blocking.
    tick = QTimer()
    tick.setInterval(250)
    tick.timeout.connect(lambda: None)
    tick.start()
    return tick


ICON_PATH = Path(__file__).with_name("assets") / "kioskium-icon.png"  # generated by packaging/icons/make_icons.py


def _set_window_icon(app) -> None:
    """Title bar / taskbar icon (Windows, X11). Wayland compositors take it from the app-id instead."""
    from PySide6.QtGui import QIcon

    if ICON_PATH.is_file():
        app.setWindowIcon(QIcon(str(ICON_PATH)))


def resolve_opengl(choice: str, platform_name: str = sys.platform, machine: str | None = None) -> str:
    """'auto' becomes 'es' on Linux arm boards: their GPUs (Vivante, Mali, Adreno, VideoCore) only
    provide OpenGL ES, and a desktop-GL context request fails (EGL 3004) before anything renders.
    Everything else keeps Qt's default; --opengl desktop|es always wins."""
    if choice != "auto":
        return choice
    if machine is None:
        import platform

        machine = platform.machine()
    if platform_name.startswith("linux") and machine.lower() in ("aarch64", "arm64", "armv7l", "armv8l"):
        return "es"
    return choice


def main(argv: list[str] | None = None) -> int:
    raw = sys.argv[1:] if argv is None else argv
    if raw and raw[0] == "ctl":
        from kioskium.control import ctl_main

        return ctl_main(raw[1:])

    args = parse_args(argv)
    setup_logging(args)

    if args.version:
        _emit(version_string())
        return 0
    if args.print_config:
        import json

        from kioskium.cli import effective_settings

        _emit(json.dumps(effective_settings(args), indent=2))
        return 0
    if args.config_file:
        log.info("settings from %s (command line overrides)", args.config_file)
    resolved = resolve_opengl(args.opengl)
    if resolved != args.opengl:
        log.info("--opengl auto -> %s (Linux %s)", resolved, __import__("platform").machine())
        args.opengl = resolved
    if args.list_screens:
        # Only the platform plugin is needed: no QtWebEngine import, no shared GL context, so this
        # works on GLES-only boards without --opengl es.
        from PySide6.QtGui import QGuiApplication

        from kioskium.screens import describe_screens

        app = QGuiApplication(configure_environment(args))  # noqa: F841 - must outlive describe_screens
        _emit("\n".join(describe_screens()))
        return 0
    if args.list_app_ids:
        from kioskium.control import WESTON_INI_CANDIDATES, format_app_ids, list_instances, read_weston_app_ids

        running = {i["appId"]: i for i in list_instances(ping=True, timeout=2.0) if i.get("running")}
        paths = ([args.weston_ini] if args.weston_ini else []) + list(WESTON_INI_CANDIDATES)
        _emit(format_app_ids(read_weston_app_ids(paths), running, searched=paths))
        return 0

    rc = _hand_over(args, raw)
    if rc is not None:
        return rc
    _default_control_channel(args)

    qt_argv = configure_environment(args)

    from PySide6.QtCore import QCoreApplication, Qt
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from kioskium.browser import KioskWindow
    from kioskium.screens import describe_screens

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    if args.opengl != "auto":
        from PySide6.QtGui import QSurfaceFormat

        fmt = QSurfaceFormat.defaultFormat()
        fmt.setRenderableType(
            QSurfaceFormat.RenderableType.OpenGLES if args.opengl == "es" else QSurfaceFormat.RenderableType.OpenGL
        )
        # Ask for an explicit 8-bit RGBA window surface. Embedded compositors that blit client
        # buffers with a 2D engine (i.MX Weston with use-g2d=1) only accept ARGB8888/XRGB8888;
        # the EGL config Qt picks by default on such drivers can be a format the blitter
        # misinterprets, which shows up as wrong colours.
        if not args.no_rgba_surface:
            fmt.setRedBufferSize(8)
            fmt.setGreenBufferSize(8)
            fmt.setBlueBufferSize(8)
            fmt.setAlphaBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
        log.info("requesting OpenGL %s contexts%s", args.opengl, "" if args.no_rgba_surface else " with RGBA8888 surfaces")
    QCoreApplication.setApplicationName(args.app_id)
    QCoreApplication.setOrganizationName("kioskium")
    QGuiApplication.setDesktopFileName(args.app_id)  # -> Wayland xdg_toplevel app_id

    app = QApplication(qt_argv)
    app.setApplicationDisplayName("Kioskium")
    _set_window_icon(app)

    screens = describe_screens()
    log.info(version_string().replace("\n", " | "))
    log.info("platform=%s app_id=%s name=%s kiosk=%s", app.platformName(), args.app_id, args.name, args.kiosk)
    log.info("QTWEBENGINE_CHROMIUM_FLAGS=%s", os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS"))
    for line in screens:
        log.info("screen %s", line)

    window = KioskWindow(args)
    _tick = _install_signal_handlers(app, window)  # noqa: F841 - keep alive
    from kioskium.control import ControlServer, register_instance, unregister_instance

    port = None
    if args.control_port is not None or args.control_socket:
        window.events = ControlServer(window, args)
        port = window.events.port
    register_instance(args.name, port, args.control_socket, args.url, args.app_id, args.control_token)
    app.aboutToQuit.connect(lambda: unregister_instance(args.name))
    window.start()
    rc = app.exec()
    try:
        window.shutdown()
    except Exception:  # noqa: BLE001 - never turn a clean exit into a crash
        log.exception("error while shutting down pages")
    log.info("exiting with code %s", rc)
    return rc


if __name__ == "__main__":
    sys.exit(main())

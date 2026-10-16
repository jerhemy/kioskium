"""Command line interface for kioskium.

Defaults can come from a JSON config file (``--config FILE``, env ``KIOSKIUM_CONFIG``, or a
``kioskium.json`` next to the executable / in the working directory): keys are the long option
names (``"show-cursor-timeout": 3``, ``"log-level": "debug"``, ``"url": "http://..."``). Command line
arguments override the file. Arguments can also be read from a file, one argument per line, by
passing ``@/path/to/file``. Lines starting with '#' are ignored.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from kioskium import __version__
from kioskium.pages import IDLE, builtin_name

DEFAULT_APP_ID = "kioskium"
DEFAULT_URL = IDLE  # no URL: show the loading screen until one is sent (kioskctl navigate)
DEFAULT_MEMORY_CACHE_MB = 32  # off-the-record profile: the HTTP cache lives in RAM
DEFAULT_DISK_CACHE_MB = 100   # --profile-dir: keep flash usage bounded on embedded devices
CONFIG_FILE_NAME = "kioskium.json"

LOG_LEVELS = ("debug", "info", "warning", "error")

# Settings that only make sense on the command line, never in a config file.
_NOT_CONFIGURABLE = {"config", "no_config", "print_config", "version", "list_screens", "list_app_ids", "help"}
_DERIVED = {"kiosk", "config_file", "hide_cursor", "cursor_timeout", "touch", "no_sandbox"}  # computed in parse_args, not settings


def cache_size_mb(args) -> int:
    """HTTP cache limit in MB: --cache-size if given (0 = Chromium's own default), else a default per cache type."""
    size = getattr(args, "cache_size", None)
    if size is not None:
        return max(0, int(size))
    return DEFAULT_DISK_CACHE_MB if getattr(args, "profile_dir", None) else DEFAULT_MEMORY_CACHE_MB


class _ArgumentParser(argparse.ArgumentParser):
    def convert_arg_line_to_args(self, arg_line: str):
        line = arg_line.strip()
        if not line or line.startswith("#"):
            return []
        return [line]


def build_parser() -> argparse.ArgumentParser:
    p = _ArgumentParser(
        prog="kiosk",
        fromfile_prefix_chars="@",
        description="Kioskium: kiosk-mode web browser (PySide6 / Qt WebEngine, Chromium backend).",
        epilog=(
            "Arguments may be read from a file with @FILE (one argument per line, '#' comments allowed), "
            "e.g. 'kiosk @/etc/kioskium.conf'. Options whose value starts with '-' "
            "must use the '=' form, e.g. --qt-arg=-plugin --qt-arg=evdevtouch."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "url",
        nargs="?",
        default=os.environ.get("KIOSKIUM_URL", DEFAULT_URL),
        help="URL, local file, or built-in page (kioskium:test, kioskium:input, kioskium:idle) to open. Without one, the loading screen waits until a URL is sent. Env: KIOSKIUM_URL",
    )
    p.add_argument("--version", action="store_true", help="print application, Qt and Chromium versions and exit")
    p.add_argument("--config", metavar="FILE",
                   help=f"JSON file with default settings (keys = long option names, e.g. {{\"show-cursor-timeout\": 3, \"log-level\": \"debug\"}}); "
                        f"command line arguments override it. Default: env KIOSKIUM_CONFIG, else {CONFIG_FILE_NAME} next to the "
                        "executable or in the working directory if present")
    p.add_argument("--no-config", action="store_true", help="ignore any default config file")
    p.add_argument("--print-config", action="store_true",
                   help="print the effective settings as JSON (config file + command line) and exit; paste into a config file")

    g = p.add_argument_group("window")
    g.add_argument("--windowed", action="store_true", help="run in a normal decorated window instead of kiosk mode")
    g.add_argument("--size", metavar="WxH[+X+Y]",
                   help="window size with --windowed, optionally with a position (e.g. 1280x800 or 1280x800+0+0)")
    g.add_argument(
        "--screen",
        metavar="NAME|INDEX",
        help="screen to show on: an index from --list-screens or a (partial, case-insensitive) screen name such as HDMI-A-1",
    )
    g.add_argument("--list-screens", action="store_true", help="list the screens Qt sees and exit")
    g.add_argument("--list-app-ids", action="store_true", help="list the app-ids the Wayland compositor maps to outputs (from weston.ini) and which are running, then exit")
    g.add_argument("--weston-ini", metavar="PATH", default=os.environ.get("KIOSKIUM_WESTON_INI"),
                   help="weston.ini to read app-id/output mappings from (default: XDG_CONFIG_HOME, ~/.config, /etc/xdg). Env: KIOSKIUM_WESTON_INI")
    g.add_argument(
        "--app-id",
        default=os.environ.get("KIOSKIUM_APP_ID", DEFAULT_APP_ID),
        help="Wayland xdg app_id for the window (QGuiApplication.desktopFileName): compositors such as Weston map it to an "
             "output (weston.ini app-ids=...). Env: KIOSKIUM_APP_ID",
    )
    g.add_argument("--name", metavar="NAME",
                   help="instance name: what kioskctl and the registry call this browser (kioskctl --name NAME ...). Also used "
                        "for per-instance files: replaces {name} in --log-file, --page-log, --profile-dir and --screenshot, or is "
                        "inserted before the extension when the path has no {name} (kioskium.log -> kioskium-NAME.log). "
                        "Default: the app-id (then only {name} is substituted)")
    g.add_argument("--show-cursor", action="store_true",
                   help="show the mouse pointer (in kiosk mode it is hidden by default; --windowed always shows it)")
    g.add_argument("--show-cursor-timeout", type=float, default=0.0, metavar="SEC",
                   help="show the pointer while a mouse is being moved (e.g. over remote desktop) and hide it again after SEC "
                        "seconds without movement; touch taps never show it")
    g.add_argument("--text-selection", action="store_true", help="allow text selection and the long-press selection handles (off by default; form fields always editable)")
    g.add_argument("--context-menu", action="store_true", help="allow the right-click/long-press context menu (off by default in every mode)")
    g.add_argument("--background", default="#000000", metavar="COLOR", help="window background colour shown before/behind the page")

    g = p.add_argument_group("split screen")
    g.add_argument("--split", metavar="URL2", help="show a second page next to URL in the same window (each pane has its own loading screen, retries, DevTools and JS API)")
    g.add_argument("--split-orientation", choices=("horizontal", "vertical"), default="horizontal", help="horizontal = side by side, vertical = stacked")
    g.add_argument("--split-ratio", type=int, default=50, metavar="PERCENT", help="share of the window given to the first page")
    g.add_argument("--split-gap", type=int, default=0, metavar="PX", help="width of the divider between the panes")

    g = p.add_argument_group("platform")
    g.add_argument("--platform", metavar="QPA", help="Qt platform plugin: wayland, xcb, eglfs, windows, offscreen, ... (default: Qt's choice)")
    g.add_argument("--qt-arg", action="append", default=[], metavar="ARG", help="extra argument passed to QApplication (repeatable)")
    g.add_argument("--chromium-flag", action="append", default=[], metavar="FLAG", help="extra Chromium command line flag (repeatable)")
    g.add_argument("--no-gpu", action="store_true", help="disable GPU/hardware acceleration (software rendering)")
    g.add_argument("--gpu-rasterization", action="store_true",
                   help="draw page tiles on the GPU (off by default: on GPUs with a small memory pool, e.g. i.MX, "
                   "it causes black, flickering tiles; compositing is on the GPU either way)")
    g.add_argument(
        "--opengl",
        choices=("auto", "es", "desktop"),
        default="auto",
        help="OpenGL flavour Qt requests for its context: 'es' for embedded GPUs that only provide OpenGL ES/EGL "
        "(Vivante, Mali, Adreno), 'desktop' for full OpenGL, 'auto' (default) = 'es' on Linux arm boards, Qt's default elsewhere",
    )
    g.add_argument("--no-rgba-surface", action="store_true", help="with --opengl: do not force an 8-bit RGBA window surface (diagnostic)")
    g.add_argument("--sandbox", action="store_true",
                   help="enable the Chromium renderer sandbox (off by default: it refuses to run as root and needs "
                   "user namespaces, which embedded kernels and services often lack)")
    g.add_argument("--no-sandbox", action="store_true", help=argparse.SUPPRESS)  # the default now; still accepted
    g.add_argument("--no-touch", action="store_true",
                   help="disable touch events in Chromium (on by default); touch then arrives as mouse events")
    g.add_argument("--remote-debugging-port", type=int, metavar="PORT", help="enable Chromium DevTools remote debugging on PORT")

    g = p.add_argument_group("content")
    g.add_argument("--profile-dir", metavar="PATH", help="persistent profile directory (cookies, local storage, cache). Default: in-memory, off-the-record")
    g.add_argument("--cache-size", type=int, default=None, metavar="MB",
                   help="maximum HTTP cache size in MB (default: 32 for the in-memory cache, 100 with --profile-dir; "
                   "0 = let Chromium decide)")
    g.add_argument("--user-agent", metavar="UA", help="override the HTTP User-Agent string")
    g.add_argument("--ignore-certificate-errors", action="store_true", help="accept invalid/self-signed TLS certificates")
    g.add_argument(
        "--allow-host",
        action="append",
        default=[],
        metavar="PATTERN",
        help="restrict main-frame navigation to hosts matching PATTERN (fnmatch glob, repeatable). The start URL's host is always allowed",
    )
    g.add_argument("--allow-new-windows", action="store_true", help="let window.open()/target=_blank open a new kiosk window (default: open in the same view)")
    g.add_argument("--download-dir", metavar="DIR", help="allow downloads into DIR (default: downloads are cancelled)")
    g.add_argument("--allow-dialogs", action="store_true", help="show JavaScript alert/confirm/prompt dialogs (default: auto-dismissed and logged)")
    g.add_argument("--grant-permissions", action="store_true", help="auto-grant page permission requests (camera, microphone, geolocation, ...)")
    g.add_argument("--show-scrollbars", action="store_true", help="show page scrollbars (hidden by default; pages still scroll)")
    g.add_argument("--allow-file-remote-access", action="store_true", help="let file:// pages access remote URLs")
    g.add_argument("--inject-js", action="append", default=[], metavar="FILE", help="JavaScript file injected into every page at document creation (repeatable)")
    g.add_argument("--inject-css", action="append", default=[], metavar="FILE", help="CSS file injected into every page (repeatable)")

    g = p.add_argument_group("loading screen")
    g.add_argument("--loading-page", metavar="FILE", help="custom HTML shown while the site is unreachable (default: built-in screen)")
    g.add_argument("--loading-title", default="Loading…", help="title text of the built-in loading screen")
    g.add_argument("--loading-detail", default="", help="line of text under the bar on the built-in loading screen (default: none)")
    g.add_argument("--loading-status", action="store_true",
                   help="built-in loading screen: also show the retry countdown, the last load error and crash recovery (default: title and bar only)")

    g = p.add_argument_group("control channel")
    g.add_argument("--control-port", type=int, metavar="PORT",
                   help="serve the HTTP/JSON control API on 127.0.0.1:PORT instead of the default channel (see `kioskctl --help`); "
                        "0 = any free port. Without --control-token, a fixed port needs no token")
    g.add_argument("--control-socket", metavar="PATH", help="serve the control API on this Unix socket instead of the default channel")
    g.add_argument("--control-token", metavar="TOKEN", help="require this token (X-Kioskium-Token header) for control requests")
    g.add_argument("--no-control", action="store_true",
                   help="no control channel. By default every browser gets one (a Unix socket in the runtime directory on "
                        "Linux, a free 127.0.0.1 port on Windows, with a random token), recorded in the registry so "
                        "`kioskctl -n NAME` and a second launch with the same name can reach it")
    g.add_argument("--fail-if-running", action="store_true",
                   help="exit with code 3 if a browser with this name is already running. Default: hand it this launch's "
                        "URL (it navigates there) and exit 0")

    g = p.add_argument_group("resilience")
    g.add_argument("--no-reload-on-crash", action="store_true", help="do not reload the page when the render process dies")
    g.add_argument("--retry-interval", type=float, default=5.0, metavar="SEC", help="base delay before retrying a failed page load (exponential backoff, max 60s)")
    g.add_argument("--reload-interval", type=float, default=0.0, metavar="SEC", help="reload the page periodically (0 = never)")
    g.add_argument("--load-timeout", type=float, default=30.0, metavar="SEC",
                   help="abort and retry a page load that takes longer than SEC, so a server that accepts the connection "
                        "but never answers does not leave the loading screen up forever (0 = no timeout)")

    g = p.add_argument_group("control")
    g.add_argument("--exit-shortcut", metavar="KEYSEQ", help="key sequence that quits the browser, e.g. 'Ctrl+Shift+Q' (default: none in kiosk mode)")
    g.add_argument("--allow-shortcuts", action="store_true", help="do not block browser navigation keys/mouse buttons in kiosk mode")
    g.add_argument("--exit-after", type=float, metavar="SEC", help="quit after SEC seconds (for automated tests)")
    g.add_argument("--screenshot", metavar="FILE", help="save a screenshot of the page after it loads (PNG)")
    g.add_argument("--screenshot-delay", type=float, default=0.0, metavar="SEC", help="also take the screenshot SEC seconds after start")
    g.add_argument("--log-level", default="info", choices=LOG_LEVELS, help="log verbosity")
    g.add_argument("--log-file", metavar="FILE", help="also write log output to FILE (rotating, see --log-max-size/--log-backups)")
    g.add_argument("--page-log", metavar="FILE", help="rotating log of the page's JavaScript console (log/warn/error with source:line), load failures and crashes")
    g.add_argument("--log-max-size", type=int, default=5, metavar="MB", help="rotate --log-file/--page-log when they reach this size")
    g.add_argument("--log-backups", type=int, default=3, metavar="N", help="rotated files to keep")
    g.add_argument("--page-console", action="store_true", help="log the page's JavaScript console messages at INFO level (always logged at DEBUG)")
    g.add_argument("--fps", action="store_true",
                   help="show a frame-rate overlay in every page (one per pane; each pane is its own renderer). Also at runtime: kioskctl fps on|off")
    g.add_argument("--fps-corner", default="top-right", choices=("top-left", "top-right", "bottom-left", "bottom-right"),
                   help="where the frame-rate overlay sits (default top-right)")
    g.add_argument("--devtools", action="store_true", help="enable an in-window DevTools inspector pane (works in kiosk mode), toggled with --devtools-key")
    g.add_argument("--devtools-open", action="store_true", help="start with the DevTools pane visible (implies --devtools)")
    g.add_argument("--devtools-key", default="F12", metavar="KEYSEQ", help="key sequence that toggles the DevTools pane")
    g.add_argument("--no-js-api", action="store_true", help="do not expose window.kioskium (openDevTools/reload/info) to pages")
    return p


def default_config_path() -> Path | None:
    """The config file used when --config is not given: env, then kioskium.json next to the
    executable (frozen bundle) or in the working directory. None if nothing exists."""
    env = os.environ.get("KIOSKIUM_CONFIG")
    if env:
        return Path(env)
    candidates = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / CONFIG_FILE_NAME)
    candidates.append(Path.cwd() / CONFIG_FILE_NAME)
    for c in candidates:
        if c.is_file():
            return c
    return None


class ConfigError(ValueError):
    pass


def load_config(path, parser: argparse.ArgumentParser | None = None) -> dict:
    """Read a JSON config file and return {dest: value} validated against the parser's options.

    Keys use the option name with dashes or underscores ("show-cursor" / "show_cursor"); keys
    starting with '#', '_' or '//' are comments. Values: true/false for flags, numbers or
    strings for valued options, lists for repeatable ones.
    """
    parser = parser or build_parser()
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"config {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config {path}: top level must be a JSON object")
    actions = {a.dest: a for a in parser._actions if a.dest not in _NOT_CONFIGURABLE}
    out: dict = {}
    for key, value in raw.items():
        if key.startswith(("#", "_", "//")):
            continue
        dest = key.strip().lstrip("-").replace("-", "_")
        action = actions.get(dest)
        if action is None:
            raise ConfigError(f"config {path}: unknown setting {key!r} (see --help; use the long option name without dashes)")
        out[dest] = _coerce(path, key, value, action)
    return out


def _coerce(path, key, value, action):
    where = f"config {path}: {key!r}"
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        if not isinstance(value, bool):
            raise ConfigError(f"{where} must be true or false")
        return value
    if isinstance(action, argparse._AppendAction):
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise ConfigError(f"{where} must be a string or a list of strings")
        return list(value)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ConfigError(f"{where} must be a {action.type.__name__ if action.type else 'string'}, not true/false")
    if action.type is not None:
        try:
            value = action.type(value) if isinstance(value, str) else action.type(value)
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"{where}: {exc}") from exc
    elif not isinstance(value, str):
        value = str(value)
    if action.choices is not None and value not in action.choices:
        raise ConfigError(f"{where} must be one of {', '.join(map(str, action.choices))}")
    return value


def effective_settings(args: argparse.Namespace) -> dict:
    """The parsed settings as a JSON-ready dict with config-file keys (for --print-config)."""
    skip = _NOT_CONFIGURABLE | _DERIVED
    return {k.replace("_", "-"): v for k, v in sorted(vars(args).items()) if k not in skip}


_INSTANCE_PATHS = ("log_file", "page_log", "profile_dir", "screenshot")


def instance_path(path: str | None, name: str, explicit: bool) -> str | None:
    """Per-instance file name: substitute ``{name}``; with an explicit --name and no placeholder,
    insert ``-name`` before the extension so two browsers never write the same file."""
    if not path:
        return path
    if "{name}" in path:
        return path.replace("{name}", name)
    if not explicit:
        return path
    root, ext = os.path.splitext(path)  # string-level, so separators stay as written on every platform
    return f"{root}-{name}{ext}"


def _safe_name(name: str) -> str:
    return "".join("-" if c in '/\\:*?"<>| ' else c for c in name.strip()) or "kiosk"


def browser_identity(name: str | None, app_id: str | None) -> tuple[str, list[str]]:
    """For launchers (kioskctl open, the daemon): the name a browser will register under, and the options that give it
    exactly that name and app-id. The app-id defaults to kioskium (not to a config file's), the name to the app-id.
    --name is only passed when it differs, since an explicit --name also renames per-instance files."""
    app_id = app_id or DEFAULT_APP_ID
    final = _safe_name(name or app_id)
    return final, ["--app-id", app_id] + (["--name", final] if final != _safe_name(app_id) else [])


def apply_instance_name(args: argparse.Namespace) -> None:
    explicit = args.name is not None
    args.name = _safe_name(args.name if explicit else args.app_id)
    for attr in _INSTANCE_PATHS:
        setattr(args, attr, instance_path(getattr(args, attr, None), args.name, explicit))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    # Config file first (defaults < env < config file < command line).
    pre = argparse.ArgumentParser(add_help=False, fromfile_prefix_chars="@")
    pre.add_argument("--config")
    pre.add_argument("--no-config", action="store_true")
    pre_args, _ = pre.parse_known_args(argv)
    config_path = None if pre_args.no_config else (Path(pre_args.config) if pre_args.config else default_config_path())
    if config_path is not None:
        try:
            parser.set_defaults(**load_config(config_path, parser))
        except ConfigError as exc:
            parser.error(str(exc))
    args = parser.parse_args(argv)
    args.config_file = str(config_path) if config_path else None
    apply_instance_name(args)
    args.kiosk = not args.windowed
    if args.no_control and (args.control_port is not None or args.control_socket):
        parser.error("--no-control and --control-port/--control-socket: use one")
    args.touch = not args.no_touch
    args.no_sandbox = not args.sandbox
    if args.show_cursor and args.show_cursor_timeout > 0:
        parser.error("--show-cursor and --show-cursor-timeout: use one (the timeout hides the pointer again)")
    # The pointer is hidden in kiosk mode unless --show-cursor; --show-cursor-timeout hides it between mouse moves.
    args.hide_cursor = not args.show_cursor and (args.kiosk or args.show_cursor_timeout > 0)
    args.cursor_timeout = args.show_cursor_timeout
    if args.devtools_open:
        args.devtools = True
    if not (args.url or "").strip():
        args.url = IDLE
    try:
        builtin_name(args.url)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def version_string() -> str:
    """Human readable version line. Imports Qt lazily so --help stays fast."""
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion
        from PySide6.QtWebEngineCore import (
            qWebEngineChromiumSecurityPatchVersion,
            qWebEngineChromiumVersion,
            qWebEngineVersion,
        )

        return (
            f"kioskium {__version__}\n"
            f"PySide6 {pyside_version}, Qt {qVersion()}, QtWebEngine {qWebEngineVersion()}\n"
            f"Chromium {qWebEngineChromiumVersion()} (security patches up to {qWebEngineChromiumSecurityPatchVersion()})\n"
            f"Python {sys.version.split()[0]}"
        )
    except Exception as exc:  # pragma: no cover - only when PySide6 is missing/broken
        return f"kioskium {__version__} (PySide6 unavailable: {exc})"

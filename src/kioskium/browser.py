"""Kiosk browser window, page and profile built on Qt WebEngine (PySide6)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QKeySequence, QPalette, QScreen
from PySide6.QtWebEngineCore import (
    QWebEngineDownloadRequest,
    QWebEngineLoadingInfo,
    QWebEngineNewWindowRequest,
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineSettings,
)
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter, QVBoxLayout, QWidget

from kioskium.cli import cache_size_mb
from kioskium.jsapi import install_js_api
from kioskium.loading import build_loading_page, status_script
from kioskium.pages import is_idle, resolve_url
from kioskium.policy import host_allowed, parse_geometry
from kioskium.screens import _rect_str, describe_screens  # noqa: F401 - re-exported

log = logging.getLogger("kioskium")
page_log = logging.getLogger("kioskium.page")  # page console / load failures / crashes (--page-log)
page_log.propagate = False  # the main log gets its own (filtered) copy

MAX_RETRY_DELAY_S = 60.0
# After this many consecutive failures of a sub-URL, fall back to the start URL.
FAILURES_BEFORE_HOME = 10


# --------------------------------------------------------------------------------------
# Profile
# --------------------------------------------------------------------------------------
def build_profile(cfg, parent: QObject | None = None) -> QWebEngineProfile:
    """Create the WebEngine profile: persistent (``--profile-dir``) or off-the-record."""
    if cfg.profile_dir:
        root = Path(cfg.profile_dir).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        profile = QWebEngineProfile("kioskium", parent)
        profile.setPersistentStoragePath(str(root / "storage"))
        profile.setCachePath(str(root / "cache"))
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.DiskHttpCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        log.info("persistent profile at %s", root)
    else:
        profile = QWebEngineProfile(parent)  # no storage name -> off-the-record
        profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        log.info("off-the-record profile (no persistent storage)")
    cache_mb = cache_size_mb(cfg)
    if cache_mb > 0:
        profile.setHttpCacheMaximumSize(cache_mb * 1024 * 1024)
    log.info("HTTP cache: %s, max %s", "disk" if cfg.profile_dir else "memory", f"{cache_mb} MB" if cache_mb else "Chromium default")

    if cfg.user_agent:
        profile.setHttpUserAgent(cfg.user_agent)

    if cfg.download_dir:
        Path(cfg.download_dir).expanduser().mkdir(parents=True, exist_ok=True)
        profile.setDownloadPath(str(Path(cfg.download_dir).expanduser().resolve()))

    apply_settings(profile.settings(), cfg)
    _install_injections(profile, cfg)
    return profile


def apply_settings(settings: QWebEngineSettings, cfg) -> None:
    A = QWebEngineSettings.WebAttribute
    wanted = {
        "FullScreenSupportEnabled": True,
        "PlaybackRequiresUserGesture": False,
        "JavascriptCanOpenWindows": True,  # routed through newWindowRequested
        "FocusOnNavigationEnabled": True,
        "ScreenCaptureEnabled": True,
        "ShowScrollBars": bool(cfg.show_scrollbars),
        "LocalContentCanAccessFileUrls": True,
        "LocalContentCanAccessRemoteUrls": bool(cfg.allow_file_remote_access),
        "PdfViewerEnabled": False,
        "TouchIconsEnabled": False,
        "WebGLEnabled": True,
        "Accelerated2dCanvasEnabled": True,
        # With the loading screen active, Chromium's own error page is disabled so a failed
        # navigation leaves the loading screen in place instead of replacing it.
        "ErrorPageEnabled": False,
    }
    wanted["TouchEventsApiEnabled"] = bool(cfg.touch)  # Qt >= 6.? ; absent on older builds
    for name, value in wanted.items():
        attr = getattr(A, name, None)
        if attr is None:
            log.debug("QWebEngineSettings has no attribute %s on this Qt; skipping", name)
            continue
        settings.setAttribute(attr, value)


_HIDE_CURSOR_CSS = "html, body, * { cursor: none !important; }"
_NO_SELECT_CSS = (
    "html, body, * { user-select: none !important; -webkit-user-select: none !important; "
    "-webkit-touch-callout: none !important; } "
    "input, textarea, [contenteditable] { user-select: text !important; -webkit-user-select: text !important; }"
)



_FPS_CORNERS = {
    "top-left": "top:8px;left:8px;",
    "top-right": "top:8px;right:8px;",
    "bottom-left": "bottom:8px;left:8px;",
    "bottom-right": "bottom:8px;right:8px;",
}

# Frame-rate overlay. requestAnimationFrame runs once per frame the renderer actually produces, so
# the number is this pane's real rate (capped by the display refresh), not a timer estimate. Lives in
# the main world as window.__kioskiumFps so pages and `kioskctl js` can read .fps / .min.
_FPS_JS = """
(function(){
  if (window.__kioskiumFps) { if (%(autostart)s) window.__kioskiumFps.start(); return; }
  var F = window.__kioskiumFps = {on:false, fps:0, min:0, _el:null, _raf:0, _frames:0, _t0:0, _hist:[]};
  function ensureEl(){
    if (F._el && F._el.isConnected) return;
    var e = document.createElement('div');
    e.setAttribute('data-kioskium', 'fps');
    e.style.cssText = 'position:fixed;%(corner)sz-index:2147483647;pointer-events:none;user-select:none;'
      + 'font:bold 14px/1.2 monospace;color:#fff;background:rgba(0,0,0,.65);padding:4px 8px;border-radius:4px;';
    e.textContent = '-- fps';
    (document.body || document.documentElement).appendChild(e);
    F._el = e;
  }
  function tick(t){
    if (!F.on) return;
    F._frames++;
    var dt = t - F._t0;
    if (dt >= 500) {
      F.fps = Math.round(F._frames * 1000 / dt);
      F._hist.push(F.fps); if (F._hist.length > 10) F._hist.shift();
      F.min = Math.min.apply(null, F._hist);
      F._frames = 0; F._t0 = t;
      ensureEl();
      F._el.textContent = F.fps + ' fps  (min ' + F.min + ')';
      F._el.style.color = F.fps >= 50 ? '#8f8' : (F.fps >= 30 ? '#ff8' : '#f88');
    }
    F._raf = requestAnimationFrame(tick);
  }
  F.start = function(){
    if (F.on) return;
    F.on = true; F._frames = 0; F._hist = []; F._t0 = performance.now();
    ensureEl(); F._raf = requestAnimationFrame(tick);
  };
  F.stop = function(){ F.on = false; if (F._raf) cancelAnimationFrame(F._raf); F._raf = 0; if (F._el) { F._el.remove(); F._el = null; } };
  if (%(autostart)s) F.start();
})();
"""


def _fps_script(cfg, autostart: bool) -> str:
    corner = _FPS_CORNERS.get(getattr(cfg, "fps_corner", "top-right"), _FPS_CORNERS["top-right"])
    return _FPS_JS % {"autostart": "true" if autostart else "false", "corner": corner}


def _install_fps_script(profile: QWebEngineProfile, cfg) -> None:
    """(Re)install the overlay script for every page loaded from now on; autostart follows cfg.fps."""
    scripts = profile.scripts()
    for old in scripts.find("kioskium-fps"):
        scripts.remove(old)
    s = QWebEngineScript()
    s.setName("kioskium-fps")
    s.setSourceCode(_fps_script(cfg, bool(getattr(cfg, "fps", False))))
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    s.setRunsOnSubFrames(False)
    scripts.insert(s)


def _inject_css(scripts, name: str, css: str) -> None:
    js = (
        "(function(){var s=document.createElement('style');s.setAttribute('data-kioskium',%s);"
        "s.textContent=%s;(document.head||document.documentElement).appendChild(s);})();" % (_js_string(name), _js_string(css))
    )
    s = QWebEngineScript()
    s.setName(f"kioskium-{name}")
    s.setSourceCode(js)
    s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
    s.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
    s.setRunsOnSubFrames(True)
    scripts.insert(s)


def _install_injections(profile: QWebEngineProfile, cfg) -> None:
    scripts = profile.scripts()
    if cfg.hide_cursor and not getattr(cfg, "cursor_timeout", 0):
        # Second line of defence next to the application override cursor: even if a compositor
        # ignores the client's cursor hint, the page itself asks for no cursor. (Not with
        # --show-cursor-timeout: the page must be able to show the pointer again.)
        _inject_css(scripts, "hide-cursor", _HIDE_CURSOR_CSS)
    if not getattr(cfg, "text_selection", False):
        # Native-app feel: no text selection, no long-press selection handles / callout. Form fields
        # keep selection so typing and editing still work. (Second net: KioskWindow clears any
        # selection Chromium still reports, see _on_selection_changed.)
        _inject_css(scripts, "no-select", _NO_SELECT_CSS)
    _install_fps_script(profile, cfg)
    for i, path in enumerate(cfg.inject_js):
        src = Path(path).read_text(encoding="utf-8")
        s = QWebEngineScript()
        s.setName(f"kioskium-js-{i}")
        s.setSourceCode(src)
        s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        s.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        s.setRunsOnSubFrames(True)
        scripts.insert(s)
        log.info("injecting JS %s", path)
    for i, path in enumerate(cfg.inject_css):
        css = Path(path).read_text(encoding="utf-8")
        js = (
            "(function(){var s=document.createElement('style');s.setAttribute('data-kioskium','css');"
            "s.textContent=%s;(document.head||document.documentElement).appendChild(s);})();" % _js_string(css)
        )
        s = QWebEngineScript()
        s.setName(f"kioskium-css-{i}")
        s.setSourceCode(js)
        s.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        s.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        s.setRunsOnSubFrames(False)
        scripts.insert(s)
        log.info("injecting CSS %s", path)


def _js_string(text: str) -> str:
    import json

    return json.dumps(text)


# --------------------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------------------
class KioskPage(QWebEnginePage):
    """QWebEnginePage with kiosk policies: navigation allow-list, dialogs, certificates, permissions."""

    newWindowWanted = Signal(object)  # QWebEngineNewWindowRequest, only when --allow-new-windows

    def __init__(self, profile: QWebEngineProfile, cfg, parent: QObject | None = None):
        super().__init__(profile, parent)
        self.cfg = cfg
        self.allow_hosts: list[str] = list(cfg.allow_host)
        self.certificateError.connect(self._on_certificate_error)
        self.fullScreenRequested.connect(lambda req: req.accept())
        self.newWindowRequested.connect(self._on_new_window_requested)
        if hasattr(self, "permissionRequested"):  # Qt >= 6.8
            self.permissionRequested.connect(self._on_permission_requested)
        else:  # pragma: no cover - older Qt
            self.featurePermissionRequested.connect(self._on_feature_permission_requested)

    # ---- navigation policy -------------------------------------------------------
    def acceptNavigationRequest(self, url: QUrl, nav_type, is_main_frame: bool) -> bool:  # noqa: N802
        if is_main_frame and not host_allowed(url.toString(), self.allow_hosts):
            log.warning("blocked navigation to %s (not in --allow-host list)", url.toString())
            return False
        return True

    # ---- JavaScript dialogs ---------------------------------------------------------
    def javaScriptAlert(self, origin: QUrl, msg: str) -> None:  # noqa: N802
        if self.cfg.allow_dialogs:
            super().javaScriptAlert(origin, msg)
        else:
            log.info("js alert (auto-dismissed): %s", msg)

    def javaScriptConfirm(self, origin: QUrl, msg: str) -> bool:  # noqa: N802
        if self.cfg.allow_dialogs:
            return super().javaScriptConfirm(origin, msg)
        log.info("js confirm (auto-cancelled): %s", msg)
        return False

    def javaScriptPrompt(self, origin: QUrl, msg: str, default: str):  # noqa: N802
        if self.cfg.allow_dialogs:
            return super().javaScriptPrompt(origin, msg, default)
        log.info("js prompt (auto-cancelled): %s", msg)
        return (False, "")

    def javaScriptConsoleMessage(self, level, message: str, line: int, source: str) -> None:  # noqa: N802
        L = QWebEnginePage.JavaScriptConsoleMessageLevel
        js_level = {L.ErrorMessageLevel: "error", L.WarningMessageLevel: "warn"}.get(level, "log")
        # --page-log file: real console level; main log: INFO with --page-console, else DEBUG (errors WARNING)
        page_log.log({"error": logging.ERROR, "warn": logging.WARNING}.get(js_level, logging.INFO),
                     "console.%s [%s:%s] %s", js_level, source or "-", line, message)
        lvl = logging.INFO if self.cfg.page_console else logging.DEBUG
        if js_level == "error":
            lvl = max(lvl, logging.WARNING)
        log.log(lvl, "page console [%s:%s] %s", source or "-", line, message)

    # ---- security / permissions ---------------------------------------------------
    def _on_certificate_error(self, error) -> None:
        if self.cfg.ignore_certificate_errors:
            log.warning("ignoring certificate error for %s: %s", error.url().toString(), error.description())
            error.acceptCertificate()
        else:
            log.error("certificate error for %s: %s", error.url().toString(), error.description())
            error.rejectCertificate()

    def _on_permission_requested(self, permission) -> None:
        if self.cfg.grant_permissions:
            log.info("granting permission %s for %s", permission.permissionType(), permission.origin().toString())
            permission.grant()
        else:
            log.info("denying permission %s for %s", permission.permissionType(), permission.origin().toString())
            permission.deny()

    def _on_feature_permission_requested(self, origin: QUrl, feature) -> None:  # pragma: no cover
        policy = (
            QWebEnginePage.PermissionPolicy.PermissionGrantedByUser
            if self.cfg.grant_permissions
            else QWebEnginePage.PermissionPolicy.PermissionDeniedByUser
        )
        self.setFeaturePermission(origin, feature, policy)

    # ---- popups ---------------------------------------------------------------------
    def _on_new_window_requested(self, request: QWebEngineNewWindowRequest) -> None:
        url = request.requestedUrl().toString()
        if self.cfg.allow_new_windows:
            log.info("opening new window for %s", url)
            self.newWindowWanted.emit(request)
        else:
            log.info("redirecting popup into current view: %s", url)
            request.openIn(self)


# --------------------------------------------------------------------------------------
# View
# --------------------------------------------------------------------------------------
class KioskView(QWebEngineView):
    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        # The native context menu (right-click / long-press) is off unless --context-menu is given.
        # The page's own 'contextmenu' event still fires, so custom in-app menus keep working.
        if not getattr(self.cfg, "context_menu", False):
            event.accept()
            return
        super().contextMenuEvent(event)

    def createWindow(self, window_type):  # noqa: N802
        # Only reached if newWindowRequested is not handled; never open uncontrolled windows.
        return None


# --------------------------------------------------------------------------------------
# Window
# --------------------------------------------------------------------------------------
class KioskWindow(QMainWindow):
    """Top-level kiosk window hosting one KioskView.

    Handles screen placement, fullscreen/kiosk flags, key/mouse lock-down, crash recovery,
    load retries with backoff, periodic reload and controlled shutdown.
    """

    _BLOCKED_KEYS = {
        Qt.Key.Key_F1, Qt.Key.Key_F2, Qt.Key.Key_F3, Qt.Key.Key_F4, Qt.Key.Key_F5, Qt.Key.Key_F6,
        Qt.Key.Key_F7, Qt.Key.Key_F8, Qt.Key.Key_F9, Qt.Key.Key_F10, Qt.Key.Key_F11, Qt.Key.Key_F12,
        Qt.Key.Key_Back, Qt.Key.Key_Forward, Qt.Key.Key_Refresh, Qt.Key.Key_HomePage, Qt.Key.Key_Search,
        Qt.Key.Key_ZoomIn, Qt.Key.Key_ZoomOut,
    }
    _BLOCKED_CTRL_KEYS = {
        Qt.Key.Key_W, Qt.Key.Key_N, Qt.Key.Key_T, Qt.Key.Key_Q, Qt.Key.Key_R, Qt.Key.Key_P, Qt.Key.Key_S,
        Qt.Key.Key_O, Qt.Key.Key_U, Qt.Key.Key_H, Qt.Key.Key_J, Qt.Key.Key_L, Qt.Key.Key_F, Qt.Key.Key_G,
        Qt.Key.Key_Plus, Qt.Key.Key_Minus, Qt.Key.Key_Equal, Qt.Key.Key_0, Qt.Key.Key_Underscore,
    }
    _BLOCKED_ALT_KEYS = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Home}

    def __init__(self, cfg, profile: QWebEngineProfile | None = None, start_url: QUrl | None = None, parent=None,
                 embedded: bool = False):
        """``embedded=True`` makes this a pane inside another window (see SplitWindow): no window
        flags, no fullscreen/placement, no exit timer or screenshot of its own."""
        super().__init__(parent)
        self.cfg = cfg
        self._embedded = embedded
        self._quitting = False
        self._load_failures = 0
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.timeout.connect(self._retry_load)
        self._pending_retry_url: QUrl | None = None
        self._load_timeout_timer = QTimer(self)
        self._load_timeout_timer.setSingleShot(True)
        self._load_timeout_timer.timeout.connect(self._on_load_timeout)
        self._reload_timer = QTimer(self)
        self._reload_timer.timeout.connect(self.reload)
        self._exit_sequence = QKeySequence(cfg.exit_shortcut) if cfg.exit_shortcut else None
        self._popups: list[KioskWindow] = []

        self.start_url = start_url or resolve_url(cfg.url, os.getcwd())
        self._owns_profile = profile is None
        self.profile = profile or build_profile(cfg, self)
        self.page = KioskPage(self.profile, cfg, self)
        # Loading / offline screen state
        self._loading_html, self._loading_base = build_loading_page(cfg)
        self._content: str | None = None  # None | "loading" | "target"
        self._after_loading = None  # callable run once the loading screen has been displayed
        self._pending_status: str | None = None
        self._retry_delay = 0.0
        if self.start_url.host() and cfg.allow_host:
            self.page.allow_hosts.append(self.start_url.host())
        self.view = KioskView(cfg, self)
        self.view.setPage(self.page)
        # The page always lives in a splitter so the in-window DevTools pane (a second WebEngine
        # view hosting the inspector, works in kiosk mode without a network port) can be created
        # on demand: by --devtools-open, the --devtools key, or window.kioskium.openDevTools().
        self.devtools: QWebEngineView | None = None
        self._devtools_sequence = QKeySequence(cfg.devtools_key) if cfg.devtools else None
        self._filtered: list[QObject] = []  # objects carrying the input event filter
        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.addWidget(self.view)
        self._splitter.setCollapsible(0, False)
        # Content holder: holds either the page splitter alone, or a pane splitter with a second
        # embedded KioskWindow next to it (see split()/unsplit()).
        self._content_box = QWidget(self)  # (not _content, which is the loading-screen state)
        self._content_layout = QVBoxLayout(self._content_box)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(0)
        self._content_layout.addWidget(self._splitter)
        self._pane_splitter: QSplitter | None = None
        self.second_pane: KioskWindow | None = None
        self.events = None  # ControlServer when a control channel is enabled (top-level window only)
        # Cursor auto-hide (--show-cursor-timeout): shown on hover movement, hidden when idle.
        self._cursor_timeout = float(getattr(cfg, "cursor_timeout", 0) or 0) if cfg.hide_cursor else 0.0
        self._hover_moves, self._hover_travel, self._hover_last = 0, 0, None
        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.timeout.connect(self._hide_cursor)
        self._host = parent.window() if embedded and parent is not None else None
        self.setCentralWidget(self._content_box)
        if cfg.devtools_open:
            self._ensure_devtools()
            self.devtools.setVisible(True)
        if not getattr(cfg, "no_js_api", False):
            self._js_bridge = install_js_api(self, self.page, self.profile)
        # Windowed mode shows the page's title (Qt appends the application name); kiosk mode has no title bar.
        self.view.titleChanged.connect(lambda title: self.setWindowTitle(title))

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor(cfg.background))
        self.setPalette(pal)
        self.setAutoFillBackground(True)
        self.page.setBackgroundColor(QColor(cfg.background))

        flags = Qt.WindowType.Window
        if cfg.kiosk:  # frameless and above other windows (notifications, other apps); --windowed is neither
            flags |= Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        if embedded:
            # QMainWindow always sets Qt::Window on itself; strip it so the pane is a real child widget.
            self.setWindowFlags(Qt.WindowType.Widget)
        else:
            self.setWindowFlags(flags)

        # signals
        self.page.loadingChanged.connect(self._on_loading_changed)
        self.page.loadFinished.connect(self._on_load_finished)
        self.page.renderProcessTerminated.connect(self._on_render_process_terminated)
        self.page.windowCloseRequested.connect(self._on_window_close_requested)
        self.page.newWindowWanted.connect(self._open_popup)
        self.profile.downloadRequested.connect(self._on_download_requested)
        if not getattr(cfg, "text_selection", False):
            self.page.selectionChanged.connect(self._on_selection_changed)

        app = QApplication.instance()
        app.aboutToQuit.connect(self._mark_quitting)
        # Input lock-down filter. Deliberately NOT an application-wide filter: PySide (seen on 6.8.0)
        # crashes when a Python app-level filter receives events for Qt Quick objects inside the
        # WebEngine view that have no Python wrapper yet. Filtering the view and its focus proxy
        # (objects Python already wraps) catches all keyboard/mouse input that reaches the page.
        self._install_input_filter()
        self.page.loadStarted.connect(self._install_input_filter)
        self.page.loadFinished.connect(lambda _ok: self._install_input_filter())

        if cfg.reload_interval and cfg.reload_interval > 0:
            self._reload_timer.start(int(cfg.reload_interval * 1000))
        if cfg.exit_after and not embedded:
            QTimer.singleShot(int(cfg.exit_after * 1000), self._quit)
        if cfg.screenshot and getattr(cfg, "screenshot_delay", 0) and not embedded:
            QTimer.singleShot(int(cfg.screenshot_delay * 1000), self._take_screenshot)

    # ---- lifecycle ------------------------------------------------------------------
    def start(self) -> None:
        screen = resolve_screen(self.cfg.screen)
        if screen is not None:
            log.info("targeting screen '%s' %s", screen.name(), _rect_str(screen.geometry()))
            self.setScreen(screen)
            self.setGeometry(screen.geometry())
        if self.cfg.kiosk:
            self.showFullScreen()
        else:
            if self.cfg.size:
                w, h, x, y = parse_geometry(self.cfg.size)
                self.resize(w, h)
                if x is not None and y is not None:
                    self.move(x, y)
            self.show()
        if self.cfg.hide_cursor:
            self._hide_cursor()
        self.begin()
        if getattr(self.cfg, "split", None):
            self.split(self.cfg.split)

    def begin(self) -> None:
        """Start loading (used directly for embedded panes; start() does placement first)."""
        if self.devtools is not None and self.cfg.devtools_open:
            QTimer.singleShot(0, lambda: self._splitter.setSizes([int(self.height() * 0.55), int(self.height() * 0.45)]))
        log.info("loading %s", self.start_url.toString())
        self._go(self.start_url)

    def _go(self, url: QUrl) -> None:
        """Show the loading screen, then load ``url``. For kioskium:idle, show the screen and wait for a URL."""
        if not is_idle(url):
            self._show_loading("connecting", url, then=lambda: self.load(url))
            return
        self._load_timeout_timer.stop()
        self._retry_timer.stop()
        log.info("idle: waiting for a URL")
        self._show_loading("idle", url)

    def load(self, url: QUrl) -> None:
        if is_idle(url):
            self._go(url)
            return
        if self.cfg.load_timeout and self.cfg.load_timeout > 0:
            self._load_timeout_timer.start(int(self.cfg.load_timeout * 1000))
        self.view.load(url)

    def reload(self) -> None:
        if is_idle(self.start_url):
            self._go(self.start_url)
            return
        log.info("reloading %s", self.view.url().toString())
        if self.cfg.load_timeout and self.cfg.load_timeout > 0:
            self._load_timeout_timer.start(int(self.cfg.load_timeout * 1000))
        self.view.reload()

    def shutdown(self) -> None:
        """Delete every page before the profile it uses; call after the event loop has ended.

        Left to Qt, the window's children are destroyed in creation order, so the profile goes
        before the page ("Release of profile requested but WebEnginePage still not deleted") and
        Chromium intermittently segfaults on exit (exit code 139).
        """
        import shiboken6

        def _delete(obj) -> None:
            if obj is not None and shiboken6.isValid(obj):
                shiboken6.delete(obj)

        for popup in list(self._popups):
            if shiboken6.isValid(popup):
                popup.shutdown()
        if self.second_pane is not None and shiboken6.isValid(self.second_pane):
            self.second_pane.shutdown()
        _delete(self.devtools)  # its inspector page is a child of the view
        _delete(self.view)
        _delete(self.page)
        if self._owns_profile:
            _delete(self.profile)

    def _quit(self) -> None:
        self._quitting = True
        self.emit_event("quit")
        QApplication.instance().quit()

    def _mark_quitting(self) -> None:
        self._quitting = True

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.cfg.kiosk and not self._quitting:
            log.info("close request ignored (kiosk mode)")
            event.ignore()
            return
        super().closeEvent(event)

    # ---- control-channel surface ----------------------------------------------------------------
    def emit_event(self, name: str, **data) -> None:
        """Publish an event on the control channel (embedded panes forward to their host window)."""
        if self._embedded:
            host = self._host
            if host is not None and hasattr(host, "emit_event"):
                host.emit_event(name, pane=2, **data)
            return
        if self.events is not None:
            self.events.publish(name, {"pane": 1, **data})

    def status_dict(self) -> dict:
        from kioskium.cli import version_string

        return {
            "ok": True,
            "appId": self.cfg.app_id,
            "name": getattr(self.cfg, "name", None) or self.cfg.app_id,
            "pid": os.getpid(),
            "url": self.view.url().toString(),
            "targetUrl": self.start_url.toString(),
            "state": "idle" if is_idle(self.start_url) else (self._content or "connecting"),
            "loadFailures": self._load_failures,
            "split": None if self.second_pane is None else {
                "url": self.second_pane.view.url().toString(),
                "state": "idle" if is_idle(self.second_pane.start_url) else (self.second_pane._content or "connecting"),
            },
            "devtools": bool(self.devtools is not None and self.devtools.isVisible()),
            "fps": bool(getattr(self.cfg, "fps", False)),
            "kiosk": bool(self.cfg.kiosk),
            "version": version_string().splitlines()[1:3],
        }

    def navigate(self, url) -> None:
        """Load a new target URL (goes through the loading screen like the initial load)."""
        target = url if isinstance(url, QUrl) else resolve_url(str(url), os.getcwd())
        self.start_url = target
        self._load_failures = 0
        self._retry_timer.stop()
        if target.host() and self.cfg.allow_host and target.host() not in self.page.allow_hosts:
            self.page.allow_hosts.append(target.host())
        self.emit_event("navigate", url=target.toString())
        self._go(target)

    def set_fps(self, enabled: bool) -> None:
        """Show/hide the frame-rate overlay in every pane, now and for pages loaded later."""
        if self._embedded:
            return
        self.cfg.fps = bool(enabled)  # cfg is shared with the second pane
        _install_fps_script(self.profile, self.cfg)
        code = _fps_script(self.cfg, True) if enabled else "window.__kioskiumFps && window.__kioskiumFps.stop();"
        for pane in (self, self.second_pane):
            if pane is not None:
                pane.page.runJavaScript(code)
        log.info("fps overlay %s", "on" if enabled else "off")
        self.emit_event("fps", enabled=bool(enabled))

    def screenshot_to(self, path: str) -> None:
        previous = self.cfg.screenshot
        self.cfg.screenshot = path
        try:
            self._take_screenshot()
        finally:
            self.cfg.screenshot = previous

    # ---- split screen (runtime) ---------------------------------------------------------------
    def split(self, url, orientation: str | None = None, ratio: int | None = None) -> None:
        """Show a second page next to this one (or navigate the existing second pane)."""
        if self._embedded:
            return
        target = url if isinstance(url, QUrl) else resolve_url(str(url), os.getcwd())
        if self.second_pane is not None:
            log.info("split: second pane -> %s", target.toString())
            self.second_pane.start_url = target
            self.second_pane._go(target)
            return
        orientation = orientation or getattr(self.cfg, "split_orientation", "horizontal")
        ratio = int(ratio if ratio is not None else getattr(self.cfg, "split_ratio", 50))
        self._pane_splitter = QSplitter(
            Qt.Orientation.Horizontal if orientation == "horizontal" else Qt.Orientation.Vertical, self._content_box
        )
        self._pane_splitter.setChildrenCollapsible(False)
        self._pane_splitter.setHandleWidth(int(getattr(self.cfg, "split_gap", 0)))
        self._content_layout.removeWidget(self._splitter)
        self._pane_splitter.addWidget(self._splitter)
        self.second_pane = KioskWindow(self.cfg, profile=self.profile, start_url=target, parent=self._pane_splitter, embedded=True)
        self._pane_splitter.addWidget(self.second_pane)
        self._content_layout.addWidget(self._pane_splitter)
        self._pane_splitter.show()
        total = self._content_box.width() if orientation == "horizontal" else self._content_box.height()
        first = max(1, min(99, ratio))
        self._pane_splitter.setSizes([int(total * first / 100), int(total * (100 - first) / 100)])
        log.info("split %s: %s | %s", orientation, self.start_url.toString(), target.toString())
        self.second_pane.begin()
        self.emit_event("split", url=target.toString(), orientation=orientation, ratio=first)

    def unsplit(self) -> None:
        """Back to a single page."""
        if self.second_pane is None:
            return
        log.info("unsplit")
        self._content_layout.removeWidget(self._pane_splitter)
        self._splitter.setParent(self._content_box)
        self._content_layout.addWidget(self._splitter)
        self._splitter.show()
        self.second_pane.hide()
        self.second_pane.deleteLater()
        self.second_pane = None
        self._pane_splitter.hide()
        self._pane_splitter.deleteLater()
        self._pane_splitter = None
        self.view.setFocus()
        self.emit_event("unsplit")

    # ---- loading screen ---------------------------------------------------------------------
    def _is_loading_url(self, url: QUrl) -> bool:
        return url == self._loading_base

    def _show_loading(self, state: str, url: QUrl, attempt: int = 0, retry_in: float = 0.0, error: str = "",
                      then=None, force: bool = False) -> None:
        """Display the loading screen, or just update its status if it is already on screen.

        ``then`` runs once the screen is actually displayed, so the next navigation never starts
        before the user sees it.
        """
        self._pending_status = status_script(state, url.toString(), attempt, retry_in, error)
        if self._content == "loading" and not force:
            self.page.runJavaScript(self._pending_status)
            self._pending_status = None
            if then:
                then()
            return
        self._after_loading = then
        self._content = None
        log.info("showing loading screen (%s)", state)
        self.page.setHtml(self._loading_html, self._loading_base)

    # ---- loading / resilience ---------------------------------------------------------
    def _on_loading_changed(self, info: QWebEngineLoadingInfo) -> None:
        status = info.status()
        S = QWebEngineLoadingInfo.LoadStatus
        url = info.url()
        if status == S.LoadStartedStatus:
            log.debug("load started: %s", url.toString())
            if not self._is_loading_url(url) and url.scheme() != "data":  # (data: = the loading screen itself)
                self.emit_event("load", status="started", url=url.toString())
        elif status == S.LoadSucceededStatus:
            if self._is_loading_url(url):
                self._content = "loading"
                if self._pending_status:
                    self.page.runJavaScript(self._pending_status)
                    self._pending_status = None
                after, self._after_loading = self._after_loading, None
                if after:
                    after()
                return
            self._load_timeout_timer.stop()
            if self._load_failures:
                log.info("load recovered after %d failure(s)", self._load_failures)
            self._load_failures = 0
            self._retry_timer.stop()
            self._content = "target"
            log.info("load finished: %s", url.toString())
            self.emit_event("load", status="finished", url=url.toString())
        elif status == S.LoadFailedStatus:
            if self._is_loading_url(url):
                log.error("the loading screen itself failed to load: %s", info.errorString())
                return
            self._load_timeout_timer.stop()
            self._load_failures += 1
            error = info.errorString() or f"{info.errorDomain()}/{info.errorCode()}"
            log.warning("load failed (%d): %s [%s/%s] %s", self._load_failures, url.toString(),
                        info.errorDomain(), info.errorCode(), info.errorString())
            page_log.warning("load failed (%d) %s: %s", self._load_failures, url.toString(), error)
            delay = self._schedule_retry(url)
            self.emit_event("load", status="failed", url=url.toString(), error=error, attempt=self._load_failures, retryIn=delay)
            # An HTTP error (4xx/5xx) commits the server's error body, so the loading screen must be
            # put back; a network error never commits and the screen is still there.
            http_error = info.errorDomain() == QWebEngineLoadingInfo.ErrorDomain.HttpStatusCodeDomain
            self._show_loading("retrying", url, self._load_failures, delay, error, force=http_error)
        elif status == S.LoadStoppedStatus:
            self._load_timeout_timer.stop()
            log.debug("load stopped: %s", url.toString())

    def _schedule_retry(self, failed_url: QUrl) -> float:
        base = max(0.5, float(self.cfg.retry_interval))
        delay = min(base * (2 ** min(self._load_failures - 1, 6)), MAX_RETRY_DELAY_S)
        if self._load_failures >= FAILURES_BEFORE_HOME or not failed_url.isValid() or failed_url.isEmpty():
            self._pending_retry_url = self.start_url
        else:
            self._pending_retry_url = failed_url
        self._retry_delay = delay
        log.info("retrying %s in %.1fs", self._pending_retry_url.toString(), delay)
        self._retry_timer.start(int(delay * 1000))
        return delay

    def _retry_load(self) -> None:
        if self._pending_retry_url is not None:
            self.load(self._pending_retry_url)

    def _on_load_timeout(self) -> None:
        log.warning("load timed out after %.1fs, retrying", self.cfg.load_timeout)
        self.view.stop()
        self._load_failures += 1
        url = self._pending_retry_url or self.view.url()
        delay = self._schedule_retry(url)
        self._show_loading("retrying", url, self._load_failures, delay, "timeout")

    def _on_load_finished(self, ok: bool) -> None:
        if ok and self.cfg.screenshot and not self._embedded:
            QTimer.singleShot(750, self._take_screenshot)

    def _take_screenshot(self) -> None:
        """Compose the capture from the visible child widgets (page+DevTools splitter, second pane):
        grabbing the top-level window directly leaves embedded panes black."""
        from PySide6.QtGui import QPainter, QPixmap

        path = Path(self.cfg.screenshot).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(QColor(self.cfg.background))
        painter = QPainter(pix)
        parts = [self._splitter]
        if self.second_pane is not None:
            parts.append(self.second_pane)
        for w in parts:
            painter.drawPixmap(w.mapTo(self, w.rect().topLeft()), w.grab())
        painter.end()
        if pix.save(str(path), "PNG"):
            log.info("screenshot saved to %s (%dx%d)", path, pix.width(), pix.height())
        else:
            log.error("failed to save screenshot to %s", path)

    def _on_render_process_terminated(self, status, exit_code: int) -> None:
        log.error("render process terminated: status=%s exit_code=%s", status, exit_code)
        page_log.error("render process terminated: status=%s exit_code=%s", status, exit_code)
        self.emit_event("crash", status=str(status), exitCode=int(exit_code))
        if self.cfg.no_reload_on_crash:
            return
        delay = max(0.5, float(self.cfg.retry_interval))
        url = self.view.url() if self._content == "target" else (self._pending_retry_url or self.start_url)
        if not url.isValid() or url.isEmpty() or self._is_loading_url(url):
            url = self.start_url
        log.info("reloading %s after render process termination in %.1fs", url.toString(), delay)
        self._content = None  # the crashed renderer took the page with it
        self._show_loading("crashed", url, self._load_failures, delay, f"render process exit code {exit_code}",
                           then=lambda: QTimer.singleShot(int(delay * 1000), lambda: self.load(url)), force=True)

    _EDITABLE_JS = ("(function(){var a=document.activeElement;return !!(a&&(a.tagName==='INPUT'||a.tagName==='TEXTAREA'"
                    "||a.isContentEditable));})()")

    def _on_selection_changed(self) -> None:
        """Clear any text selection outside editable fields (app-level, independent of page CSS)."""
        if not self.page.hasSelection():
            return

        def _maybe_unselect(editable):
            if not editable and self.page.hasSelection():
                self.page.triggerAction(QWebEnginePage.WebAction.Unselect)

        self.page.runJavaScript(self._EDITABLE_JS, _maybe_unselect)

    def _on_window_close_requested(self) -> None:
        if self.cfg.kiosk:
            log.info("window.close() from page ignored (kiosk mode)")
        else:
            self.close()

    def _on_download_requested(self, item: QWebEngineDownloadRequest) -> None:
        if self.cfg.download_dir:
            log.info("download accepted: %s -> %s", item.url().toString(), item.downloadDirectory())
            item.accept()
        else:
            log.warning("download cancelled (no --download-dir): %s", item.url().toString())
            item.cancel()

    def _open_popup(self, request: QWebEngineNewWindowRequest) -> None:
        popup = KioskWindow(self.cfg, profile=self.profile, start_url=request.requestedUrl())
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        popup.page.windowCloseRequested.disconnect()
        popup.page.windowCloseRequested.connect(popup._close_popup)
        self._popups.append(popup)
        popup.destroyed.connect(lambda *_: self._popups.remove(popup) if popup in self._popups else None)
        request.openIn(popup.page)
        popup.start_url = request.requestedUrl()
        if self.cfg.kiosk:
            popup.showFullScreen()
        else:
            popup.show()

    def _close_popup(self) -> None:
        self._quitting = True  # bypass the kiosk close guard for this popup only
        self.close()

    # ---- input lock-down ------------------------------------------------------------------
    def _needs_input_filter(self) -> bool:
        return (
            self._exit_sequence is not None
            or self._devtools_sequence is not None
            or self.devtools is not None
            or self._cursor_timeout > 0
            or (self.cfg.kiosk and not self.cfg.allow_shortcuts)
        )

    # ---- cursor auto-hide ----------------------------------------------------------------------
    @staticmethod
    def _cursor_hidden() -> bool:
        return QApplication.overrideCursor() is not None

    def _hide_cursor(self) -> None:
        if not self._cursor_hidden():
            QApplication.setOverrideCursor(Qt.CursorShape.BlankCursor)
            if self._cursor_timeout > 0:
                log.debug("cursor hidden (idle)")
        self._hover_moves, self._hover_travel, self._hover_last = 0, 0, None

    def _show_cursor(self) -> None:
        if self._cursor_hidden():
            QApplication.restoreOverrideCursor()
            log.debug("cursor shown (mouse movement)")
        self._cursor_timer.start(int(self._cursor_timeout * 1000))

    def _track_pointer(self, event) -> None:
        """Hover movement (several moves with no button down) shows the cursor; a touch tap, which
        arrives as press/release with at most one move, never does."""
        et = event.type()
        if et == QEvent.Type.MouseMove and not event.buttons():
            pos = event.globalPosition().toPoint()
            if self._hover_last is not None:
                self._hover_travel += abs(pos.x() - self._hover_last.x()) + abs(pos.y() - self._hover_last.y())
            self._hover_last = pos
            self._hover_moves += 1
            if not self._cursor_hidden():
                self._cursor_timer.start(int(self._cursor_timeout * 1000))
            elif self._hover_moves >= 3 and self._hover_travel >= 12:
                # a few events and a dozen pixels: hand movement, not jitter or a synthetic move
                self._show_cursor()
        elif et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            self._hover_moves, self._hover_travel, self._hover_last = 0, 0, None
            if not self._cursor_hidden():
                self._cursor_timer.start(int(self._cursor_timeout * 1000))

    def _ensure_devtools(self) -> None:
        if self.devtools is not None:
            return
        self.devtools = QWebEngineView(self)
        # Own (default, off-the-record) profile: the kiosk's injected scripts/CSS must not run inside the inspector.
        self.devtools.setPage(QWebEnginePage(QWebEngineProfile.defaultProfile(), self.devtools))
        self.page.setDevToolsPage(self.devtools.page())
        self.devtools.loadFinished.connect(self._on_devtools_loaded)
        self.devtools.setVisible(False)
        self._splitter.addWidget(self.devtools)
        self._install_input_filter()

    def set_devtools_visible(self, show: bool) -> None:
        self._ensure_devtools()
        if self.devtools.isVisible() == show:
            return
        self.devtools.setVisible(show)
        if show:
            h = max(self.height(), 2)
            self._splitter.setSizes([int(h * 0.55), int(h * 0.45)])
            self.devtools.setFocus()
        else:
            self.view.setFocus()
        log.info("devtools %s", "shown" if show else "hidden")
        self.emit_event("devtools", visible=show)

    def _on_devtools_loaded(self, ok: bool) -> None:
        log.info("devtools pane loaded: %s (%s)", ok, self.devtools.url().toString()[:60] if self.devtools else "")
        if ok and self.cfg.screenshot and not self._embedded and self.devtools is not None and self.devtools.isVisible():
            QTimer.singleShot(5000, self._take_screenshot)

    def toggle_devtools(self) -> None:
        self.set_devtools_visible(self.devtools is None or not self.devtools.isVisible())

    def _install_input_filter(self) -> None:
        """(Re)install the event filter on the view and its current focus proxy.

        The focus proxy (Chromium's render widget host view) is recreated on some navigations and
        after a render-process restart, so this is called again on every load.
        """
        if not self._needs_input_filter():
            return
        targets = [self.view]
        proxy = self.view.focusProxy()
        if proxy is not None:
            targets.append(proxy)
        if self.devtools is not None:
            targets.append(self.devtools)
            dproxy = self.devtools.focusProxy()
            if dproxy is not None:
                targets.append(dproxy)
        for target in targets:
            if all(target is not t for t in self._filtered):
                target.installEventFilter(self)
                self._filtered.append(target)

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        et = event.type()
        if self._cursor_timeout > 0 and et in (QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
            self._track_pointer(event)
        if et == QEvent.Type.KeyPress or et == QEvent.Type.ShortcutOverride:
            if self._exit_sequence is not None and et == QEvent.Type.KeyPress:
                if QKeySequence(event.keyCombination()) == self._exit_sequence:
                    log.info("exit shortcut pressed")
                    self._quit()
                    return True
            if self._devtools_sequence is not None and et == QEvent.Type.KeyPress:
                if QKeySequence(event.keyCombination()) == self._devtools_sequence:
                    self.toggle_devtools()
                    return True
            if self.cfg.kiosk and not self.cfg.allow_shortcuts and self._is_blocked_key(event):
                return True
        elif self.cfg.kiosk and not self.cfg.allow_shortcuts:
            if et in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease, QEvent.Type.MouseButtonDblClick):
                if event.button() in (Qt.MouseButton.BackButton, Qt.MouseButton.ForwardButton):
                    return True
            elif et == QEvent.Type.Wheel and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                return True  # ctrl+wheel zoom
        return super().eventFilter(obj, event)

    def _is_blocked_key(self, event) -> bool:
        key = Qt.Key(event.key())
        mods = event.modifiers()
        if key in self._BLOCKED_KEYS:
            return True
        if mods & Qt.KeyboardModifier.ControlModifier and key in self._BLOCKED_CTRL_KEYS:
            return True
        if mods & Qt.KeyboardModifier.AltModifier and key in self._BLOCKED_ALT_KEYS:
            return True
        return False


# --------------------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------------------
def resolve_screen(spec: str | None) -> QScreen | None:
    if not spec:
        return None
    screens = QApplication.screens()
    s = spec.strip()
    if s.isdigit():
        idx = int(s)
        if 0 <= idx < len(screens):
            return screens[idx]
        log.error("screen index %d out of range (%d screens)", idx, len(screens))
        return None
    low = s.lower()
    for scr in screens:
        if scr.name().lower() == low:
            return scr
    for scr in screens:
        if low in scr.name().lower() or low in f"{scr.manufacturer()} {scr.model()}".lower():
            return scr
    log.error("no screen matches '%s'; available: %s", spec, ", ".join(x.name() for x in screens))
    return None



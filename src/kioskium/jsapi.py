"""JavaScript API exposed to pages as ``window.kioskium`` (Qt WebChannel bridge).

Available once the page has loaded (or after the ``kioskiumReady`` document event):

    window.kioskium.openDevTools()      // show the in-window inspector pane
    window.kioskium.closeDevTools()
    window.kioskium.toggleDevTools()
    window.kioskium.reload()            // reload the current page
    window.kioskium.split(url)          // second pane next to this page (runtime split screen)
    window.kioskium.splitWith(url, "vertical", 60)   // orientation + first-pane share in percent
    window.kioskium.unsplit()           // back to a single page
    window.kioskium.showFps(true)       // frame-rate overlay in every pane (false hides it)
    window.kioskium.info()              // Promise<string>: JSON with app/Qt/Chromium versions
    window.kioskium.log("text")         // write to the browser's log at INFO level

All calls are asynchronous (WebChannel messages); methods returning a value resolve a Promise.
"""

from __future__ import annotations

import json
import logging

from PySide6.QtCore import QFile, QIODevice, QObject, Slot
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineScript

log = logging.getLogger("kioskium")

_BOOTSTRAP_JS = """
(function () {
  if (typeof qt === 'undefined' || !qt.webChannelTransport) { return; }
  new QWebChannel(qt.webChannelTransport, function (channel) {
    window.kioskium = channel.objects.kioskium;
    try { document.dispatchEvent(new Event('kioskiumReady')); } catch (e) {}
  });
})();
"""


class JsBridge(QObject):
    """Methods callable from page JavaScript. Keep this surface small: it runs in kiosk pages."""

    def __init__(self, window):
        super().__init__(window)
        self._window = window

    @Slot()
    def openDevTools(self) -> None:  # noqa: N802 - JS-facing name
        log.info("js api: openDevTools")
        self._window.set_devtools_visible(True)

    @Slot()
    def closeDevTools(self) -> None:  # noqa: N802
        log.info("js api: closeDevTools")
        self._window.set_devtools_visible(False)

    @Slot()
    def toggleDevTools(self) -> None:  # noqa: N802
        log.info("js api: toggleDevTools")
        self._window.toggle_devtools()

    @Slot()
    def reload(self) -> None:
        log.info("js api: reload")
        self._window.reload()

    @Slot(str)
    def split(self, url: str) -> None:
        """Show URL in a second pane next to the page (or navigate the existing second pane)."""
        log.info("js api: split %s", url)
        self._window.split(url)

    @Slot(str, str, int)
    def splitWith(self, url: str, orientation: str, ratio: int) -> None:  # noqa: N802
        log.info("js api: split %s (%s, %d%%)", url, orientation, ratio)
        self._window.split(url, orientation, ratio)

    @Slot()
    def unsplit(self) -> None:
        log.info("js api: unsplit")
        self._window.unsplit()

    @Slot(bool)
    def showFps(self, enabled: bool) -> None:  # noqa: N802
        log.info("js api: fps %s", enabled)
        self._window.set_fps(enabled)

    @Slot(result=str)
    def info(self) -> str:
        from kioskium.cli import version_string

        cfg = self._window.cfg
        return json.dumps({"version": version_string().splitlines(), "name": getattr(cfg, "name", None) or cfg.app_id,
                           "appId": cfg.app_id})

    @Slot(str)
    def log(self, text: str) -> None:
        log.info("page: %s", text)


def install_js_api(window, page, profile) -> JsBridge:
    """Register the bridge on ``page`` and inject the client script into every page of ``profile``."""
    bridge = JsBridge(window)
    channel = QWebChannel(page)
    channel.registerObject("kioskium", bridge)
    page.setWebChannel(channel, QWebEngineScript.ScriptWorldId.MainWorld)
    window._js_channel = channel  # keep alive

    f = QFile(":/qtwebchannel/qwebchannel.js")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly):
        log.error("qwebchannel.js not found in Qt resources; window.kioskium unavailable")
        return bridge
    client_js = bytes(f.readAll()).decode("utf-8")
    f.close()

    script = QWebEngineScript()
    script.setName("kioskium-js-api")
    script.setSourceCode(client_js + "\n" + _BOOTSTRAP_JS)
    script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
    script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
    script.setRunsOnSubFrames(False)
    profile.scripts().insert(script)
    log.info("window.kioskium JavaScript API enabled")
    return bridge

"""The loading / offline screen shown instead of Chromium's error page.

The page is plain HTML set into the view with ``setHtml``. Whenever the browser is waiting for the
target site it calls ``window.kioskiumStatus(status)`` on this page (if the function exists) with:

    {
      "state":   "connecting" | "retrying" | "crashed" | "idle",   # idle: no URL yet (kioskium:idle)
      "url":     "https://…",            # the page being loaded
      "attempt": 3,                       # consecutive failures so far (0 while connecting)
      "retryIn": 8,                       # seconds until the next attempt (0 = now)
      "error":   "net::ERR_CONNECTION_REFUSED"   # last error text, may be empty
    }

A custom page (``--loading-page FILE``) can implement that function to show whatever it likes; the
file's own directory is the base URL, so relative images/fonts/CSS work.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QUrl

DEFAULT_LOADING_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Loading</title>
<style>
  html, body { margin: 0; height: 100%; background: __BACKGROUND__; color: #e6edf3;
               font: 18px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; __CURSOR__
               user-select: none; -webkit-user-select: none; -webkit-touch-callout: none; }
  main { height: 100%; display: flex; flex-direction: column; align-items: center; justify-content: center;
         text-align: center; padding: 24px; box-sizing: border-box; }
  h1 { margin: 0 0 22px; font-size: 28px; font-weight: 600; letter-spacing: .01em; }
  /* indeterminate progress bar: the fill grows in from the left, then shrinks away to the right.
     Clipping (not resizing) the fill keeps its rounded ends round and avoids a re-layout per frame. */
  .progress { position: relative; width: min(460px, 64vw); height: 10px; border-radius: 999px; overflow: hidden;
              background: rgba(255,255,255,.12); box-shadow: inset 0 0 0 1px rgba(255,255,255,.08); }
  .progress::before { content: ""; position: absolute; inset: 0; border-radius: 999px;
                      background: linear-gradient(90deg, #ffffff, #dfe7f1);
                      box-shadow: 0 0 12px rgba(255,255,255,.35);
                      animation: running-progress 2.4s cubic-bezier(.65, 0, .35, 1) infinite; }
  @keyframes running-progress {
    0%   { clip-path: inset(0 100% 0 0 round 999px); }
    50%  { clip-path: inset(0 0 0 0 round 999px); }
    100% { clip-path: inset(0 0 0 100% round 999px); }
  }
  .sub { margin-top: 20px; color: #8b949e; font-size: 15px; max-width: 80vw; overflow-wrap: anywhere; }
  .err { margin-top: 6px; color: #6e7681; font-size: 13px; font-family: ui-monospace, monospace; }
  .sub:empty, .err:empty { display: none; }
</style>
</head>
<body>
<main>
  <h1 id="title">__TITLE__</h1>
  <div class="progress" role="progressbar" aria-label="loading"></div>
  <div class="sub" id="detail">__DETAIL__</div>
  <div class="err" id="error"></div>
</main>
<script>
  (function () {
    // --loading-status: also show the retry countdown, the last error and crash recovery.
    // Without it the screen only shows the title (and --loading-detail, if set).
    var showStatus = __STATUS__;
    var last = null, timer = null;
    function render() {
      if (!last) return;
      var t = document.getElementById('title'), d = document.getElementById('detail'), e = document.getElementById('error');
      if (!showStatus || last.state === 'connecting' || last.state === 'idle') {
        t.textContent = '__TITLE__';
        d.textContent = '__DETAIL__';
      } else if (last.state === 'crashed') {
        t.textContent = 'Restarting\\u2026';
        d.textContent = 'The page stopped responding and is being reloaded.';
      } else {
        t.textContent = '__TITLE__';
        var secs = Math.max(0, Math.round(last.retryIn));
        d.textContent = 'Attempt ' + last.attempt + (secs > 0 ? ' \\u00b7 retrying in ' + secs + 's' : ' \\u00b7 retrying now');
      }
      e.textContent = showStatus ? (last.error || '') : '';
    }
    window.kioskiumStatus = function (s) {
      last = s;
      if (timer) clearInterval(timer);
      render();
      timer = setInterval(function () { if (last && last.retryIn > 0) { last.retryIn -= 1; render(); } }, 1000);
    };
  })();
</script>
</body>
</html>
"""


def build_loading_page(cfg) -> tuple[str, QUrl]:
    """Return (html, base_url) for the loading screen according to the configuration."""
    if cfg.loading_page:
        path = Path(cfg.loading_page).expanduser().resolve()
        return path.read_text(encoding="utf-8"), QUrl.fromLocalFile(str(path))
    html = (
        DEFAULT_LOADING_HTML.replace("__BACKGROUND__", cfg.background or "#000000")
        .replace("__CURSOR__", "cursor: none;" if getattr(cfg, "hide_cursor", False) and not getattr(cfg, "cursor_timeout", 0) else "")
        .replace("__TITLE__", _escape(cfg.loading_title))
        .replace("__DETAIL__", _escape(cfg.loading_detail))
        .replace("__STATUS__", "true" if getattr(cfg, "loading_status", False) else "false")
    )
    return html, QUrl("about:blank")


def status_script(state: str, url: str, attempt: int, retry_in: float, error: str = "") -> str:
    """JavaScript that pushes a status object to the loading page (no-op if it has no handler)."""
    payload = json.dumps(
        {"state": state, "url": url, "attempt": int(attempt), "retryIn": round(float(retry_in), 1), "error": error or ""}
    )
    return f"window.kioskiumStatus && window.kioskiumStatus({payload});"


def _escape(text: str) -> str:
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )

"""Local control channel: HTTP + JSON API, instance registry, and the ``kioskctl`` client (also ``kiosk ctl``).

Server (inside the browser, on the Qt event loop so every command runs on the UI thread):
    --control-port N        listen on 127.0.0.1:N
    --control-socket PATH   listen on a Unix socket (Linux); same API
    --control-token T       require header  X-Kioskium-Token: T  (or  Authorization: Bearer T)

    GET  /api/health                  -> {ok, state}   no token needed; an answer means the UI thread is alive
    GET  /api/status                  -> {ok, name, appId, pid, url, state, split, devtools, fps, version}
    POST /api/navigate   {"url", "pane"?}        pane 1 = main page (default), 2 = second pane of a split
    POST /api/reload     {"pane"?}
    POST /api/split      {"url", "orientation"?, "ratio"?}
    POST /api/unsplit
    POST /api/devtools   {"action": "open"|"close"|"toggle"}
    POST /api/fps        {"enabled": true|false|"toggle"}      frame-rate overlay in every pane
    POST /api/screenshot {"path"}
    POST /api/js         {"code", "pane"?} -> {"result"}
    POST /api/quit
    GET  /api/events[?since=SEQ]      -> Server-Sent Events stream: "event: <name>" / "data: {json}" with ids;
                                         events: load, navigate, crash, split, unsplit, devtools, fps, quit

Registry: every instance writes <runtime dir>/kioskium/<name>.json (appId, pid, port, socket, url, startedAt)
and removes it on exit. The name identifies a browser; the app-id only places its window (Wayland).
``ctl list`` checks the pid and pings the API, so stale files are reported as such.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import sys
import time
from pathlib import Path

log = logging.getLogger("kioskium")

_SAFE_NAME = str.maketrans({c: "_" for c in '/\\:*?"<>|'})


# ------------------------------------------------------------------------------------------
# Registry (pure Python)
# ------------------------------------------------------------------------------------------
def runtime_dir() -> Path:
    """Where registrations (with their tokens), sockets and kioskctl's logs live: private to this user."""
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "kioskium" / "run"  # the profile is the user's own
        base.mkdir(parents=True, exist_ok=True)
        return base
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(xdg) / "kioskium" if xdg else Path(f"/tmp/kioskium-{os.getuid()}")
    base.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = base.stat()
    if st.st_uid != os.getuid():  # e.g. someone else created /tmp/kioskium-UID first
        raise PermissionError(f"{base} belongs to another user; set XDG_RUNTIME_DIR")
    if st.st_mode & 0o077:
        base.chmod(0o700)
    return base


def default_socket_path(name: str) -> str:
    return str(runtime_dir() / (name.translate(_SAFE_NAME) + ".sock"))


def registry_path(name: str) -> Path:
    return runtime_dir() / (name.translate(_SAFE_NAME) + ".json")


def register_instance(name: str, port: int | None, socket_path: str | None, url: str, app_id: str | None = None,
                      token: str | None = None) -> Path:
    path = registry_path(name)
    data = {"name": name, "appId": app_id or name, "pid": os.getpid(), "port": port, "socket": socket_path, "url": url,
            "startedAt": time.time()}
    if token:  # the registry is per user and the token is on the command line already; lets kioskctl --name reach it
        data["token"] = token
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def unregister_instance(name: str) -> None:
    try:
        path = registry_path(name)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("pid") == os.getpid():
                path.unlink()
    except OSError:
        pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform.startswith("win"):
        import ctypes

        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # an exited child nobody has reaped yet (zombie) still answers kill(0)
        with open(f"/proc/{pid}/stat", "rb") as f:
            return f.read().rpartition(b")")[2].split()[0] != b"Z"
    except (OSError, IndexError):
        return True


def list_instances(ping: bool = True, timeout: float = 1.0, prune: bool = True) -> list[dict]:
    """All registry entries with a ``running`` flag: pid alive and, when a control channel exists,
    the API answering with its own name/pid (so a reused pid after a crash or reboot never counts).
    Stale entries (dead process, or API not answering) are deleted when ``prune`` is set."""
    out = []
    for path in sorted(runtime_dir().glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict) or not data.get("name"):  # not a browser registration
            continue
        pid = int(data.get("pid") or 0)
        process_alive = pid_alive(pid)
        alive = process_alive
        status = None
        if process_alive and ping and (data.get("port") or data.get("socket")):
            try:
                status = request("GET", "/api/status", None, port=data.get("port"), socket_path=data.get("socket"),
                                 token=data.get("token"), timeout=timeout)
            except Exception as exc:  # noqa: BLE001
                status = {"ok": False, "error": str(exc)}
            alive = bool(status.get("ok")) and status.get("pid") == pid and status.get("name") == data.get("name")
        data["running"] = alive
        data["status"] = status
        data["file"] = str(path)
        # Prune only when the process is gone, or when the API proves the pid now belongs to something
        # else. A ping timeout on a busy device must not throw away a live registration.
        identity_mismatch = bool(status and status.get("ok") and not alive)
        if prune and pid != os.getpid() and (not process_alive or identity_mismatch):
            try:
                path.unlink()
                data["pruned"] = True
            except OSError:
                pass
        out.append(data)
    return out


def find_instance(name: str) -> dict | None:
    for inst in list_instances(ping=True, timeout=2.0):
        if inst.get("name") == name and inst.get("running"):
            return inst
    return None


# ------------------------------------------------------------------------------------------
# Weston app-id mapping (what app-ids the compositor knows about)
# ------------------------------------------------------------------------------------------
WESTON_INI_CANDIDATES = (
    "$XDG_CONFIG_HOME/weston/weston.ini",
    "$XDG_CONFIG_HOME/weston.ini",
    "~/.config/weston.ini",
    "/etc/xdg/weston/weston.ini",
)


def read_weston_app_ids(paths=None) -> list[dict]:
    """Parse [output] sections of weston.ini: name, mode and the app-ids (``app-ids=id[x;y;w;h], ...``
    as used by kiosk shells that pin app-ids to outputs). Returns one dict per app-id with the file it came from."""
    rows = []
    for raw in paths or WESTON_INI_CANDIDATES:
        path = pathlib.Path(os.path.expandvars(os.path.expanduser(raw)))
        if "$" in str(path) or not path.is_file():
            continue
        section, name, mode, app_ids = None, None, None, []

        def flush(name, mode, app_ids, path):
            for entry in app_ids:
                ident, _, geom = entry.partition("[")
                rows.append({"output": name, "mode": mode, "appId": ident.strip(), "geometry": ("[" + geom) if geom else "",
                             "file": str(path)})

        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("["):
                if section == "output":
                    flush(name, mode, app_ids, path)
                section, name, mode, app_ids = line.strip("[]").strip().lower(), None, None, []
                continue
            if section != "output" or "=" not in line:
                continue
            key, value = (x.strip() for x in line.split("=", 1))
            if key == "name":
                name = value
            elif key == "mode":
                mode = value
            elif key == "app-ids":
                app_ids = [v.strip() for v in value.split(",") if v.strip()]
        if section == "output":
            flush(name, mode, app_ids, path)
        if rows:
            break  # first config found wins (same precedence Weston uses)
    return rows


def format_app_ids(rows: list[dict], running: dict[str, dict], searched=None) -> str:
    if not rows:
        return "no weston.ini with [output] app-ids found (looked in: " + ", ".join(searched or WESTON_INI_CANDIDATES) + ")"
    lines = [f"from {rows[0]['file']}"]
    for r in rows:
        inst = running.get(r["appId"])
        state = f"RUNNING {inst.get('name')} pid {inst['pid']} port {inst.get('port') or '-'}" if inst else "free"
        lines.append(f"{r['output'] or '?':10s} {r['mode'] or '':12s} {r['appId']:36s} {r['geometry']:16s} {state}")
    return "\n".join(lines)


# ------------------------------------------------------------------------------------------
# Minimal HTTP parsing (Qt-free, unit-tested)
# ------------------------------------------------------------------------------------------
MAX_CHUNKED_DRAIN = 1 << 20  # bytes of a refused chunked body read before answering anyway


def parse_http_request(buf: bytes):
    """Return (method, path, headers, body) once a full request is buffered, else None."""
    head_end = buf.find(b"\r\n\r\n")
    if head_end < 0:
        return None
    head = buf[:head_end].decode("iso-8859-1", "replace")
    lines = head.split("\r\n")
    try:
        method, target, _version = lines[0].split(" ", 2)
    except ValueError:
        return ("BAD", "", {}, b"")
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    body_start = head_end + 4
    if headers.get("transfer-encoding"):  # refused later (411), but only once all of it has arrived
        rest = buf[body_start:]
        if not (rest.startswith(b"0\r\n\r\n") or b"\r\n0\r\n\r\n" in rest) and len(rest) < MAX_CHUNKED_DRAIN:
            return None
        return (method.upper(), target.split("?", 1)[0], headers, b"")
    length = int(headers.get("content-length", "0") or 0)
    if len(buf) < body_start + length:
        return None
    path = target.split("?", 1)[0]
    return (method.upper(), path, headers, buf[body_start : body_start + length])


def http_response(status: int, payload: dict) -> bytes:
    reason = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed",
              411: "Length Required", 500: "Internal Server Error"}.get(status, "OK")
    body = json.dumps(payload).encode("utf-8")
    head = (
        f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n"
        "Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


# ------------------------------------------------------------------------------------------
# Server (Qt)
# ------------------------------------------------------------------------------------------
class ControlServer:
    """Serves the API over TCP (QTcpServer) and/or a Unix socket (QLocalServer)."""

    def __init__(self, window, cfg):
        from PySide6.QtNetwork import QHostAddress, QLocalServer, QTcpServer

        self.window = window
        self.cfg = cfg
        self.token = getattr(cfg, "control_token", None)
        self._buffers: dict[int, bytes] = {}
        self._subscribers: list = []
        self._events: list[tuple[int, str, dict]] = []  # replay buffer (seq, name, data)
        self._seq = 0
        from PySide6.QtCore import QTimer

        self._ping = QTimer(window)
        self._ping.setInterval(15000)
        self._ping.timeout.connect(self._heartbeat)
        self._ping.start()
        self._tcp = None
        self._local = None
        port = getattr(cfg, "control_port", None)
        self.port = None
        if port is not None:
            self._tcp = QTcpServer(window)
            if not self._tcp.listen(QHostAddress.SpecialAddress.LocalHost, int(port)):
                raise RuntimeError(f"control port {port} unavailable: {self._tcp.errorString()}")
            self._tcp.newConnection.connect(lambda: self._accept(self._tcp.nextPendingConnection()))
            self.port = int(self._tcp.serverPort())  # the real port when 0 (any free port) was requested
            log.info("control API listening on http://127.0.0.1:%d/api/", self.port)
        sock = getattr(cfg, "control_socket", None)
        if sock:
            QLocalServer.removeServer(sock)
            self._local = QLocalServer(window)
            self._local.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
            if not self._local.listen(sock):
                raise RuntimeError(f"control socket {sock} unavailable: {self._local.errorString()}")
            self._local.newConnection.connect(lambda: self._accept(self._local.nextPendingConnection()))
            log.info("control API listening on unix socket %s", sock)

    # ---- connection handling ---------------------------------------------------------
    def _accept(self, conn) -> None:
        if conn is None:
            return
        self._buffers[id(conn)] = b""
        conn.readyRead.connect(lambda c=conn: self._on_ready_read(c))
        conn.disconnected.connect(lambda c=conn: self._buffers.pop(id(c), None))

    def _on_ready_read(self, conn) -> None:
        self._buffers[id(conn)] = self._buffers.get(id(conn), b"") + bytes(conn.readAll())
        parsed = parse_http_request(self._buffers[id(conn)])
        if parsed is None:
            return
        method, path, headers, body = parsed
        query = {}
        raw_target = self._buffers[id(conn)].split(b"\r\n", 1)[0].decode("iso-8859-1", "replace")
        if "?" in raw_target:
            from urllib.parse import parse_qs

            query = {k: v[-1] for k, v in parse_qs(raw_target.split(" ")[1].split("?", 1)[1]).items()}
        if path == "/api/health" and method == "GET":  # no token: for monitors; answering = the UI thread is alive
            self._reply(conn, 200, {"ok": True, "state": self.window.status_dict()["state"]})
            return
        if self.token:
            given = headers.get("x-kioskium-token") or headers.get("authorization", "").removeprefix("Bearer ").strip()
            if given != self.token:
                self._reply(conn, 401, {"ok": False, "error": "bad token"})
                return
        if headers.get("transfer-encoding"):  # chunked: never read as an empty body
            self._reply(conn, 411, {"ok": False, "error": "send the body with a Content-Length header (chunked bodies are not supported)"})
            return
        try:
            payload = json.loads(body.decode("utf-8")) if body.strip() else {}
            if not isinstance(payload, dict):
                raise ValueError("JSON object expected")
            payload = {**query, **payload}
        except ValueError as exc:
            self._reply(conn, 400, {"ok": False, "error": f"bad JSON: {exc}"})
            return
        try:
            self._dispatch(conn, method, path, payload)
        except ValueError as exc:  # invalid argument, e.g. an unknown kioskium: page
            self._reply(conn, 400, {"ok": False, "error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            log.exception("control command failed")
            self._reply(conn, 500, {"ok": False, "error": str(exc)})

    def _reply(self, conn, status: int, payload: dict) -> None:
        conn.write(http_response(status, payload))
        if hasattr(conn, "disconnectFromHost"):
            conn.disconnectFromHost()
        else:
            conn.disconnectFromServer()

    def _pane(self, pane: int):
        """The KioskWindow for pane 1 (main) or 2 (second), or None if it does not exist."""
        return self.window if pane != 2 else self.window.second_pane

    # ---- events (Server-Sent Events) ------------------------------------------------------
    def publish(self, name: str, data: dict | None = None) -> None:
        """Record an event and push it to every connected /api/events client."""
        self._seq += 1
        entry = (self._seq, name, data or {})
        self._events.append(entry)
        del self._events[:-200]
        chunk = _sse_chunk(*entry)
        for conn in list(self._subscribers):
            try:
                conn.write(chunk)
            except Exception:  # noqa: BLE001
                self._subscribers.remove(conn)

    def _subscribe(self, conn, since: int) -> None:
        conn.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                   b"Access-Control-Allow-Origin: *\r\nConnection: keep-alive\r\n\r\n")
        conn.write(b": kioskium events\n\n")
        for seq, name, data in self._events:
            if seq > since:
                conn.write(_sse_chunk(seq, name, data))
        self._subscribers.append(conn)
        conn.disconnected.connect(lambda c=conn: c in self._subscribers and self._subscribers.remove(c))
        log.info("control: event subscriber connected (%d total)", len(self._subscribers))

    def _heartbeat(self) -> None:
        for conn in list(self._subscribers):
            try:
                conn.write(b": ping\n\n")
            except Exception:  # noqa: BLE001
                self._subscribers.remove(conn)

    # ---- commands -------------------------------------------------------------------------
    def _dispatch(self, conn, method: str, path: str, p: dict) -> None:
        w = self.window
        if path == "/api/status" and method == "GET":
            self._reply(conn, 200, w.status_dict())
        elif path == "/api/events" and method == "GET":
            self._buffers.pop(id(conn), None)
            self._subscribe(conn, int(p.get("since") or 0))
        elif method != "POST":
            self._reply(conn, 405, {"ok": False, "error": "POST required"})
        elif path == "/api/navigate":
            url = p.get("url")
            if not url:
                self._reply(conn, 400, {"ok": False, "error": "url required"})
                return
            pane = int(p.get("pane") or 1)
            log.info("control: navigate pane %d -> %s", pane, url)
            if pane == 2:
                w.split(url)  # re-points the second pane, or creates it
            else:
                w.navigate(url)
            self._reply(conn, 200, {"ok": True, "pane": pane})
        elif path == "/api/reload":
            pane = int(p.get("pane") or 1)
            log.info("control: reload pane %d", pane)
            target = self._pane(pane)
            if target is None:
                self._reply(conn, 400, {"ok": False, "error": "no second pane"})
                return
            target.reload()
            self._reply(conn, 200, {"ok": True, "pane": pane})
        elif path == "/api/split":
            url = p.get("url")
            if not url:
                self._reply(conn, 400, {"ok": False, "error": "url required"})
                return
            log.info("control: split %s", url)
            w.split(url, p.get("orientation"), p.get("ratio"))
            self._reply(conn, 200, {"ok": True})
        elif path == "/api/unsplit":
            log.info("control: unsplit")
            w.unsplit()
            self._reply(conn, 200, {"ok": True})
        elif path == "/api/devtools":
            action = p.get("action", "toggle")
            log.info("control: devtools %s", action)
            if action == "toggle":
                w.toggle_devtools()
            else:
                w.set_devtools_visible(action == "open")
            self._reply(conn, 200, {"ok": True, "devtools": bool(w.devtools is not None and w.devtools.isVisible())})
        elif path == "/api/fps":
            enabled = p.get("enabled", "toggle")
            if enabled == "toggle":
                enabled = not bool(getattr(w.cfg, "fps", False))
            log.info("control: fps %s", enabled)
            w.set_fps(bool(enabled))
            self._reply(conn, 200, {"ok": True, "fps": bool(enabled)})
        elif path == "/api/screenshot":
            target = p.get("path")
            if not target:
                self._reply(conn, 400, {"ok": False, "error": "path required"})
                return
            log.info("control: screenshot %s", target)
            w.screenshot_to(target)
            self._reply(conn, 200, {"ok": True, "path": target})
        elif path == "/api/js":
            code = p.get("code")
            if not code:
                self._reply(conn, 400, {"ok": False, "error": "code required"})
                return
            pane = int(p.get("pane") or 1)
            target = self._pane(pane)
            if target is None:
                self._reply(conn, 400, {"ok": False, "error": "no second pane"})
                return
            log.info("control: js pane %d: %s", pane, code[:80])

            def done(raw, c=conn):
                outcome = parse_js_result(raw)
                if outcome is None:  # the page's CSP forbids eval: run the code directly (scalars only)
                    target.page.runJavaScript(
                        code, lambda r: self._reply(c, 200, {"ok": True, "pane": pane, "result": _jsonable(r)}))
                elif "error" in outcome:
                    self._reply(c, 500, {"ok": False, "pane": pane, "error": outcome["error"]})
                else:
                    self._reply(c, 200, {"ok": True, "pane": pane, "result": outcome["value"]})

            target.page.runJavaScript(js_wrapper(code), done)
        elif path == "/api/quit":
            log.info("control: quit")
            self._reply(conn, 200, {"ok": True})
            from PySide6.QtCore import QTimer

            QTimer.singleShot(50, w._quit)
        else:
            self._reply(conn, 404, {"ok": False, "error": f"unknown endpoint {path}"})


def _sse_chunk(seq: int, name: str, data: dict) -> bytes:
    return f"id: {seq}\nevent: {name}\ndata: {json.dumps(data)}\n\n".encode("utf-8")


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return str(value)


# PySide6 only passes strings, numbers and booleans back from runJavaScript (arrays, objects, null and
# errors all arrive as ""), so /api/js lets the page serialise the result itself. The code runs through
# a global (indirect) eval, keeping the "value of the last statement" semantics of runJavaScript.
_JS_WRAPPER = """(function () {
  var r;
  try { r = (0, eval)(%s); }
  catch (e) {
    if (e instanceof EvalError) return '{"csp": true}';
    return JSON.stringify({error: String(e && e.stack ? String(e).split('\\n')[0] : e)});
  }
  if (r === undefined) return '{"value": null}';
  if (typeof r === 'function' || typeof r === 'symbol' || (typeof Node !== 'undefined' && r instanceof Node)
      || (r && typeof r.then === 'function')) return JSON.stringify({value: String(r)});
  try { var s = JSON.stringify({value: r}); if (s !== undefined) return s; } catch (e) {}
  return JSON.stringify({value: String(r)});
})()"""


def js_wrapper(code: str) -> str:
    """JavaScript that evaluates ``code`` and returns ``{"value": ...}`` / ``{"error": ...}`` as JSON text."""
    return _JS_WRAPPER % json.dumps(code)


def parse_js_result(raw) -> dict | None:
    """Decode what js_wrapper() returned: {"value": ...} or {"error": "..."}; None if the page's
    Content-Security-Policy blocked eval (the caller then runs the code directly)."""
    try:
        data = json.loads(raw) if isinstance(raw, str) and raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return {"error": "no result from the page (it may be navigating)"}
    if data.get("csp"):
        return None
    if "error" in data:
        return {"error": f"JavaScript error: {data['error']}"}
    return {"value": data.get("value")}


# ------------------------------------------------------------------------------------------
# Client (Qt-free): used by `kioskctl` and usable from any Python
# ------------------------------------------------------------------------------------------
def connection(*, port: int | None = None, socket_path: str | None = None, timeout: float | None = 10.0,
               host: str = "127.0.0.1"):
    """An http.client connection to a control channel: TCP (an instance on 127.0.0.1, or the daemon on any host)
    or a Unix socket."""
    import http.client

    if socket_path:
        import socket as _socket

        class _UnixConn(http.client.HTTPConnection):
            def connect(self):  # noqa: D401
                self.sock = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
                self.sock.settimeout(timeout)
                self.sock.connect(socket_path)

        return _UnixConn("localhost", timeout=timeout)
    if port:
        return http.client.HTTPConnection(host, int(port), timeout=timeout)
    raise ValueError("port or socket_path required")


def request(method: str, path: str, payload: dict | None, *, port: int | None = None, socket_path: str | None = None,
            token: str | None = None, timeout: float = 10.0, host: str = "127.0.0.1") -> dict:
    conn = connection(port=port, socket_path=socket_path, timeout=timeout, host=host)
    body = json.dumps(payload or {}).encode("utf-8") if method == "POST" else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Kioskium-Token"] = token
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    try:
        parsed = json.loads(data.decode("utf-8"))
    except ValueError:
        parsed = {"ok": False, "error": f"non-JSON response ({resp.status})", "raw": data[:200].decode("utf-8", "replace")}
    if resp.status != 200 and "error" not in parsed:
        parsed["error"] = f"HTTP {resp.status}"
    return parsed


def iter_events(*, port: int | None = None, socket_path: str | None = None, token: str | None = None, since: int = 0,
                host: str = "127.0.0.1", path: str = "/api/events"):
    """Yield (seq, name, data) from an event stream (/api/events) until the connection closes."""
    conn = connection(port=port, socket_path=socket_path, timeout=None, host=host)
    headers = {"Accept": "text/event-stream"}
    if token:
        headers["X-Kioskium-Token"] = token
    conn.request("GET", f"{path}?since={since}", headers=headers)
    resp = conn.getresponse()
    if resp.status != 200:
        raise OSError(f"HTTP {resp.status}: {resp.read()[:200].decode('utf-8', 'replace')}")
    seq, name, data = 0, None, ""
    while True:
        line = resp.readline()
        if not line:
            break
        line = line.decode("utf-8", "replace").rstrip("\r\n")
        if line.startswith("id:"):
            seq = int(line[3:].strip() or 0)
        elif line.startswith("event:"):
            name = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
        elif line == "" and name:
            try:
                yield seq, name, json.loads(data or "{}")
            except ValueError:
                yield seq, name, {"raw": data}
            name, data = None, ""


def _ctl_events(port, sock, token, since, count, host: str = "127.0.0.1", path: str = "/api/events") -> int:
    n = 0
    try:
        for seq, name, data in iter_events(port=port, socket_path=sock, token=token, since=since, host=host, path=path):
            print(json.dumps({"seq": seq, "event": name, **data}, separators=(",", ":")))
            sys.stdout.flush()
            n += 1
            if count and n >= count:
                break
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(json.dumps({"ok": False, "error": f"cannot connect: {exc}"}))
        return 2
    return 0


_OPEN_REFUSED = {"app_id": "--app-id", "name": "--name"}  # kioskctl open's own options
OPEN_WAIT = 60.0  # seconds open and restart wait for the browser's control API by default


def _already_running(name: str) -> str:
    return (f"{name} is already running. Change its page: kioskctl navigate -n {name} URL; restart it: "
            f"kioskctl restart -n {name}; or open another browser with its own -n NAME")


def _ctl_open(a, browser_args: list[str]) -> int:
    """`kioskctl open` without a daemon: start a browser in the background, wait until its control API answers."""
    import subprocess

    from kioskium.cli import browser_identity
    from kioskium.daemon import default_kiosk_command, option_dest

    name, identity = browser_identity(a.name, a.app_id)
    if find_instance(name):
        return _ctl_fail(a.json, _already_running(name), 3)
    if {option_dest(x) for x in browser_args} & {"no_control"}:
        return _ctl_fail(a.json, "--no-control: kioskctl could not reach the browser it opens", 2)

    # open means a new browser: --fail-if-running instead of handing the URL to one started meanwhile.
    cmd = default_kiosk_command() + browser_args + identity + ["--fail-if-running"]
    if a.token:
        cmd += [f"--control-token={a.token}"]  # '=' form: a token may start with '-'
    if a.url:
        cmd.append(a.url)

    # The browser outlives kioskctl: its own session / process group, output in a file that starts over each time.
    log_dir = runtime_dir() / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / (name.translate(_SAFE_NAME) + ".log")
    kwargs: dict = {}
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    started = time.time()
    with open(log_path, "wb") as out:
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, **kwargs)
        except OSError as exc:
            return _ctl_fail(a.json, f"cannot start {cmd[0]}: {exc}", 2)

    base = {"name": name, "pid": proc.pid, "daemon": False, "log": str(log_path)}
    deadline = time.monotonic() + max(0.0, a.wait)
    while True:
        code = proc.poll()
        if code is not None:
            try:
                tail = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-10:]
            except OSError:
                tail = []
            if code == 3:
                return _open_report(a, {"ok": False, "error": _already_running(name), "exitCode": code, **base}, 3)
            return _open_report(a, {"ok": False, "error": f"kiosk exited with code {code}", "exitCode": code,
                                    **base, "output": tail}, 1)
        try:
            reg = json.loads(registry_path(name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reg = None
        # Matched by pid, or by start time when a launcher (venv python.exe on Windows) runs the browser as a child.
        if reg and (reg.get("pid") == proc.pid or float(reg.get("startedAt") or 0) >= started - 1) \
                and (reg.get("port") or reg.get("socket")):
            try:
                status = request("GET", "/api/status", None, port=reg.get("port"), socket_path=reg.get("socket"),
                                 token=a.token or reg.get("token"), timeout=2.0)
            except OSError:
                status = {}
            if status.get("ok"):
                return _open_report(a, {"ok": True, **base, "appId": reg.get("appId"), "pid": reg.get("pid"),
                                        "port": reg.get("port"), "socket": reg.get("socket"),
                                        "url": status.get("targetUrl") or status.get("url"), "status": status}, 0)
        if time.monotonic() >= deadline:
            if a.wait <= 0:
                return _open_report(a, {"ok": True, **base, "url": a.url, "ready": False}, 0)
            return _open_report(a, {"ok": False, "error": f"started, but the control API did not answer within {a.wait:g} s "
                                                          "(still starting? see the log)", **base}, 1)
        time.sleep(0.2)


def _open_report(a, result: dict, code: int) -> int:
    """open/restart result: one line of text (or --json), errors on stderr with the browser's last output."""
    if a.json:
        print(json.dumps(result, separators=(",", ":")))
    elif result.get("ok"):
        if result.get("daemon"):
            via = "daemon"
        elif result.get("port"):
            via = f"port {result['port']}"
        else:
            via = result.get("socket") or "not ready yet"
        verb = "restarted" if a.command == "restart" else "opened"
        print(f"{verb} {result['name']}  pid {result.get('pid') or '-'}  {via}  {result.get('url') or ''}".rstrip())
    else:
        print(result["error"], file=sys.stderr)
        for line in result.get("output") or []:
            print("  " + line, file=sys.stderr)
        if result.get("log"):
            print(f"log: {result['log']}", file=sys.stderr)
    return code


_GLOBAL_VALUE_OPTIONS = {"--port", "--socket", "-n", "--name", "--token", "--timeout", "--daemon-url"}


def _split_serve(argv: list[str]) -> tuple[list[str], list[str]] | None:
    """(global options, serve options) when the command is serve, else None. serve has its own parser, so the
    global options before it are picked out here."""
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "serve":
            return argv[:i], argv[i + 1:]
        if not token.startswith("-") or token == "--":
            return None
        i += 2 if token in _GLOBAL_VALUE_OPTIONS else 1
    return None


def ctl_main(argv: list[str]) -> int:
    """Entry point for `kioskctl ...` (and `kiosk ctl ...`)."""
    import argparse

    split = _split_serve(argv)
    if split is not None:
        before, after = split
        g = argparse.ArgumentParser(prog="kioskctl", usage="kioskctl [--token TOKEN] serve [options]")
        g.add_argument("--token")
        ga, unknown = g.parse_known_args(before)
        if unknown:
            g.error(f"{unknown[0]} does not apply to serve (see kioskctl serve --help)")
        from kioskium.daemon import serve_main

        return serve_main((["--token", ga.token] if ga.token else []) + after)

    p = argparse.ArgumentParser(
        prog="kioskctl", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Open, control and close kioskium browsers.\n\n"
                    "When a daemon runs on this machine (kioskctl serve), kioskctl goes through it, so the browsers it\n"
                    "opens are supervised; otherwise kioskctl starts them itself. A browser is chosen by its name (-n);\n"
                    "with only one running, -n can be left out.",
        epilog="Reference with examples: docs/KIOSKCTL.md")
    target_help = "which browser, by name (see kioskctl list); may also go after the command"
    p.add_argument("-n", "--name", help=target_help)
    p.add_argument("--port", type=int, help="talk to the browser on this control port instead")
    p.add_argument("--socket", help="talk to the browser on this control Unix socket instead (Linux)")
    p.add_argument("--daemon-url", metavar="URL",
                   help="use the daemon at URL, e.g. 10.0.0.5:7000 (default: env KIOSKIUM_DAEMON_URL, else the one on "
                        "this machine if it runs)")
    p.add_argument("--token", default=os.environ.get("KIOSKIUM_TOKEN"), help="API token (env KIOSKIUM_TOKEN)")
    p.add_argument("--timeout", type=float, default=10.0, help="seconds to wait for an answer (default 10)")
    p.add_argument("--json", action="store_true", help="machine output: exactly one compact JSON document on stdout")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")
    # -n/--name may also follow the command; SUPPRESS keeps a subcommand from resetting a global -n to None
    target = argparse.ArgumentParser(add_help=False)
    target.add_argument("-n", "--name", default=argparse.SUPPRESS, help=target_help)

    sub.add_parser("serve", help="run the daemon: browsers opened and supervised over REST (kioskctl serve --help)")
    s = sub.add_parser(
        "open", help="open a browser; browser options go after '--'", formatter_class=argparse.RawDescriptionHelpFormatter,
        usage="kioskctl open [URL] [--app-id ID] [-n NAME] [--restart POLICY] [--wait SEC] [-- BROWSER OPTIONS]",
        description="Open a browser. When a daemon runs (kioskctl serve, or --daemon-url), it starts and supervises\n"
                    "the browser; otherwise kioskctl starts it in the background.",
        epilog="browser options:\n  anything after '--' goes to the browser, e.g. -- --windowed --loading-status (see kiosk --help)\n\n"
               "examples:\n"
               "  kioskctl open kioskium:test\n"
               "  kioskctl open http://localhost:8080/ --app-id left-screen\n"
               "  kioskctl open http://localhost:8080/menu --app-id left-screen -n menu -- --loading-status")
    s.add_argument("url", nargs="?", metavar="URL", help="page to show (default: the loading screen, waiting for kioskctl navigate)")
    s.add_argument("--app-id", metavar="ID", help="which output the window goes to on Weston (weston.ini app-ids=...; "
                                                 "default: kioskium)")
    s.add_argument("-n", "--name", default=argparse.SUPPRESS, help="what later commands call it (default: the app-id)")
    s.add_argument("--restart", choices=("no", "on-failure", "always"), metavar="POLICY",
                   help="no, on-failure or always (default: the daemon's, see serve --restart); needs a daemon")
    s.add_argument("--wait", type=float, default=OPEN_WAIT, metavar="SEC",
                   help=f"give up waiting after SEC seconds if it is not ready yet; the browser keeps running "
                        f"(default {OPEN_WAIT:g}; 0 = return once it has started)")
    s = sub.add_parser("close", aliases=["quit", "exit"], parents=[target],
                       help="close a browser: it exits, and the daemon forgets it")
    s.add_argument("--all", action="store_true", help="close every browser")
    s = sub.add_parser("restart", parents=[target], help="restart a browser the daemon opened")
    s.add_argument("--wait", type=float, default=OPEN_WAIT, metavar="SEC",
                   help=f"give up waiting after SEC seconds if it is not ready yet; the browser keeps running "
                        f"(default {OPEN_WAIT:g})")
    sub.add_parser("list", help="the browsers: running ones, and the daemon's stopped or failed ones")
    sub.add_parser("status", parents=[target],
                   help="a browser's state; with no browser named or running, the daemon's")
    pane_help = "1 = main page (default), 2 = second pane of a split"
    s = sub.add_parser("navigate", help="load a page (through the loading screen)", parents=[target])
    s.add_argument("url")
    s.add_argument("--pane", type=int, default=1, choices=(1, 2), help=pane_help)
    s = sub.add_parser("reload", help="reload the page", parents=[target])
    s.add_argument("--pane", type=int, default=1, choices=(1, 2), help=pane_help)
    s = sub.add_parser("split", help="show a second page in the same window", parents=[target])
    s.add_argument("url")
    s.add_argument("--orientation", choices=("horizontal", "vertical"))
    s.add_argument("--ratio", type=int)
    sub.add_parser("unsplit", help="remove the second pane", parents=[target])
    s = sub.add_parser("devtools", help="Chromium inspector under the page", parents=[target])
    s.add_argument("action", nargs="?", default="toggle", choices=("open", "close", "toggle"))
    s = sub.add_parser("fps", help="frame-rate overlay in every pane", parents=[target])
    s.add_argument("action", nargs="?", default="toggle", choices=("on", "off", "toggle"))
    s = sub.add_parser("screenshot", help="save the window as PNG (on the browser's machine)", parents=[target])
    s.add_argument("path")
    s = sub.add_parser("js", help="run JavaScript in the page and print the result", parents=[target])
    s.add_argument("code")
    s.add_argument("--pane", type=int, default=1, choices=(1, 2), help=pane_help)
    s = sub.add_parser("events", parents=[target],
                       help="print a browser's events (with no browser named or running, the daemon's); Ctrl+C stops")
    s.add_argument("--since", type=int, default=0)
    s.add_argument("--count", type=int, default=0, help="stop after N events")
    # `open URL -- --windowed --loading-status`: everything after '--' is for the browser
    browser_args: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        head = p.parse_known_args(argv[:i])[0] if "open" in argv[:i] else None
        if head is not None and head.command == "open":
            argv, browser_args = argv[:i], argv[i + 1:]
    a = p.parse_args(argv)
    a.command = {"quit": "close", "exit": "close"}.get(a.command, a.command)
    a.name = getattr(a, "name", None)
    if (a.port or a.socket) and a.daemon_url:
        p.error("--port/--socket talk to a browser directly; leave out --daemon-url")
    if a.command == "open":
        from kioskium.daemon import option_dest

        dests = {option_dest(x) for x in browser_args}
        for dest, flag in _OPEN_REFUSED.items():
            if dest in dests:
                return _ctl_fail(a.json, f"put {flag} before the '--': kioskctl open [URL] {flag} ... -- [browser options]", 2)
    try:
        return _Ctl(p, a, browser_args).run()
    except _Unreachable as exc:
        return _ctl_fail(a.json, str(exc), 2)


def _browser_command(a) -> tuple[str, dict]:
    """HTTP method and JSON body of a browser command (status, navigate, ... quit)."""
    method, payload = "POST", {}
    if a.command == "status":
        method = "GET"
    elif a.command == "navigate":
        payload = {"url": a.url, "pane": a.pane}
    elif a.command == "reload":
        payload = {"pane": a.pane}
    elif a.command == "split":
        payload = {k: v for k, v in {"url": a.url, "orientation": a.orientation, "ratio": a.ratio}.items() if v is not None}
    elif a.command == "devtools":
        payload = {"action": a.action}
    elif a.command == "fps":
        payload = {"enabled": {"on": True, "off": False}.get(a.action, "toggle")}
    elif a.command == "screenshot":
        payload = {"path": a.path}
    elif a.command == "js":
        payload = {"code": a.code, "pane": a.pane}
    return method, payload


def _ctl_print(as_json: bool, result: dict) -> int:
    print(json.dumps(result, separators=(",", ":")) if as_json else json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


# ------------------------------------------------------------------------------------------
# Routing: a browser directly (--port/--socket, or the registry), or through the daemon
# ------------------------------------------------------------------------------------------
DEFAULT_DAEMON_URL = "http://127.0.0.1:7000"
_ACTIVE = ("starting", "running", "restarting")


class _Unreachable(Exception):
    pass


def daemon_address(url: str | None = None) -> tuple[str, int]:
    """(host, port) of the daemon from URL, env KIOSKIUM_DAEMON_URL or KIOSKIUM_DAEMON_PORT, else 127.0.0.1:7000.
    Accepts http://host:port, host:port or a bare port."""
    from urllib.parse import urlsplit

    url = url or os.environ.get("KIOSKIUM_DAEMON_URL")
    if not url:
        return "127.0.0.1", int(os.environ.get("KIOSKIUM_DAEMON_PORT") or 7000)
    if url.isdigit():
        return "127.0.0.1", int(url)
    parts = urlsplit(url if "//" in url else "http://" + url)
    if parts.scheme != "http":
        raise ValueError(f"{url}: the daemon speaks plain http")
    return parts.hostname or "127.0.0.1", parts.port or 7000


class _Ctl:
    """One kioskctl command. The daemon is used when one is named (--daemon-url, KIOSKIUM_DAEMON_URL), or when the
    one on this machine answers /api/health; browsers it does not know are reached through the registry."""

    def __init__(self, p, a, browser_args: list[str]):
        self.p, self.a, self.browser_args = p, a, browser_args
        self.remote = bool(a.daemon_url or os.environ.get("KIOSKIUM_DAEMON_URL"))
        try:
            self.host, self.port = daemon_address(a.daemon_url)
        except ValueError as exc:
            p.error(str(exc))
        self._daemon: bool | None = True if self.remote else None
        self._instances: list[dict] | None = None

    # ---- the daemon ---------------------------------------------------------------------------
    @property
    def daemon(self) -> bool:
        """Whether a daemon is in use (a named one always is; the local one when it answers)."""
        if self._daemon is None:
            try:
                health = request("GET", "/api/health", None, port=self.port, host=self.host, timeout=0.5)
                self._daemon = bool(health.get("ok") and health.get("daemon"))
            except OSError:
                self._daemon = False
        return self._daemon

    def call(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        try:
            return request(method, path, payload, port=self.port, host=self.host, token=self.a.token,
                           timeout=timeout or self.a.timeout)
        except OSError as exc:
            raise _Unreachable(f"cannot connect to the daemon at http://{self.host}:{self.port}: {exc}") from exc

    def daemon_instances(self) -> list[dict]:
        if self._instances is None:
            self._instances = []
            if self.daemon:
                result = self.call("GET", "/api/instances")
                if not result.get("ok"):
                    raise _Unreachable(f"the daemon at http://{self.host}:{self.port}: {result.get('error')}")
                self._instances = result.get("instances") or []
        return self._instances

    def daemon_has(self, name: str) -> dict | None:
        return next((i for i in self.daemon_instances() if i["name"] == name), None)

    @staticmethod
    def path(name: str) -> str:
        from urllib.parse import quote

        return "/api/instances/" + quote(name, safe="")

    # ---- choosing the browser -----------------------------------------------------------------
    def browsers(self) -> list[dict]:
        """Every browser: the daemon's (state as it reports) and, unless the daemon is remote, the registry's."""
        rows = [{**i, "daemon": True} for i in self.daemon_instances()]
        if not self.remote:
            known = {r["name"] for r in rows}
            for r in list_instances(ping=True, timeout=min(self.a.timeout, 2.0)):
                if r["name"] not in known:
                    rows.append({"name": r["name"], "appId": r.get("appId"), "state": "running" if r["running"] else "stale",
                                 "pid": r.get("pid"), "url": (r.get("status") or {}).get("url") or r.get("url"),
                                 "port": r.get("port"), "socket": r.get("socket"), "daemon": False})
        return rows

    def only_browser(self) -> str | None:
        names = [b["name"] for b in self.browsers() if b["state"] in _ACTIVE]
        return names[0] if len(names) == 1 else None

    def target(self) -> str:
        name = self.a.name or self.only_browser()
        if not name:
            self.p.error(f"which browser? kioskctl {self.a.command} -n NAME (see kioskctl list)")
        return name

    # ---- commands -----------------------------------------------------------------------------
    def run(self) -> int:
        a = self.a
        if a.port or a.socket:
            if a.command in ("open", "list", "restart") or (a.command == "close" and a.all):
                self.p.error(f"--port/--socket pick one running browser; leave them out for {a.command}")
            return self.direct(a.port, a.socket, a.token)
        return getattr(self, "cmd_" + a.command, self.cmd_browser)()

    def cmd_open(self) -> int:
        a = self.a
        if not self.daemon:
            if a.restart:
                self.p.error("--restart needs the daemon (kioskctl serve); none answers at "
                             f"http://{self.host}:{self.port}")
            return _ctl_open(a, self.browser_args)
        body = {"args": self.browser_args, "wait": a.wait}
        for key, value in (("name", a.name), ("appId", a.app_id), ("url", a.url), ("restart", a.restart)):
            if value:
                body[key] = value
        return self.daemon_result(self.call("POST", "/api/instances", body, timeout=max(a.timeout, a.wait + 15)))

    def daemon_result(self, r: dict) -> int:
        inst = r.get("instance") or {}
        name = inst.get("name") or self.a.name
        if r.get("ok"):
            result = {"ok": True, "name": name, "appId": inst.get("appId"), "pid": inst.get("pid"), "daemon": True,
                      "url": inst.get("url"), "instance": inst}
            return _open_report(self.a, result, 0)
        if "already" in (r.get("error") or ""):
            return _open_report(self.a, {"ok": False, "error": _already_running(name), "instance": inst}, 3)
        return _open_report(self.a, {"ok": False, "error": r.get("error") or "failed", "instance": inst,
                                     "output": r.get("output") or []}, 1)

    def cmd_restart(self) -> int:
        name = self.target()
        if not self.daemon_has(name):
            where = "kioskctl serve" if self.daemon else f"a daemon (none answers at http://{self.host}:{self.port})"
            return _ctl_fail(self.a.json, f"{name} was not opened through the daemon, so it cannot be restarted; that "
                                          f"needs {where}. Or: kioskctl close -n {name}, then open it again", 2)
        return self.daemon_result(self.call("POST", f"{self.path(name)}/restart", {"wait": self.a.wait},
                                            timeout=max(self.a.timeout, self.a.wait + 35)))

    def cmd_close(self) -> int:
        a = self.a
        if a.all:
            names = [b["name"] for b in self.browsers() if b["state"] != "stale"]
        else:
            names = [self.target()]
        results = [self.close_one(name) for name in names]
        if a.json:
            out = results[0] if not a.all else {"ok": all(r.get("ok") for r in results), "closed": results}
            print(json.dumps(out, separators=(",", ":")))
        else:
            for r in results:
                if r.get("ok"):
                    print(f"closed {r['name']}")
                else:
                    print(r["error"], file=sys.stderr)
            if a.all and not results:
                print("no browsers to close")
        if not results:
            return 0
        if not all(r.get("ok") for r in results):
            return 2 if any(r.get("notFound") for r in results) else 1
        return 0

    def close_one(self, name: str) -> dict:
        if self.daemon_has(name):
            r = self.call("DELETE", self.path(name), timeout=max(self.a.timeout, 30))
            return {**r, "name": name}
        inst = None if self.remote else find_instance(name)
        if not inst:
            return {"ok": False, "name": name, "notFound": True, "error": f"no browser named {name} (see kioskctl list)"}
        try:
            r = request("POST", "/api/quit", {}, port=inst.get("port"), socket_path=inst.get("socket"),
                        token=inst.get("token") or self.a.token, timeout=self.a.timeout)
        except OSError as exc:
            r = {"ok": False, "error": f"{name}: cannot connect: {exc}"}
        return {**r, "name": name, "pid": inst.get("pid")}

    def cmd_list(self) -> int:
        rows = self.browsers()
        if self.a.json:
            print(json.dumps({"ok": True, "instances": rows}, separators=(",", ":")))
            return 0
        if not rows:
            print("no browsers")
            return 0
        table = [("STATE", "NAME", "APP-ID", "PID", "BY", "URL")] + [
            (r["state"], r["name"], r.get("appId") or "", str(r.get("pid") or ""), "daemon" if r["daemon"] else "local",
             r.get("url") or "") for r in rows]
        widths = [max(len(row[i]) for row in table) for i in range(5)]
        for row in table:
            print("  ".join(cell.rjust(w) if i == 3 else cell.ljust(w) for i, (cell, w) in enumerate(zip(row, widths)))
                  + "  " + row[5])
        return 0

    def cmd_status(self) -> int:
        a = self.a
        name = a.name or self.only_browser()
        if not name:
            if self.daemon:
                return _ctl_print(a.json, self.call("GET", "/api/status"))
            self.p.error("which browser? kioskctl status -n NAME (see kioskctl list)")
        inst = self.daemon_has(name)
        if inst is None:
            return self.browser(name)
        r = self.call("GET", self.path(name))
        if not r.get("ok"):
            return _ctl_print(a.json, r)
        live = r.get("status") or {}
        if inst["state"] == "running" and live.get("ok"):
            return _ctl_print(a.json, {**live, "instance": r["instance"]})
        return _ctl_print(a.json, {"ok": False, "error": f"{name} is {r['instance']['state']}", "instance": r["instance"]})

    def cmd_events(self) -> int:
        a = self.a
        name = a.name or self.only_browser()
        if not name:
            if self.daemon:
                return _ctl_events(self.port, None, a.token, a.since, a.count, host=self.host)
            self.p.error("which browser? kioskctl events -n NAME (see kioskctl list)")
        if self.daemon_has(name):
            return _ctl_events(self.port, None, a.token, a.since, a.count, host=self.host, path=f"{self.path(name)}/events")
        return self.browser(name)

    def cmd_browser(self) -> int:
        """navigate, reload, js, ...: to the browser directly when the registry has it, else through the daemon."""
        return self.browser(self.target())

    def browser(self, name: str) -> int:
        a = self.a
        inst = None if self.remote else find_instance(name)
        if inst:
            if not inst.get("port") and not inst.get("socket"):
                return _ctl_fail(a.json, f"{name} has no control channel (start it with --control-port)", 2)
            return self.direct(inst.get("port"), inst.get("socket"), inst.get("token") or a.token)
        if self.daemon_has(name):
            if a.command == "events":
                return _ctl_events(self.port, None, a.token, a.since, a.count, host=self.host,
                                   path=f"{self.path(name)}/events")
            method, payload = _browser_command(a)
            return _ctl_print(a.json, self.call(method, f"{self.path(name)}/{a.command}", payload))
        return _ctl_fail(a.json, f"no browser named {name} (see kioskctl list)", 2)

    def direct(self, port, sock, token) -> int:
        a = self.a
        if a.command == "events":
            return _ctl_events(port, sock, token, a.since, a.count)
        method, payload = _browser_command(a)
        path = "/api/quit" if a.command == "close" else f"/api/{a.command}"
        try:
            result = request(method, path, payload, port=port, socket_path=sock, token=token, timeout=a.timeout)
        except OSError as exc:
            return _ctl_fail(a.json, f"cannot connect: {exc}", 2)
        return _ctl_print(a.json, result)


def _ctl_fail(as_json: bool, message: str, code: int) -> int:
    if as_json:
        print(json.dumps({"ok": False, "error": message}, separators=(",", ":")))
    else:
        print(message, file=sys.stderr)
    return code

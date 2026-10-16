"""Stand-in for the kiosk browser in daemon tests: registers itself and serves a tiny control API.

POST /api/crash exits with code 3, POST /api/window-closed with 0 (as if closed by hand);
--fail-fast exits with code 2 before serving (like a bad option).
"""

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from kioskium import control  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--app-id")
p.add_argument("--name")
p.add_argument("--control-port", type=int)
p.add_argument("--control-socket")
p.add_argument("--control-token")
p.add_argument("--fail-fast", action="store_true")
p.add_argument("url", nargs="?", default="kioskium:idle")
a, _ = p.parse_known_args()
if a.fail_fast:
    print("kiosk: error: fail-fast requested", flush=True)
    sys.exit(2)
state = {"url": a.url}
name = a.name or a.app_id or "kioskium"  # like the real browser: the name defaults to the app-id


class H(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        if self.path == "/api/health":  # like the real browser: no token needed
            return self._send({"ok": True, "state": "idle"})
        if self.headers.get("X-Kioskium-Token") != a.control_token:
            return self._send({"ok": False, "error": "bad token"}, 401)
        self._send({"ok": True, "name": name, "appId": a.app_id, "pid": os.getpid(), "url": state["url"]})

    def do_POST(self):  # noqa: N802
        if self.headers.get("X-Kioskium-Token") != a.control_token:
            return self._send({"ok": False, "error": "bad token"}, 401)
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path == "/api/navigate":
            state["url"] = body["url"]
            self._send({"ok": True, "pane": 1})
        elif self.path == "/api/quit":
            self._send({"ok": True})
            control.unregister_instance(name)  # like the real browser on a clean quit
            threading.Timer(0.1, lambda: os._exit(0)).start()
        elif self.path == "/api/crash":
            os._exit(3)
        elif self.path == "/api/window-closed":  # the user closed the window: a clean exit the daemon did not ask for
            self._send({"ok": True})
            control.unregister_instance(name)
            threading.Timer(0.1, lambda: os._exit(0)).start()
        else:
            self._send({"ok": False, "error": "unknown"}, 404)


if a.control_socket:
    import socketserver

    class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

        def get_request(self):
            conn, _ = super().get_request()
            return conn, ("local", 0)

    if os.path.exists(a.control_socket):
        os.unlink(a.control_socket)
    server = UnixServer(a.control_socket, H)
    port = None
else:
    server = ThreadingHTTPServer(("127.0.0.1", a.control_port or 0), H)
    port = server.server_port
control.register_instance(name, port, a.control_socket, a.url, a.app_id, a.control_token)
print(f"fake kiosk {name} ready", flush=True)
server.serve_forever()

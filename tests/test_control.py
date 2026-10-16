import json
import os

from kioskium import control


def test_js_wrapper_embeds_code_as_a_js_string_literal():
    code = 'document.title + "\\\\" + \'x\'\nlast()'
    js = control.js_wrapper(code)
    assert json.dumps(code) in js  # quotes, backslashes and newlines escaped exactly once
    assert js.startswith("(function () {") and js.endswith("})()")
    assert "%s" not in js


def test_parse_js_result():
    assert control.parse_js_result('{"value": [1, {"a": null}]}') == {"value": [1, {"a": None}]}
    assert control.parse_js_result('{"value": null}') == {"value": None}
    assert control.parse_js_result('{"error": "ReferenceError: x is not defined"}') == {
        "error": "JavaScript error: ReferenceError: x is not defined"}
    assert control.parse_js_result('{"csp": true}') is None  # caller falls back to a direct call
    assert "error" in control.parse_js_result("")  # nothing came back (page navigating)
    assert "error" in control.parse_js_result("not json")


def test_parse_http_request_waits_for_full_body():
    assert control.parse_http_request(b"POST /api/x HTTP/1.1\r\nContent-Length: 5\r\n\r\nab") is None
    method, path, headers, body = control.parse_http_request(
        b"POST /api/x?y=1 HTTP/1.1\r\nContent-Length: 5\r\nX-Kioskium-Token: t\r\n\r\nabcde")
    assert (method, path, body) == ("POST", "/api/x", b"abcde")
    assert headers["x-kioskium-token"] == "t"


def test_parse_http_request_waits_for_the_end_of_a_chunked_body():
    head = b"POST /api/navigate HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
    assert control.parse_http_request(head + b"f\r\n{\"url\":\"http://x\"}") is None  # still arriving
    method, path, headers, body = control.parse_http_request(head + b"f\r\n{\"url\":\"http://x\"}\r\n0\r\n\r\n")
    assert (method, path, body) == ("POST", "/api/navigate", b"") and headers["transfer-encoding"] == "chunked"


def test_parse_http_request_get_without_body():
    parsed = control.parse_http_request(b"GET /api/status HTTP/1.1\r\nHost: x\r\n\r\n")
    assert parsed == ("GET", "/api/status", {"host": "x"}, b"")


def test_health_needs_no_token():
    class Conn:
        def __init__(self, raw):
            self.raw, self.out = raw, b""

        def readAll(self):  # noqa: N802
            return self.raw

        def write(self, data):
            self.out += data

        def disconnectFromHost(self):  # noqa: N802
            pass

    class Window:
        def status_dict(self):
            return {"ok": True, "state": "target"}

    server = object.__new__(control.ControlServer)  # without Qt: only the request handling
    server.window, server.token, server._buffers = Window(), "secret", {}

    def send(raw):
        server._buffers.clear()  # the real server drops a connection's buffer when it disconnects
        conn = Conn(raw.encode())
        server._on_ready_read(conn)
        head, body = conn.out.split(b"\r\n\r\n", 1)
        return int(head.split()[1]), json.loads(body)

    def get(path):
        return send(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n")

    assert get("/api/health") == (200, {"ok": True, "state": "target"})
    assert get("/api/status")[0] == 401
    # A chunked body (no Content-Length) is refused, not read as {} (which would navigate nowhere, open defaults, ...)
    status, body = send("POST /api/navigate HTTP/1.1\r\nX-Kioskium-Token: secret\r\nTransfer-Encoding: chunked\r\n\r\n"
                        "f\r\n{\"url\":\"http://x\"}\r\n0\r\n\r\n")
    assert status == 411 and "Content-Length" in body["error"]


def test_http_response_format():
    raw = control.http_response(200, {"ok": True})
    head, body = raw.split(b"\r\n\r\n", 1)
    assert head.startswith(b"HTTP/1.1 200 OK")
    assert b"Content-Length: %d" % len(body) in head
    assert json.loads(body) == {"ok": True}


def test_registry_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "runtime_dir", lambda: tmp_path)
    path = control.register_instance("menu", 7001, None, "http://x/", app_id="left-screen.qtwebbrowser")
    assert path.exists() and path.name == "menu.json"  # keyed by name; the app-id only places the window
    rows = control.list_instances(ping=False)
    assert rows and rows[0]["name"] == "menu" and rows[0]["appId"] == "left-screen.qtwebbrowser"
    assert rows[0]["pid"] == os.getpid() and rows[0]["running"]
    # a dead pid is reported as stale
    (tmp_path / "old.json").write_text(json.dumps({"name": "old", "pid": 999999999, "port": 1}), encoding="utf-8")
    stale = [r for r in control.list_instances(ping=False) if r["name"] == "old"]
    assert stale and not stale[0]["running"] and stale[0].get("pruned")
    assert not (tmp_path / "old.json").exists()  # stale entries are cleaned up
    assert path.exists()  # the live one is kept
    control.unregister_instance("menu")
    assert not path.exists()


def test_registry_path_sanitises_the_name(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "runtime_dir", lambda: tmp_path)
    assert control.registry_path("a/b:c").name == "a_b_c.json"


def test_live_process_is_not_pruned_on_ping_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "runtime_dir", lambda: tmp_path)
    # our own pid is alive; port 1 refuses connections -> ping fails -> keep the file, report not running
    (tmp_path / "me.json").write_text(json.dumps({"name": "me", "pid": os.getpid() + 0, "port": 1}), encoding="utf-8")
    monkeypatch.setattr(control, "pid_alive", lambda pid: True)
    monkeypatch.setattr(control, "request", lambda *a, **k: (_ for _ in ()).throw(OSError("refused")))
    rows = [r for r in control.list_instances(ping=True, timeout=0.2) if r["name"] == "me"]
    assert rows and not rows[0]["running"] and not rows[0].get("pruned")
    assert (tmp_path / "me.json").exists()


def test_read_weston_app_ids(tmp_path):
    ini = tmp_path / "weston.ini"
    ini.write_text("""[core]
shell=kiosk-shell.so

# HDMI-A-2 maps to HDMI-1
[output]
name=HDMI-A-2
app-ids=left-screen.qtwebbrowser[0;0;100%;100%]
mode=1280x800

[output]
name=HDMI-A-1
app-ids=right-screen.qtwebbrowser[0;0;100%;100%], other.app
mode=1920x1080
""", encoding="utf-8")
    rows = control.read_weston_app_ids([str(ini)])
    assert [(r["output"], r["appId"], r["geometry"]) for r in rows] == [
        ("HDMI-A-2", "left-screen.qtwebbrowser", "[0;0;100%;100%]"),
        ("HDMI-A-1", "right-screen.qtwebbrowser", "[0;0;100%;100%]"),
        ("HDMI-A-1", "other.app", ""),
    ]
    text = control.format_app_ids(rows, {"other.app": {"name": "info", "pid": 5, "port": 7000}})
    assert "RUNNING info pid 5 port 7000" in text and "free" in text


def test_registry_ignores_files_that_are_not_registrations(tmp_path, monkeypatch):
    monkeypatch.setattr(control, "runtime_dir", lambda: tmp_path)
    (tmp_path / "other.json").write_text(json.dumps({"pid": 999999999, "instances": []}), encoding="utf-8")
    assert control.list_instances(ping=False) == []
    assert (tmp_path / "other.json").exists()  # not pruned either

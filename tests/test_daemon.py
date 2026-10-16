import http.client
import json
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from kioskium import daemon

FAKE = str(Path(__file__).with_name("fake_kiosk.py"))


def test_tokens_starting_with_a_dash_are_passed_safely(monkeypatch):
    sup = daemon.Supervisor(["kiosk"], use_socket=False)
    inst = daemon.Instance("left", "http://x/", [], "no", None)
    inst.token = "-starts-with-dash"
    cmd = sup.command(inst)
    assert "--control-token=-starts-with-dash" in cmd and "-starts-with-dash" not in cmd


def test_serve_points_browser_options_to_args(capsys):
    for argv in (["--windowed"], ["--", "--windowed"]):
        with pytest.raises(SystemExit) as exc:
            daemon.serve_main(argv)
        assert exc.value.code == 2 and "browser options go in each instance's args" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        daemon.serve_main(["--no-such-thing"])
    assert "unrecognized arguments: --no-such-thing" in capsys.readouterr().err


def test_a_broken_instances_file_does_not_stop_the_daemon(caplog, tmp_path):
    bad = tmp_path / "instances.json"
    bad.write_text("[{name: oops}]", encoding="utf-8")
    with socket.socket() as busy:  # an occupied port: serve gets past the file, then fails to listen and returns
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        assert daemon.serve_main(["--port", str(busy.getsockname()[1]), "--instances", str(bad)]) == 2
    assert any("not starting the browsers in" in r.getMessage() for r in caplog.records)


def test_check_args():
    assert daemon.check_args(["--show-cursor", "--user-agent", "x", "--no-sandbox"]) is None
    assert "set by the daemon" in daemon.check_args(["--control-port", "1"])
    assert "set by the daemon" in daemon.check_args(["--app-id=x"])
    assert "set by the daemon" in daemon.check_args(["--name", "x"])
    assert "not allowed" in daemon.check_args(["--chromium-flag=--foo"])
    assert "not allowed" in daemon.check_args(["--inject-j", "x.js"])  # argparse abbreviation of --inject-js
    assert "not allowed" in daemon.check_args(["@args.txt"])
    assert daemon.check_args(["--chromium-flag=--foo", "@args.txt"], allow_unsafe=True) is None
    assert "list of strings" in daemon.check_args("--show-cursor")


@pytest.fixture
def make_api(tmp_path, monkeypatch):
    """Starts daemons on free ports that launch fake_kiosk.py; each call returns a request helper."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # registry on Windows
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # registry + sockets on Linux
    running = []

    def make(**supervisor_options):
        sup = daemon.Supervisor([sys.executable, FAKE], **supervisor_options)
        server = daemon.DaemonServer(("127.0.0.1", 0), sup, token="secret")
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
        running.append((sup, server))

        def call(method, path, payload=None, headers=None, chunked=False):
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=30)
            body = json.dumps(payload).encode() if payload is not None else None
            if chunked:  # what .NET's JsonContent and other streaming clients send: no Content-Length
                body = iter([body])
            conn.request(method, path, body=body, headers={"X-Kioskium-Token": "secret", **(headers or {})},
                         encode_chunked=chunked)
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read())

        return call

    yield make
    for sup, server in running:
        sup.shutdown()
        server.shutdown()
        server.server_close()


@pytest.fixture
def api(make_api):
    """A daemon with the default settings."""
    return make_api()


def _wait_state(call, name, want, restarts=0, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = call("GET", f"/api/instances/{name}")[1]["instance"]
        if state["state"] == want and state["restarts"] == restarts:
            return state
        time.sleep(0.1)
    raise AssertionError(f"{name} never reached {want}: {state}")


def test_start_forward_crash_restart_and_stop(api):
    status, body = api("POST", "/api/instances", {"name": "left", "appId": "left-screen", "url": "http://x/"})
    assert status == 200 and body["instance"]["state"] == "running"
    assert (body["instance"]["name"], body["instance"]["appId"]) == ("left", "left-screen")
    assert api("POST", "/api/instances", {"name": "left"})[0] == 409  # already running (by name)

    assert api("POST", "/api/instances/left/navigate", {"url": "http://y/"}) == (200, {"ok": True, "pane": 1})
    assert api("GET", "/api/instances/left")[1]["status"]["url"] == "http://y/"
    assert [i["name"] for i in api("GET", "/api/instances")[1]["instances"]] == ["left"]

    api("POST", "/api/instances/left/crash")  # exit code 3 -> on-failure restarts it
    assert _wait_state(api, "left", "running", restarts=1)["exitCode"] is None

    status, body = api("POST", "/api/instances/left/quit")  # quit through the daemon = stop, no restart
    assert status == 200 and body["instance"]["state"] == "stopped"
    assert api("POST", "/api/instances/left/navigate", {"url": "http://z/"})[0] == 409
    assert api("POST", "/api/instances/left/start")[1]["instance"]["state"] == "running"
    assert api("DELETE", "/api/instances/left")[0] == 200
    assert api("GET", "/api/instances")[1]["instances"] == []


def test_chunked_bodies_are_refused(api):
    # Read as an empty body, {"name": "left"} would start a browser called "kioskium" instead.
    status, body = api("POST", "/api/instances", {"name": "left"}, chunked=True)
    assert status == 411 and "Content-Length" in body["error"]
    assert api("GET", "/api/instances")[1]["instances"] == []


def test_a_closed_window_stays_closed_by_default(api):
    assert api("GET", "/api/status")[1]["restart"] == "on-failure"
    assert api("POST", "/api/instances", {"name": "left"})[0] == 200
    api("POST", "/api/instances/left/window-closed")  # exit code 0, not asked for by the daemon
    assert _wait_state(api, "left", "exited")["exitCode"] == 0


def test_serve_restart_always_relaunches_a_closed_window(make_api):
    api = make_api(default_restart="always")
    assert api("GET", "/api/status")[1]["restart"] == "always"
    status, body = api("POST", "/api/instances", {"name": "left"})
    assert status == 200 and body["instance"]["restart"] == "always"
    api("POST", "/api/instances/left/window-closed")
    assert _wait_state(api, "left", "running", restarts=1)["exitCode"] is None

    status, body = api("POST", "/api/instances/left/stop")  # stopping through the daemon still keeps it closed
    assert status == 200 and body["instance"]["state"] == "stopped"
    time.sleep(1.5)  # longer than the first back-off
    assert api("GET", "/api/instances/left")[1]["instance"]["state"] == "stopped"

    assert api("POST", "/api/instances", {"name": "once", "restart": "no"})[1]["instance"]["restart"] == "no"
    api("POST", "/api/instances/once/window-closed")  # the request's own policy wins
    assert _wait_state(api, "once", "exited")["restarts"] == 0


def test_serve_checks_the_restart_policy(capsys):
    with pytest.raises(SystemExit) as exc:
        daemon.serve_main(["--restart", "sometimes"])
    assert exc.value.code == 2 and "--restart" in capsys.readouterr().err


def test_two_browsers_can_share_an_app_id(api):
    for name in ("menu", "specials"):
        status, body = api("POST", "/api/instances", {"name": name, "appId": "left-screen"})
        assert status == 200 and body["instance"]["state"] == "running"
    assert api("POST", "/api/instances/specials/navigate", {"url": "http://y/"})[0] == 200
    assert api("GET", "/api/instances/menu")[1]["status"]["url"] == "kioskium:idle"  # each name reaches its own browser
    assert api("GET", "/api/instances/specials")[1]["status"]["url"] == "http://y/"


def test_instances_file_needs_distinct_names(tmp_path):
    f = tmp_path / "instances.json"
    f.write_text(json.dumps([{"appId": "left"}, {"name": "left", "appId": "other"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="same name"):
        daemon.load_instances(str(f))
    f.write_text(json.dumps([{"appId": "left"}, {"name": "menu", "appId": "left"}]), encoding="utf-8")
    assert len(daemon.load_instances(str(f))) == 2


def test_failed_first_launch_reports_output(api):
    status, body = api("POST", "/api/instances", {"name": "bad", "args": ["--fail-fast"]})
    assert status == 500 and body["instance"]["state"] == "failed"
    assert "fail-fast requested" in body["output"][-1]


def test_requests_are_checked(api):
    assert api("GET", "/api/status", headers={"X-Kioskium-Token": "wrong"})[0] == 401
    assert api("GET", "/api/status", headers={"Origin": "http://evil.example"})[0] == 403
    assert api("GET", "/api/status", headers={"Host": "evil.example"})[0] == 403  # DNS rebinding
    assert api("POST", "/api/instances", {"name": "x", "args": ["--inject-js", "a.js"]})[0] == 400
    assert api("POST", "/api/instances", {"name": 5})[0] == 400
    assert api("GET", "/api/instances/nope")[0] == 404
    assert api("GET", "/api/status")[1]["daemon"] is True


def test_health_needs_no_token(api):
    assert api("GET", "/api/health", headers={"X-Kioskium-Token": "wrong"}) == (200, {"ok": True, "daemon": True})
    assert api("GET", "/api/health", headers={"Origin": "http://evil.example"})[0] == 403
    assert api("POST", "/api/instances", {"name": "left"})[0] == 200
    assert api("GET", "/api/instances/left/health") == (200, {"ok": True, "state": "idle"})  # forwarded to the browser


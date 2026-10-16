"""kioskctl: on its own (browsers it starts itself) and through the daemon (found automatically, or --daemon-url)."""

import json
import socket
import sys
import threading
from pathlib import Path

import pytest

from kioskium import control, daemon

FAKE = str(Path(__file__).with_name("fake_kiosk.py"))


def _closed_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))  # registry on Windows
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))  # registry + sockets on Linux
    monkeypatch.delenv("KIOSKIUM_TOKEN", raising=False)
    monkeypatch.delenv("KIOSKIUM_DAEMON_URL", raising=False)
    monkeypatch.setenv("KIOSKIUM_DAEMON_PORT", str(_closed_port()))  # no daemon, whatever runs on this machine
    monkeypatch.setattr(daemon, "default_kiosk_command", lambda: [sys.executable, FAKE])
    return tmp_path


def ctl(capsys, *argv):
    """Run kioskctl --json ARGV; (exit code, parsed output)."""
    code = control.ctl_main(["--json", *argv])
    out = capsys.readouterr().out.strip()
    return code, json.loads(out) if out else None


def text(capsys, *argv):
    """Run kioskctl ARGV; (exit code, stdout, stderr)."""
    code = control.ctl_main(list(argv))
    out = capsys.readouterr()
    return code, out.out.strip(), out.err.strip()


def test_daemon_address(monkeypatch):
    monkeypatch.delenv("KIOSKIUM_DAEMON_URL", raising=False)
    monkeypatch.delenv("KIOSKIUM_DAEMON_PORT", raising=False)
    assert control.daemon_address() == ("127.0.0.1", 7000)
    assert control.daemon_address("7100") == ("127.0.0.1", 7100)
    assert control.daemon_address("device:7100") == ("device", 7100)
    assert control.daemon_address("http://10.0.0.5:7200/") == ("10.0.0.5", 7200)
    monkeypatch.setenv("KIOSKIUM_DAEMON_PORT", "7300")
    assert control.daemon_address() == ("127.0.0.1", 7300)
    with pytest.raises(ValueError):
        control.daemon_address("https://device:7000")


def test_global_token_can_go_before_serve(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(daemon, "serve_main", lambda argv: seen.append(argv) or 0)
    assert control.ctl_main(["serve", "--port", "7100"]) == 0
    assert control.ctl_main(["--token", "t", "serve", "--port", "7100"]) == 0
    assert control.ctl_main(["--token=t", "serve"]) == 0
    assert seen == [["--port", "7100"], ["--token", "t", "--port", "7100"], ["--token", "t"]]
    with pytest.raises(SystemExit):
        control.ctl_main(["--json", "serve"])
    assert "--json does not apply to serve" in capsys.readouterr().err


def test_open_without_a_daemon(runtime, capsys):
    code, out, _ = text(capsys, "open", "http://example.com/")
    try:
        assert code == 0 and out.startswith("opened kioskium  pid ") and "port " in out and out.endswith("http://example.com/")
        inst = control.find_instance("kioskium")
        assert inst["appId"] == "kioskium"  # defaults like kiosk's

        code, _, err = text(capsys, "open")
        assert code == 3 and "already running" in err and "kioskctl navigate -n kioskium" in err

        # the only browser: no -n needed
        assert ctl(capsys, "navigate", "http://example.com/next") == (0, {"ok": True, "pane": 1})
        code, st = ctl(capsys, "status")
        assert code == 0 and st["url"] == "http://example.com/next"
        code, rows = ctl(capsys, "list")
        assert [(r["name"], r["state"], r["daemon"]) for r in rows["instances"]] == [("kioskium", "running", False)]

        code, _, err = text(capsys, "restart")
        assert code == 2 and "not opened through the daemon" in err
    finally:
        assert text(capsys, "close")[:2] == (0, "closed kioskium")
    assert text(capsys, "close", "-n", "kioskium")[0] == 2
    assert text(capsys, "list")[:2] == (0, "no browsers")


def test_two_browsers_need_a_name(runtime, capsys):
    assert ctl(capsys, "open", "-n", "a")[0] == 0
    assert ctl(capsys, "open", "-n", "b", "--app-id", "a")[0] == 0  # same app-id, other name
    try:
        with pytest.raises(SystemExit):
            control.ctl_main(["navigate", "http://x/"])
        assert "which browser?" in capsys.readouterr().err
        assert ctl(capsys, "navigate", "-n", "b", "http://x/")[0] == 0
    finally:
        code, out = ctl(capsys, "close", "--all")
    assert code == 0 and sorted(r["name"] for r in out["closed"]) == ["a", "b"]


def test_open_reports_a_browser_that_exits(runtime, capsys):
    code, out = ctl(capsys, "open", "-n", "bad", "--", "--fail-fast")
    assert code == 1 and out["exitCode"] == 2 and "fail-fast requested" in out["output"][-1]
    assert Path(out["log"]).is_file()
    code, _, err = text(capsys, "open", "-n", "bad", "--", "--fail-fast")
    assert code == 1 and "fail-fast requested" in err and "log: " in err


def test_open_keeps_its_own_options_out_of_browser_args(runtime, capsys):
    code, out = ctl(capsys, "open", "--", "--app-id", "x")
    assert code == 2 and "before the '--'" in out["error"]
    with pytest.raises(SystemExit):
        control.ctl_main(["open", "--restart", "always"])  # restart policies need the daemon
    assert "needs the daemon" in capsys.readouterr().err


@pytest.fixture
def served(runtime, monkeypatch):
    """A daemon (token 'secret') on KIOSKIUM_DAEMON_PORT, so kioskctl finds it on its own; launches fake_kiosk.py."""
    sup = daemon.Supervisor([sys.executable, FAKE])
    server = daemon.DaemonServer(("127.0.0.1", 0), sup, token="secret")
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    monkeypatch.setenv("KIOSKIUM_DAEMON_PORT", str(server.server_port))
    monkeypatch.setenv("KIOSKIUM_TOKEN", "secret")
    yield f"127.0.0.1:{server.server_port}"
    sup.shutdown()
    server.shutdown()
    server.server_close()


def test_kioskctl_uses_a_running_daemon(served, capsys):
    code, out, _ = text(capsys, "open", "kioskium:test", "--app-id", "left-screen", "-n", "left", "--restart", "always",
                        "--", "--windowed")
    assert code == 0 and out.startswith("opened left  pid ") and "  daemon  kioskium:test" in out
    code, out = ctl(capsys, "list")
    inst = out["instances"][0]
    assert (inst["name"], inst["appId"], inst["daemon"], inst["args"], inst["restart"]) == (
        "left", "left-screen", True, ["--windowed"], "always")

    code, _, err = text(capsys, "open", "-n", "left")
    assert code == 3 and "already running" in err

    # browser commands; the registry has the daemon's browsers too, the daemon is the fallback
    assert ctl(capsys, "navigate", "http://example.com/")[0] == 0
    code, st = ctl(capsys, "status")
    assert code == 0 and st["url"] == "http://example.com/" and st["instance"]["restarts"] == 0  # status includes the daemon's view

    code, out, _ = text(capsys, "restart", "-n", "left")
    assert code == 0 and out.startswith("restarted left  pid ")

    assert text(capsys, "close")[:2] == (0, "closed left")
    assert ctl(capsys, "list")[1]["instances"] == []  # gone: the daemon forgot it
    code, st = ctl(capsys, "status")  # no browser left: the daemon's own status
    assert code == 0 and st["daemon"] is True


def test_daemon_events_through_kioskctl(served, capsys):
    assert ctl(capsys, "open", "-n", "ev")[0] == 0
    assert ctl(capsys, "open", "-n", "ev2")[0] == 0
    code = control.ctl_main(["events", "--count", "2"])  # two browsers, none named: the daemon's own events
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert code == 0 and [e["event"] for e in lines] == ["started", "ready"]


def test_daemon_url_and_errors(served, capsys, monkeypatch):
    monkeypatch.delenv("KIOSKIUM_TOKEN")
    code, out = ctl(capsys, "--daemon-url", served, "--token", "secret", "open", "-n", "remote")
    assert code == 0 and out["daemon"] is True
    code, out = ctl(capsys, "--daemon-url", served, "list")  # no token
    assert code == 2 and "bad token" in out["error"]
    code, out = ctl(capsys, "--daemon-url", "127.0.0.1:1", "list")
    assert code == 2 and "cannot connect to the daemon" in out["error"]
    with pytest.raises(SystemExit):
        control.ctl_main(["--daemon-url", served, "--port", "7001", "status"])

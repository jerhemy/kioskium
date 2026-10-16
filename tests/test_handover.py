import os
import sys

import pytest

from kioskium import app, control
from kioskium.cli import parse_args


@pytest.fixture
def running(monkeypatch):
    """Pretend the registry holds `entry` for the name, and record what the hand-over sends."""
    sent = []
    state = {"entry": None, "reply": {"ok": True, "pane": 1}}

    def fake_list(**_kw):
        return [state["entry"]] if state["entry"] else []

    def fake_request(method, path, payload, **kw):
        sent.append((method, path, payload, kw.get("token")))
        if isinstance(state["reply"], Exception):
            raise state["reply"]
        return state["reply"]

    monkeypatch.setattr(control, "list_instances", fake_list)
    monkeypatch.setattr(control, "request", fake_request)
    return state, sent


def _entry(**extra):
    return {"name": "info", "pid": 4242, "running": True, "socket": "/run/k/info.sock", "token": "reg-token", **extra}


def _hand_over(argv):
    return app._hand_over(parse_args(["--no-config", *argv]), argv)


def test_nothing_running_starts_a_new_browser(running):
    assert _hand_over(["--name", "info", "http://b/"]) is None


def test_same_name_gets_the_url(running, caplog):
    state, sent = running
    state["entry"] = _entry()
    assert _hand_over(["--name", "info", "--windowed", "http://b/"]) == 0
    assert sent == [("POST", "/api/navigate", {"url": "http://b/"}, "reg-token")]  # the running browser's token
    assert caplog.text.rstrip().endswith("ignored (they only apply when a browser starts): --windowed")  # not --name


def test_without_a_url_the_running_browser_is_left_alone(running):
    state, sent = running
    state["entry"] = _entry()
    assert _hand_over(["--name", "info"]) == 0 and sent == []


@pytest.mark.parametrize("entry, reply, argv", [
    (_entry(), None, ["--fail-if-running"]),                        # asked to fail instead
    (_entry(running=False), None, []),                              # alive but not answering: never start a second one
    (_entry(socket=None, port=None), None, []),                     # --no-control: nothing to hand the URL to
    (_entry(), {"ok": False, "error": "unknown page"}, []),         # the browser refused the URL
    (_entry(), OSError("connection refused"), []),                  # gone while we asked
])
def test_cases_that_exit_3(running, entry, reply, argv):
    state, _ = running
    state["entry"], state["reply"] = entry, reply or state["reply"]
    assert _hand_over(["--name", "info", *argv, "http://b/"]) == 3


def test_default_control_channel(monkeypatch):
    monkeypatch.setattr(control, "default_socket_path", lambda name: f"/run/k/{name}.sock")

    def channel(argv, platform):
        monkeypatch.setattr(sys, "platform", platform)
        args = parse_args(["--no-config", *argv])
        app._default_control_channel(args)
        return args.control_port, args.control_socket, args.control_token

    port, sock, token = channel(["--name", "info"], "linux")
    assert (port, sock) == (None, "/run/k/info.sock") and len(token) > 20
    port, sock, token = channel([], "win32")
    assert (port, sock) == (0, None) and token
    assert channel(["--control-port", "7001"], "linux") == (7001, None, None)  # a fixed port stays tokenless
    assert channel(["--control-token", "t"], "win32") == (0, None, "t")
    assert channel(["--no-control"], "linux") == (None, None, None)


def test_no_control_conflicts_with_an_explicit_channel(capsys):
    with pytest.raises(SystemExit):
        parse_args(["--no-config", "--no-control", "--control-port", "7001"])
    assert "--no-control" in capsys.readouterr().err


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX permissions")
def test_runtime_dir_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    (tmp_path / "kioskium").mkdir(mode=0o755)
    os.chmod(tmp_path / "kioskium", 0o755)
    assert control.runtime_dir().stat().st_mode & 0o777 == 0o700  # tokens live here

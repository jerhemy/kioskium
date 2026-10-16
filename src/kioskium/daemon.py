"""Supervisor daemon: ``kioskctl serve`` (also ``kiosk ctl serve``).

One always-running, Qt-free process with a fixed REST port that opens, closes and restarts browsers,
keeps them running and forwards their control API, so software that can only speak HTTP can open a browser
on a screen without a shell:

    GET    /api/health                       -> {ok, daemon: true}   no token needed (Origin and Host are still checked)
    GET    /api/status                       -> {ok, daemon, version, pid, instances}
    GET    /api/events[?since=SEQ]            -> Server-Sent Events: started, ready, exited, restarting, stopped, failed
    GET    /api/instances                    -> {ok, instances: [...]}
    POST   /api/instances  {"name"?, "appId"?, "url"?, "args"?, "restart"?, "wait"?}   start (or replace a stopped one)
    GET    /api/instances/NAME               -> the instance, plus its live /api/status when it is running
    POST   /api/instances/NAME/start|stop|restart
    DELETE /api/instances/NAME               stop it and forget it
    *      /api/instances/NAME/<endpoint>    forwarded to that instance's /api/<endpoint> (navigate, js, events, ...);
                                             /quit is the same as /stop, so the restart policy does not bring it back

Each instance gets a private control channel that only the daemon uses: a Unix socket in the runtime
directory on Linux, a free 127.0.0.1 port elsewhere, always with a random token. The registry still
lists it, so ``kioskctl --name NAME`` works for debugging.

A browser is identified by its name. The app-id only places its window (Weston maps app-ids to outputs); it
defaults to kioskium, and the name defaults to the app-id.

The browsers are the daemon's children: their output goes to the daemon's log, and they close when it exits.
"""

from __future__ import annotations

import argparse
import collections
import ipaddress
import json
import logging
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from kioskium import __version__, control
from kioskium.cli import DEFAULT_APP_ID, browser_identity

log = logging.getLogger("kioskium.daemon")

DEFAULT_PORT = 7000
RESTART_POLICIES = ("no", "on-failure", "always")
READY_TIMEOUT = 60.0   # default time POST /api/instances waits for the page's control API
STOP_TIMEOUT = 10.0    # graceful quit before the process is terminated
STABLE_AFTER = 60.0    # a run this long resets the restart backoff
MAX_BACKOFF = 30.0

# Options the daemon sets itself.
DAEMON_OWNED = {"app_id", "name", "control_port", "control_socket", "control_token", "no_control",
                "fail_if_running", "version", "print_config", "list_screens", "list_app_ids", "help"}
# Options that read or write arbitrary files, run code in every page or hand Chromium raw flags. A client of the
# REST API may only pass them when the daemon runs with --allow-unsafe-args; the operator's --instances file may.
UNSAFE = {"chromium_flag", "qt_arg", "inject_js", "inject_css", "remote_debugging_port", "allow_file_remote_access",
          "download_dir", "log_file", "page_log", "profile_dir", "loading_page", "screenshot", "config", "weston_ini"}


# ------------------------------------------------------------------------------------------
# Argument checks
# ------------------------------------------------------------------------------------------
def _kiosk_options() -> dict:
    from kioskium.cli import build_parser

    return build_parser()._option_string_actions  # noqa: SLF001 - argparse has no public accessor


def option_dest(token: str, options: dict | None = None) -> str | None:
    """The argparse dest a kiosk command-line token selects (abbreviations included), or None for values/unknowns."""
    if not token.startswith("-"):
        return None
    options = _kiosk_options() if options is None else options
    name = token.split("=", 1)[0]
    if name in options:
        return options[name].dest
    found = {a.dest for o, a in options.items() if o.startswith("--") and o.startswith(name)}
    return found.pop() if len(found) == 1 else None


def check_args(args, allow_unsafe: bool = False) -> str | None:
    """None if a client may pass these kiosk arguments, else the reason it may not."""
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        return "args must be a list of strings"
    options = _kiosk_options()
    for a in args:
        if a.startswith("@"):
            if not allow_unsafe:
                return f"{a}: argument files are not allowed (start the daemon with --allow-unsafe-args)"
            continue
        dest = option_dest(a, options)
        if dest in DAEMON_OWNED:
            return f"{a.split('=', 1)[0]} is set by the daemon"
        if dest in UNSAFE and not allow_unsafe:
            return f"{a.split('=', 1)[0]} is not allowed (start the daemon with --allow-unsafe-args)"
    return None


def default_kiosk_command() -> list[str]:
    """How to start the browser: the kiosk executable next to this one in a bundle, else this Python."""
    if getattr(sys, "frozen", False):
        return [str(Path(sys.executable).with_name("kiosk.exe" if sys.platform.startswith("win") else "kiosk"))]
    return [sys.executable, "-m", "kioskium"]


# ------------------------------------------------------------------------------------------
# Events
# ------------------------------------------------------------------------------------------
class EventLog:
    def __init__(self, keep: int = 200):
        self._cond = threading.Condition()
        self._events: list[tuple[int, str, dict]] = []
        self._seq = 0
        self._keep = keep

    def publish(self, name: str, data: dict) -> None:
        with self._cond:
            self._seq += 1
            self._events.append((self._seq, name, data))
            del self._events[:-self._keep]
            self._cond.notify_all()
        log.info("event %s %s", name, json.dumps(data))

    def wait_after(self, since: int, timeout: float) -> list[tuple[int, str, dict]]:
        """Events newer than ``since``; blocks up to ``timeout`` seconds for one to arrive."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > since, timeout)
            return [e for e in self._events if e[0] > since]


# ------------------------------------------------------------------------------------------
# Supervisor
# ------------------------------------------------------------------------------------------
class Instance:
    def __init__(self, name: str, url: str | None, args: list[str], restart: str, app_id: str | None):
        self.name = name
        self.app_id = app_id
        self.url = url
        self.args = args
        self.restart = restart
        self.token = secrets.token_urlsafe(24)
        self.state = "stopped"  # starting, running, restarting, exited, failed, stopped
        self.proc: subprocess.Popen | None = None
        self.pid: int | None = None  # the browser's own pid (from the registry; differs from proc.pid behind launchers)
        self.port: int | None = None
        self.socket: str | None = None
        self.exit_code: int | None = None
        self.started_at: float | None = None
        self.restarts = 0
        self.failures = 0
        self.generation = 0  # bumped on every launch so a stale watcher never acts
        self.output: collections.deque = collections.deque(maxlen=40)
        self.stop_requested = threading.Event()
        self.retry_first_launch = False  # instances from --instances retry even if the first launch fails (boot races)

    def info(self) -> dict:
        return {"name": self.name, "appId": self.app_id or DEFAULT_APP_ID, "state": self.state, "pid": self.pid, "url": self.url,
                "args": self.args, "restart": self.restart, "restarts": self.restarts, "exitCode": self.exit_code,
                "startedAt": self.started_at}

    def recent_output(self) -> list[str]:
        return list(self.output)


class Supervisor:
    def __init__(self, kiosk_cmd: list[str], use_socket: bool | None = None, events: EventLog | None = None,
                 default_restart: str = "on-failure"):
        self.kiosk_cmd = list(kiosk_cmd)
        self.default_restart = default_restart  # for requests and --instances rows that don't set "restart"
        self.use_socket = (not sys.platform.startswith("win")) if use_socket is None else use_socket
        self.events = events or EventLog()
        self.instances: dict[str, Instance] = {}
        self.lock = threading.RLock()

    # ---- public operations (called from HTTP handler threads) -----------------------------
    def start(self, name: str, url: str | None = None, args: list[str] | None = None, restart: str | None = None,
              app_id: str | None = None, wait: float = READY_TIMEOUT, retry_first_launch: bool = False) -> tuple[int, dict]:
        restart = restart or self.default_restart
        if restart not in RESTART_POLICIES:
            return 400, {"ok": False, "error": f"restart must be one of {', '.join(RESTART_POLICIES)}"}
        with self.lock:
            inst = self.instances.get(name)
            if inst and inst.state in ("starting", "running", "restarting"):
                return 409, {"ok": False, "error": f"{name} is already {inst.state}", "instance": inst.info()}
            other = control.find_instance(name)
            if other:
                return 409, {"ok": False, "error": f"a kiosk named {name} is already running outside the daemon "
                                                   f"(pid {other.get('pid')})"}
            inst = Instance(name, url, list(args or []), restart, app_id)
            inst.retry_first_launch = retry_first_launch
            self.instances[name] = inst
            self._launch(inst)
        return self._await(inst, wait)

    def start_again(self, name: str, wait: float = READY_TIMEOUT) -> tuple[int, dict]:
        inst = self.instances.get(name)
        if inst is None:
            return 404, {"ok": False, "error": f"no instance {name}"}
        return self.start(name, inst.url, inst.args, inst.restart, inst.app_id, wait)

    def stop(self, name: str, forget: bool = False, timeout: float = STOP_TIMEOUT) -> tuple[int, dict]:
        inst = self.instances.get(name)
        if inst is None:
            return 404, {"ok": False, "error": f"no instance {name}"}
        inst.stop_requested.set()
        if self._alive(inst):
            self._terminate(inst, timeout)
        with self.lock:
            inst.state = "stopped"
            inst.pid = None
            if forget:
                self.instances.pop(name, None)
        return 200, {"ok": True, "instance": inst.info()}

    def restart(self, name: str, wait: float = READY_TIMEOUT) -> tuple[int, dict]:
        code, body = self.stop(name)
        return (code, body) if code != 200 else self.start_again(name, wait)

    def list(self) -> list[dict]:
        with self.lock:
            return [i.info() for i in self.instances.values()]

    def get(self, name: str) -> Instance | None:
        return self.instances.get(name)

    def shutdown(self) -> None:
        """Daemon exit: quit every browser."""
        threads = [threading.Thread(target=self.stop, args=(a,), daemon=True) for a in list(self.instances)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(STOP_TIMEOUT + 5)

    # ---- process handling -----------------------------------------------------------------
    def command(self, inst: Instance) -> list[str]:
        _, identity = browser_identity(inst.name, inst.app_id)
        cmd = [*self.kiosk_cmd, *inst.args, *identity, "--fail-if-running",  # never hand over to a browser started elsewhere
               f"--control-token={inst.token}"]  # "=" form: a token may start with "-"
        if self.use_socket:
            inst.socket = control.default_socket_path(inst.name)
            cmd += ["--control-socket", inst.socket]
        else:
            cmd += ["--control-port", "0"]
        if inst.url:
            cmd.append(inst.url)
        return cmd

    def _launch(self, inst: Instance) -> None:
        inst.token = secrets.token_urlsafe(24)  # fresh per launch: a stale registry entry can never match
        cmd = self.command(inst)
        log.info("starting %s: %s", inst.name, " ".join(c.replace(inst.token, "***") for c in cmd))
        kwargs: dict = {}
        if sys.platform.startswith("win"):
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # Ctrl+C in the daemon's console stays ours
        else:
            kwargs["start_new_session"] = True
        inst.state, inst.pid, inst.port, inst.exit_code = "starting", None, None, None
        inst.started_at = time.time()
        inst.stop_requested.clear()
        inst.output.clear()
        inst.generation += 1
        inst.proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kwargs)
        self.events.publish("started", {"name": inst.name, "pid": inst.proc.pid})
        threading.Thread(target=self._pump_output, args=(inst, inst.proc), daemon=True).start()
        threading.Thread(target=self._watch, args=(inst, inst.generation), daemon=True).start()

    def _pump_output(self, inst: Instance, proc: subprocess.Popen) -> None:
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").rstrip()
            if line:
                inst.output.append(line)
                log.info("[%s] %s", inst.name, line)

    def _await(self, inst: Instance, wait: float) -> tuple[int, dict]:
        deadline = time.monotonic() + max(0.0, float(wait))
        while inst.state == "starting" and time.monotonic() < deadline:
            time.sleep(0.1)
        if inst.state == "failed":
            return 500, {"ok": False, "error": f"{inst.name} exited with code {inst.exit_code} before it was ready",
                         "instance": inst.info(), "output": inst.recent_output()}
        return 200, {"ok": True, "instance": inst.info()}

    def _registration(self, inst: Instance) -> dict | None:
        """The registry entry of this instance's browser (matched by pid or, behind a launcher, by token)."""
        try:
            data = json.loads(control.registry_path(inst.name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("token") == inst.token or (inst.proc is not None and data.get("pid") == inst.proc.pid):
            return data
        return None

    def _alive(self, inst: Instance) -> bool:
        return inst.proc is not None and inst.proc.poll() is None

    def _watch(self, inst: Instance, generation: int) -> None:
        proc, ready = inst.proc, False
        while self._alive(inst) and inst.generation == generation:
            if not ready and not inst.stop_requested.is_set():
                reg = self._registration(inst)
                if reg and (reg.get("port") or reg.get("socket")):
                    inst.pid, inst.port = reg.get("pid"), reg.get("port")
                    try:
                        status = self.call(inst, "GET", "/api/status", None, timeout=2.0)
                    except OSError:
                        status = {}
                    if status.get("ok"):
                        ready = True
                        inst.state = "running"
                        self.events.publish("ready", {"name": inst.name, "pid": inst.pid})
            try:
                proc.wait(0.25)
            except subprocess.TimeoutExpired:
                pass
        code = proc.returncode
        ran = time.time() - (inst.started_at or time.time())
        with self.lock:
            if inst.generation != generation:  # relaunched meanwhile
                return
            inst.exit_code, inst.pid = code, None
            if inst.stop_requested.is_set():
                inst.state = "stopped"
                self.events.publish("stopped", {"name": inst.name, "exitCode": code})
                return
            if not ready and not inst.retry_first_launch and inst.restarts == 0:
                inst.state = "failed"
                self.events.publish("failed", {"name": inst.name, "exitCode": code, "output": inst.recent_output()[-5:]})
                return
            again = inst.restart == "always" or (inst.restart == "on-failure" and code != 0)
            if not again:
                inst.state = "exited"
                self.events.publish("exited", {"name": inst.name, "exitCode": code})
                return
            inst.failures = 0 if ran >= STABLE_AFTER else inst.failures + 1
            delay = min(MAX_BACKOFF, 2.0 ** max(0, inst.failures - 1))
            inst.state = "restarting"
            self.events.publish("restarting", {"name": inst.name, "exitCode": code, "delay": delay})
        if inst.stop_requested.wait(delay):
            with self.lock:
                inst.state = "stopped"
            return
        with self.lock:
            if inst.generation == generation and not inst.stop_requested.is_set():
                inst.restarts += 1
                self._launch(inst)

    def _terminate(self, inst: Instance, timeout: float) -> None:
        proc = inst.proc
        try:
            self.call(inst, "POST", "/api/quit", {}, timeout=3.0)
        except OSError:
            pass
        deadline = time.monotonic() + timeout
        while self._alive(inst) and time.monotonic() < deadline:
            time.sleep(0.1)
        if not self._alive(inst):
            return
        log.warning("%s did not quit within %.0f s, terminating", inst.name, timeout)
        for pid in {inst.pid} - {None, proc.pid}:  # the browser itself, when it runs behind a launcher
            try:
                os.kill(int(pid), signal.SIGTERM)
            except OSError:
                pass
        proc.terminate()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(5)

    # ---- talking to an instance -----------------------------------------------------------
    def connection(self, inst: Instance, timeout: float | None):
        return control.connection(port=inst.port, socket_path=inst.socket if self.use_socket else None, timeout=timeout)

    def call(self, inst: Instance, method: str, path: str, payload: dict | None, timeout: float = 10.0) -> dict:
        return control.request(method, path, payload, port=inst.port, socket_path=inst.socket if self.use_socket else None,
                               token=inst.token, timeout=timeout)


# ------------------------------------------------------------------------------------------
# HTTP front end
# ------------------------------------------------------------------------------------------
def _drain_chunked(rfile, limit: int = 1 << 20) -> None:
    """Read and discard a chunked request body (up to ``limit`` bytes), so a refusal reaches the client."""
    total = 0
    while total < limit:
        size_line = rfile.readline(1024)
        try:
            size = int(size_line.split(b";", 1)[0].strip() or b"0", 16)
        except ValueError:
            return
        if size == 0:
            while rfile.readline(1024) not in (b"\r\n", b"\n", b""):  # trailer lines, then the blank line
                pass
            return
        rfile.read(size + 2)  # chunk data and its CRLF
        total += size


def _is_loopback(host: str) -> bool:
    if host in ("localhost", ""):
        return True
    try:
        return ipaddress.ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


class DaemonServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = not sys.platform.startswith("win")  # on Windows it would let two daemons share the port

    def __init__(self, address, supervisor: Supervisor, token: str | None = None, allow_origins=(),
                 allow_unsafe_args: bool = False):
        self.supervisor = supervisor
        self.token = token
        self.allow_origins = set(allow_origins)
        self.allow_unsafe_args = allow_unsafe_args
        self.loopback_only = _is_loopback(address[0])
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    server: DaemonServer
    server_version = f"kioskium-daemon/{__version__}"

    def log_message(self, fmt, *args):  # route http.server's access log through logging
        log.debug("%s %s", self.address_string(), fmt % args)

    def do_GET(self):  # noqa: N802
        self._handle("GET")

    def do_POST(self):  # noqa: N802
        self._handle("POST")

    def do_DELETE(self):  # noqa: N802
        self._handle("DELETE")

    def do_OPTIONS(self):  # noqa: N802
        origin = self.headers.get("Origin")
        if not self._origin_allowed(origin):
            self._reply(403, {"ok": False, "error": "origin not allowed"})
            return
        self.send_response(204)
        self._cors(origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Kioskium-Token, Authorization")
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ---- helpers ----------------------------------------------------------------------------
    def _origin_allowed(self, origin: str | None) -> bool:
        """Browsers send Origin on cross-site requests; a web page must not drive the daemon unless allowed."""
        return origin is None or "*" in self.server.allow_origins or origin in self.server.allow_origins

    def _cors(self, origin: str | None) -> None:
        if origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors(self.headers.get("Origin") if self._origin_allowed(self.headers.get("Origin")) else None)
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        if not self.server.token:
            return True
        given = self.headers.get("X-Kioskium-Token") or (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        return secrets.compare_digest(given or "", self.server.token)

    def _handle(self, method: str) -> None:
        try:
            if not self._origin_allowed(self.headers.get("Origin")):
                self._reply(403, {"ok": False, "error": "origin not allowed (see --allow-origin)"})
                return
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
            if self.server.loopback_only and not _is_loopback(host):  # DNS rebinding
                self._reply(403, {"ok": False, "error": f"unexpected Host {host}"})
                return
            target = urlsplit(self.path)
            if method == "GET" and target.path.rstrip("/") == "/api/health":  # no token: for monitors
                self._reply(200, {"ok": True, "daemon": True})
                return
            if not self._authorised():
                self._reply(401, {"ok": False, "error": "bad token"})
                return
            query = {k: v[-1] for k, v in parse_qs(target.query).items()}
            if self.headers.get("Transfer-Encoding"):  # chunked: never read as an empty body
                _drain_chunked(self.rfile)  # read it all first: closing on unread data resets the connection
                self._reply(411, {"ok": False, "error": "send the body with a Content-Length header (chunked bodies are not supported)"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            self._route(method, target.path.rstrip("/"), target.query, query, body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):  # client went away
            pass
        except Exception as exc:  # noqa: BLE001
            log.exception("daemon request failed")
            try:
                self._reply(500, {"ok": False, "error": str(exc)})
            except OSError:
                pass

    def _json(self, body: bytes, query: dict) -> dict:
        payload = json.loads(body.decode("utf-8")) if body.strip() else {}
        if not isinstance(payload, dict):
            raise ValueError("JSON object expected")
        return {**query, **payload}

    def _route(self, method: str, path: str, raw_query: str, query: dict, body: bytes) -> None:
        sup = self.server.supervisor
        if path == "/api/status" and method == "GET":
            self._reply(200, {"ok": True, "daemon": True, "version": __version__, "pid": os.getpid(),
                              "restart": sup.default_restart, "instances": [i["name"] for i in sup.list()]})
            return
        if path == "/api/events" and method == "GET":
            self._events(sup.events, int(query.get("since") or 0))
            return
        if path == "/api/instances":
            if method == "GET":
                self._reply(200, {"ok": True, "instances": sup.list()})
            elif method == "POST":
                self._start(body, query)
            else:
                self._reply(405, {"ok": False, "error": "GET or POST"})
            return
        if not path.startswith("/api/instances/"):
            self._reply(404, {"ok": False, "error": f"unknown endpoint {path}"})
            return
        name, _, rest = path[len("/api/instances/"):].partition("/")
        name = unquote(name)
        inst = sup.get(name)
        if inst is None:
            self._reply(404, {"ok": False, "error": f"no instance {name}"})
            return
        if not rest:
            if method == "GET":
                out = {"ok": True, "instance": inst.info()}
                if inst.state == "running":
                    try:
                        out["status"] = sup.call(inst, "GET", "/api/status", None, timeout=3.0)
                    except OSError as exc:
                        out["status"] = {"ok": False, "error": str(exc)}
                self._reply(200, out)
            elif method == "DELETE":
                self._reply(*sup.stop(name, forget=True))
            else:
                self._reply(405, {"ok": False, "error": "GET or DELETE"})
            return
        wait = float(self._json(body, query).get("wait", READY_TIMEOUT)) if rest in ("start", "restart") else 0
        if rest in ("stop", "quit") and method == "POST":
            self._reply(*sup.stop(name))
        elif rest == "start" and method == "POST":
            self._reply(*sup.start_again(name, wait))
        elif rest == "restart" and method == "POST":
            self._reply(*sup.restart(name, wait))
        elif inst.state != "running":
            self._reply(409, {"ok": False, "error": f"{name} is {inst.state}", "instance": inst.info()})
        else:
            self._forward(inst, method, "/api/" + rest + ("?" + raw_query if raw_query else ""), body)

    def _start(self, body: bytes, query: dict) -> None:
        try:
            p = self._json(body, query)
        except ValueError as exc:
            self._reply(400, {"ok": False, "error": f"bad JSON: {exc}"})
            return
        if not all(isinstance(p.get(k), (str, type(None))) for k in ("name", "appId")):
            self._reply(400, {"ok": False, "error": "name and appId must be strings"})
            return
        name, _ = browser_identity(p.get("name"), p.get("appId"))
        args = p.get("args") or []
        problem = check_args(args, self.server.allow_unsafe_args)
        if problem:
            self._reply(400, {"ok": False, "error": problem})
            return
        try:
            wait = float(p.get("wait", READY_TIMEOUT))
        except (TypeError, ValueError):
            self._reply(400, {"ok": False, "error": "wait must be a number of seconds"})
            return
        self._reply(*self.server.supervisor.start(name, p.get("url"), args, p.get("restart"),
                                                  p.get("appId"), wait))

    def _forward(self, inst: Instance, method: str, path: str, body: bytes) -> None:
        sup = self.server.supervisor
        stream = path.startswith("/api/events")
        conn = sup.connection(inst, timeout=None if stream else 60.0)
        headers = {"Content-Type": "application/json", "X-Kioskium-Token": inst.token}
        try:
            conn.request(method, path, body=body if method == "POST" else None, headers=headers)
            resp = conn.getresponse()
        except OSError as exc:
            self._reply(502, {"ok": False, "error": f"{inst.name} is not answering: {exc}"})
            return
        self.send_response(resp.status)
        for name in ("Content-Type", "Cache-Control"):
            if resp.getheader(name):
                self.send_header(name, resp.getheader(name))
        self._cors(self.headers.get("Origin"))
        if not stream:
            data = resp.read()
            conn.close()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        self.end_headers()
        try:
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        finally:
            conn.close()

    def _events(self, events: EventLog, since: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self._cors(self.headers.get("Origin"))
        self.end_headers()
        self.wfile.write(b": kioskium daemon events\n\n")
        self.wfile.flush()
        while True:
            batch = events.wait_after(since, 15.0)
            for seq, name, data in batch:
                self.wfile.write(control._sse_chunk(seq, name, data))
                since = seq
            if not batch:
                self.wfile.write(b": ping\n\n")
            self.wfile.flush()


# ------------------------------------------------------------------------------------------
# Command line
# ------------------------------------------------------------------------------------------
def load_instances(path: str) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = data.get("instances") if isinstance(data, dict) else data
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ValueError(f"{path}: expected a list of objects (or {{\"instances\": [...]}})")
    names = [browser_identity(r.get("name"), r.get("appId"))[0] for r in rows]
    if len(set(names)) != len(names):
        raise ValueError(f"{path}: two instances have the same name ({', '.join(sorted({n for n in names if names.count(n) > 1}))}); "
                         "give each a different name or appId")
    return rows


def serve_main(argv: list[str]) -> int:
    """Entry point for ``kioskctl serve``."""
    p = argparse.ArgumentParser(
        prog="kioskctl serve",
        description="The kioskium daemon: a fixed REST port (default 7000) that opens, closes and restarts browsers, keeps them "
                    "running (restart policies) and forwards their control API (see docs/API.md, section 8). kioskctl on "
                    "this machine uses it automatically.",
        epilog="Browser options go in each instance's args (POST /api/instances, or --instances FILE). Options for every "
               "browser can go in kioskium.json next to kiosk.",
    )
    p.add_argument("--port", type=int, default=int(os.environ.get("KIOSKIUM_DAEMON_PORT") or DEFAULT_PORT),
                   help=f"REST port (default {DEFAULT_PORT}, env KIOSKIUM_DAEMON_PORT)")
    p.add_argument("--bind", default="127.0.0.1", metavar="ADDR", help="address to listen on; anything but loopback needs --token")
    p.add_argument("--token", default=os.environ.get("KIOSKIUM_TOKEN"), help="require this token (X-Kioskium-Token) on every request "
                   "(env KIOSKIUM_TOKEN)")
    p.add_argument("--allow-origin", action="append", default=[], metavar="ORIGIN",
                   help="let web pages from ORIGIN call the API (repeatable, '*' = any). "
                   "Default: requests with an Origin header are refused")
    p.add_argument("--allow-unsafe-args", action="store_true",
                   help="let clients pass options that touch files or Chromium internals (" + ", ".join(sorted(
                       "--" + d.replace("_", "-") for d in UNSAFE)) + ")")
    p.add_argument("--instances", metavar="FILE", help="JSON list of instances to start at launch (same fields as POST /api/instances); "
                   "if it cannot be read, the daemon logs why and starts without them")
    p.add_argument("--restart", default="on-failure", choices=RESTART_POLICIES, metavar="POLICY",
                   help="restart policy for browsers whose request or --instances entry sets none: on-failure (default: "
                   "after a crash or an error exit), always (also when the window is closed or the browser quits by "
                   "itself; only stop or close through the daemon keeps it closed) or no")
    p.add_argument("--log-level", default="info", choices=("debug", "info", "warning", "error"), help="daemon log level (default info)")
    a, unknown = p.parse_known_args(argv)
    unknown = [u for u in unknown if u != "--"]
    if unknown:
        if any(option_dest(u) is not None for u in unknown):
            p.error(f"{' '.join(unknown)}: browser options go in each instance's args, e.g. "
                    f"POST /api/instances {{\"name\": \"left\", \"args\": {json.dumps(unknown)}}}, "
                    "or in kioskium.json for every browser")
        p.error(f"unrecognized arguments: {' '.join(unknown)}")
    logging.basicConfig(level=getattr(logging, a.log_level.upper()), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    if not _is_loopback(a.bind) and not a.token:
        p.error(f"--bind {a.bind} is reachable from other machines: set --token as well")
    boot = []
    if a.instances:
        # A typo in the file must not take the API down with it: the API is how the device gets fixed remotely.
        try:
            boot = load_instances(a.instances)
        except (OSError, ValueError) as exc:
            log.error("not starting the browsers in %s: %s", a.instances, exc)

    sup = Supervisor(default_kiosk_command(), default_restart=a.restart)
    try:
        server = DaemonServer((a.bind, a.port), sup, a.token, a.allow_origin, a.allow_unsafe_args)
    except OSError as exc:
        log.error("cannot listen on %s:%d: %s", a.bind, a.port, exc)
        return 2
    log.info("kioskium daemon %s listening on http://%s:%d/api/ (kiosk: %s)", __version__, a.bind, server.server_port,
             " ".join(sup.kiosk_cmd))
    for row in boot:  # the file is the operator's own configuration: no argument restrictions
        threading.Thread(target=sup.start, daemon=True, kwargs={
            "name": browser_identity(row.get("name"), row.get("appId"))[0], "app_id": row.get("appId"), "url": row.get("url"),
            "args": row.get("args") or [], "restart": row.get("restart"), "wait": 0,
            "retry_first_launch": True}).start()

    def _stop(signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        log.info("stopping")
    finally:
        sup.shutdown()
        server.server_close()
    return 0

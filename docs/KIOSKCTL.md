# kioskctl reference

`kioskctl` is the command-line client for Kioskium. It opens browsers, controls them (navigate,
split, run JavaScript, screenshots, events), closes and restarts them, and runs the daemon that
supervises browsers and opens them over REST (`kioskctl serve`). It needs no Qt and works the same on
Linux and Windows.

- Bundles: `kioskctl` (`kioskctl.exe` on Windows) next to `kiosk`.
- pip install: `kioskctl`. `kiosk ctl ...` does the same.

Every command maps to a REST call ([REST and kioskctl](#rest-and-kioskctl)), so anything here can
also be done with curl or wget.

```
kioskctl [GLOBAL OPTIONS] COMMAND [COMMAND OPTIONS]
kioskctl serve [DAEMON OPTIONS]
```

**Browsers:** [`open`](#open) · [`close`](#close) · [`restart`](#restart) · [`list`](#list) ·
[`status`](#status)

**Pages:** [`navigate`](#navigate) · [`reload`](#reload) · [`split`](#split) · [`unsplit`](#unsplit) ·
[`devtools`](#devtools) · [`fps`](#fps) · [`screenshot`](#screenshot) · [`js`](#js) · [`events`](#events)

**Daemon:** [`serve`](#serve)

---

## With or without the daemon

`kioskctl` works both ways, and picks on its own:

- **No daemon running:** `open` starts the browser itself, in the background. The browser keeps
  running after `kioskctl` exits, but nothing restarts it if it crashes. Its output goes to
  `<runtime dir>/logs/<name>.log`.
- **A daemon running on this machine** (`kioskctl serve`, for example as a systemd service): `open`, `close`,
  `restart` and `list` go through it. The browser is the daemon's child: it is restarted after a
  crash (`--restart`), its output goes to the daemon's log (the journal), and other software can
  reach it over the daemon's REST port.

`kioskctl` finds the daemon by asking `http://127.0.0.1:7000/api/health` (the port follows
`KIOSKIUM_DAEMON_PORT`). To use a daemon on another machine, give `--daemon-url HOST:PORT` (or set
`KIOSKIUM_DAEMON_URL`); then every command goes there.

## Names and app-ids

Every browser has two identifiers, each with one job:

- **The name** is what you call it. Every command after `open` uses it: `kioskctl navigate -n menu URL`.
- **The app-id** says where the window goes. Weston maps app-ids to outputs in `weston.ini`
  (`app-ids=left-screen[...]`, see [EMBEDDED.md](EMBEDDED.md)). Off Weston it does nothing useful.

The app-id defaults to `kioskium` and the name defaults to the app-id, so with one browser per
screen you only give the app-id:

```sh
kioskctl open http://localhost:8080/ --app-id left-screen     # named left-screen
kioskctl navigate -n left-screen http://localhost:8080/home

kioskctl open http://localhost:8080/menu --app-id left-screen -n menu     # your own name
kioskctl navigate -n menu http://localhost:8080/specials
```

Names are unique: `open` refuses a name that is already running. Two browsers may share an app-id
(both windows then go to that output) as long as their names differ. Characters that can't be in a
file name, and spaces, become `-` (`"my menu"` is `my-menu`).

## Choosing the browser

`-n NAME` picks the browser, before or after the command. **With only one browser running, leave it
out**: `kioskctl navigate URL` goes to that one.

| Global option | Use |
|---|---|
| `-n NAME`, `--name NAME` | the browser, by name (see `kioskctl list`) |
| `--port PORT` | instead: the browser on this control port (one started with `kiosk --control-port PORT`) |
| `--socket PATH` | instead: the browser on this control Unix socket (Linux) |
| `--daemon-url URL` | use the daemon at `URL` (default: env `KIOSKIUM_DAEMON_URL`, else the one on this machine, if it runs) |
| `--token TOKEN` | the daemon's token, or the token of a browser started with `--control-token` (default: env `KIOSKIUM_TOKEN`) |
| `--timeout SEC` | how long to wait for an answer (default 10) |
| `--json` | print exactly one compact JSON document on stdout, for scripts |

```sh
kioskctl status                    # the only browser
kioskctl status -n left-screen
kioskctl -n left-screen status
kioskctl --port 7001 status
```

Names are found in the registry: every browser registers itself in `$XDG_RUNTIME_DIR/kioskium/`
(Linux) or `%LOCALAPPDATA%\kioskium\run\` (Windows) with its port or socket and token
([API.md, section 5](API.md#5-instance-registry-and-a-second-launch)), and a name that isn't there is
asked of the daemon. For the registry, `kioskctl` must run as the same user with the same
`XDG_RUNTIME_DIR` as the browser (for a daemon run as root by systemd, `/run/user/0`); otherwise the
daemon still reaches it.

## Output and exit codes

`open`, `close`, `restart` and `list` print short text; the other commands print the JSON answer,
indented. With `--json` every command prints one JSON document on one line instead. **Scripts should
use `--json`**: its fields are kept stable, while the text may change.

| Exit code | Meaning |
|---|---|
| `0` | done |
| `1` | the browser or daemon refused the command (`"ok": false`, e.g. a JavaScript error), or it failed to start |
| `2` | browser or daemon not found or not reachable, or invalid arguments |
| `3` | `open`: a browser with that name is already running |

```sh
if kioskctl status -n left-screen >/dev/null; then echo running; fi
title=$(kioskctl --json js -n left-screen "document.title")
```

---

## Commands

### open

```
kioskctl open [URL] [--app-id ID] [-n NAME] [--restart POLICY] [--wait SEC] [-- BROWSER OPTIONS]
```

Opens a browser and waits until it is ready. Through the daemon when one runs, else on its own (see
[With or without the daemon](#with-or-without-the-daemon)).

| Option | |
|---|---|
| `URL` | page to show (default: the loading screen, waiting for `navigate`) |
| `--app-id ID` | which output the window goes to on Weston (default `kioskium`) |
| `-n NAME`, `--name NAME` | what later commands call it (default: the app-id) |
| `--restart POLICY` | `no`, `on-failure` or `always` (default: the daemon's `--restart`); needs the daemon |
| `--wait SEC` | give up waiting after `SEC` seconds if it is not ready yet (default 60; `0` returns once it has started). `open` returns as soon as the browser answers, usually within a few seconds, and never closes it |
| `-- OPTIONS` | everything after `--` goes to the browser (any `kiosk` option, see [OPTIONS.md](OPTIONS.md)) |

```sh
kioskctl open kioskium:test
kioskctl open http://localhost:8080/ --app-id left-screen -- --show-cursor
kioskctl open http://localhost:8080/menu --app-id left-screen -n menu --restart always
kioskctl open -n test -- --windowed --control-port 7002
```
```
opened left-screen  pid 4312  port 41871  http://localhost:8080/
opened menu  pid 4388  daemon  http://localhost:8080/menu
```

With `--json`: `{"ok": true, "name", "appId", "pid", "daemon": false|true, "url", ...}`, plus `port`,
`socket` and `log` without the daemon, or `instance` (as in `GET /api/instances/NAME`) with it.

If a browser with that name is already running, `open` exits with `3` and says how to change its
page or restart it instead. If the browser exits while starting (for example because of a bad
option), `open` prints the error and the last lines of its output, and exits with `1`.

Without the daemon on Linux, the browser needs the compositor's environment. Over SSH that usually
means `XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0 kioskctl open ...`. The daemon has them.

### close

```
kioskctl close [-n NAME] [--all]
```

Closes a browser: the window closes and the process exits. One the daemon opened is also removed
from the daemon's list, so it is not restarted. `quit` and `exit` are the same command.

| Option | |
|---|---|
| `--all` | close every browser |

```sh
kioskctl close -n left-screen
kioskctl close --all
```
```
closed left-screen
```

### restart

```
kioskctl restart [-n NAME] [--wait SEC]
```

Closes a browser and opens it again with the same URL, app-id and options. Only for browsers the
daemon opened; for others, `close` and `open` again. `--wait` is the same as for `open`.

```sh
kioskctl restart -n left-screen
```
```
restarted left-screen  pid 4502  daemon  http://localhost:8080/
```

### list

```
kioskctl list
```

Every browser: the daemon's (with their state: `running`, `starting`, `restarting`, `exited`,
`failed`, `stopped`) and the ones `kioskctl open` or `kiosk` started on their own (`running`, or
`stale` for a registry entry whose process is gone; stale entries are removed as they are listed).

```
STATE    NAME          APP-ID         PID  BY      URL
running  menu          left-screen   4312  daemon  http://localhost:8080/menu
running  right-screen  right-screen  4388  local   kioskium:test
```

`kioskctl --json list` returns `{"ok": true, "instances": [...]}`; each has `name`, `appId`,
`state`, `pid`, `url` and `daemon` (whether the daemon manages it), plus the daemon's fields
(`restart`, `restarts`, `args`, ...) or `port`/`socket`.

### status

```
kioskctl status [-n NAME]
```

The browser's state, the same JSON as `GET /api/status`. For a browser the daemon manages it also
has `instance` (restart policy, restart count, ...); if that browser isn't running, `ok` is `false`
and the error says what it is (`failed`, `restarting`, ...). With no browser named and none or
several running, it shows the daemon's own status.

```sh
kioskctl status -n left-screen
```
```json
{
  "ok": true, "name": "left-screen", "appId": "left-screen", "pid": 4312,
  "url": "http://localhost:8080/home", "targetUrl": "http://localhost:8080/",
  "state": "target", "loadFailures": 0, "split": null,
  "devtools": false, "fps": false, "kiosk": true, "version": ["..."]
}
```

`state` is `idle` (loading screen, waiting for a URL), `connecting` (first attempt), `loading`
(retrying after a failure) or `target` (the page is shown).

### navigate

```
kioskctl navigate URL [--pane 1|2]
```

Loads a page through the loading screen, which retries until the page loads. `URL` can be a web
address, a local file or a built-in page: `kioskium:test` (diagnostics), `kioskium:input` (input
test), `kioskium:idle` (the loading screen).

| Option | |
|---|---|
| `--pane 1\|2` | `1` the main page (default), `2` the second pane of a split (created if there is none) |

```sh
kioskctl navigate -n left-screen http://localhost:8080/
kioskctl navigate -n left-screen kioskium:test
kioskctl navigate -n left-screen http://localhost:8080/side --pane 2
```

### reload

```
kioskctl reload [--pane 1|2]
```

Reloads the main page, or the second pane with `--pane 2`.

### split

```
kioskctl split URL [--orientation horizontal|vertical] [--ratio PERCENT]
```

Shows a second page in the same window. Calling it again points the second pane at a new URL.

| Option | |
|---|---|
| `--orientation` | `horizontal` side by side (default), or `vertical` one above the other |
| `--ratio PERCENT` | the first pane's share of the window (default 50) |

```sh
kioskctl split -n left-screen kioskium:input --ratio 40
kioskctl split -n left-screen http://localhost:8080/map --orientation vertical
```

### unsplit

```
kioskctl unsplit
```

Removes the second pane.

### devtools

```
kioskctl devtools [open|close|toggle]
```

Shows the Chromium inspector as a pane under the page, also in kiosk mode. Default: `toggle`.

### fps

```
kioskctl fps [on|off|toggle]
```

Frame-rate overlay in every pane (the same as starting with `--fps`). Default: `toggle`.

### screenshot

```
kioskctl screenshot PATH
```

Saves the whole window (both panes, DevTools) as PNG. `PATH` is on the machine running
the browser and written by the browser's user. On Windows use forward slashes (`C:/temp/shot.png`).

### js

```
kioskctl js CODE [--pane 1|2]
```

Runs JavaScript in the page and prints the value of the last statement, like the DevTools console.
Arrays and objects come back as JSON. A JavaScript error exits with `1`.

```sh
kioskctl js -n left-screen "document.title"
kioskctl js -n left-screen "[innerWidth, innerHeight]"
kioskctl js -n left-screen 'document.querySelector("#start").click()'
kioskctl --json js -n left-screen "location.href"     # {"ok":true,"pane":1,"result":"http://..."}
```

### events

```
kioskctl events [-n NAME] [--since SEQ] [--count N]
```

Prints a browser's live events, one JSON object per line, until Ctrl+C. With no browser named and
none or several running, it prints the daemon's events instead (`started`, `ready`, `exited`,
`restarting`, `stopped`, `failed`).

```
{"seq":12,"event":"load","pane":1,"status":"finished","url":"http://localhost:8080/home"}
{"seq":13,"event":"split","pane":1,"url":"file:///.../input-page.html","orientation":"horizontal","ratio":40}
```

| Option | |
|---|---|
| `--since SEQ` | first replay the buffered events after sequence number `SEQ` (the last 200 are kept) |
| `--count N` | stop after `N` events |

The event types are listed in [API.md, section 3](API.md#3-events-server-sent-events).

---

## serve

```
kioskctl serve [--port PORT] [--bind ADDR] [--token TOKEN] [--allow-origin ORIGIN]
               [--allow-unsafe-args] [--instances FILE] [--restart POLICY]
               [--log-level debug|info|warning|error]
```

Runs the daemon: one fixed REST port (default `7000`) that opens, closes and restarts browsers,
restarts crashed ones and forwards their control API. The browsers are its children: their output
goes to its log, and they close when it exits. Of the global options, only `--token` applies to
`serve`, and it may go before or after it. The REST API is in
[API.md, section 8](API.md#8-daemon-start-browsers-over-rest-kioskctl-serve).

| Option | |
|---|---|
| `--port PORT` | REST port (default `7000`, env `KIOSKIUM_DAEMON_PORT`) |
| `--bind ADDR` | address to listen on (default `127.0.0.1`); any other address needs `--token` |
| `--token TOKEN` | require `X-Kioskium-Token: TOKEN` on every request except `/api/health` (env `KIOSKIUM_TOKEN`) |
| `--allow-origin ORIGIN` | let web pages from `ORIGIN` call the API (repeatable, `*` = any). Default: requests from web pages are refused |
| `--allow-unsafe-args` | let clients pass browser options that touch files or Chromium internals (`--chromium-flag`, `--inject-js`, `--profile-dir`, `--log-file`, ...) |
| `--instances FILE` | JSON list of browsers to open at start-up, same fields as `POST /api/instances`. If it can't be read, the daemon logs why and runs without them, so the API stays up |
| `--restart POLICY` | restart policy for browsers that don't set their own: `on-failure` (default: after a crash or an error exit), `always` (also when the window is closed or the browser quits by itself) or `no`. `kioskctl close`/`stop` through the daemon always keeps a browser closed |
| `--log-level LEVEL` | daemon log level (default `info`) |

Browser options don't go on the `serve` command line. They go after `--` in `kioskctl open`, in each
browser's `args` over REST, or in `kioskium.json` next to `kiosk` for every browser:

```sh
kioskctl serve &
kioskctl open kioskium:test --app-id left-screen -n left -- --show-cursor   # through the daemon
kioskctl navigate -n left http://localhost:8080/
kioskctl close -n left
```

```sh
kioskctl serve --instances /etc/kioskium/instances.json
```
```json
[
  {"appId": "left-screen",  "url": "http://localhost:8080/left",  "args": ["--loading-status"]},
  {"appId": "right-screen", "name": "info", "url": "http://localhost:8080/right", "restart": "always"}
]
```

Running it as a service: [EMBEDDED.md](EMBEDDED.md#running-the-daemon-with-systemd) has a systemd unit.

A daemon on another machine (listening there with `--bind` and `--token`):

```sh
kioskctl --daemon-url device:7000 --token SECRET list
kioskctl --daemon-url device:7000 --token SECRET open http://localhost:8080/ --app-id left-screen
```

## REST and kioskctl

Browser endpoints ([API.md, section 1](API.md#1-control-api-httpjson)), directly or forwarded by the
daemon as `/api/instances/NAME/<endpoint>`:

| REST | kioskctl |
|---|---|
| `GET /api/health` | none; `status` shows the same `state` |
| `GET /api/status` | `status` |
| `POST /api/navigate` | `navigate URL [--pane 1\|2]` |
| `POST /api/reload` | `reload [--pane 1\|2]` |
| `POST /api/split`, `POST /api/unsplit` | `split URL [--orientation O] [--ratio N]`, `unsplit` |
| `POST /api/devtools` | `devtools [open\|close\|toggle]` |
| `POST /api/fps` | `fps [on\|off\|toggle]` |
| `POST /api/screenshot` | `screenshot PATH` |
| `POST /api/js` | `js CODE [--pane 1\|2]` |
| `POST /api/quit` | `close` |
| `GET /api/events` | `events [--since N] [--count N]` |

Daemon endpoints ([API.md, section 8](API.md#8-daemon-start-browsers-over-rest-kioskctl-serve)):

| REST | kioskctl |
|---|---|
| `GET /api/health` | none; `kioskctl` uses it to find the daemon |
| `GET /api/status` | `status` (with no browser named and none or several running) |
| `GET /api/events` | `events` (likewise) |
| `GET /api/instances` | `list` |
| `POST /api/instances` | `open [URL] [--app-id ID] [-n NAME] [--restart P] [--wait S] [-- OPTIONS]` |
| `GET /api/instances/NAME` | `status -n NAME` |
| `DELETE /api/instances/NAME` | `close -n NAME` |
| `POST /api/instances/NAME/restart` | `restart -n NAME` |
| `POST /api/instances/NAME/stop`, `/start` | none: `close` removes a browser, `open` adds it again |
| `/api/instances/NAME/<endpoint>` | the browser commands, `-n NAME` |

---

## PowerShell

```powershell
.\kioskctl.exe list
.\kioskctl.exe navigate -n left-screen http://localhost:8080/
(.\kioskctl.exe --json status -n left-screen | ConvertFrom-Json).state
.\kioskctl.exe --json list | ConvertFrom-Json | Select-Object -ExpandProperty instances
```

# Kioskium API reference

Everything a running browser exposes, with copy-paste examples. Four surfaces, plus a daemon that
starts browsers on request (section 8):

1. **Control API**: HTTP/JSON. Every browser has one (a Unix socket on Linux, a free `127.0.0.1` port on
   Windows, found through the registry), or on a port you pick with `--control-port`.
2. **`kioskctl`**: command-line client for the control API (`kioskctl` when installed with
   pip, `kiosk ctl ...` also works).
3. **Events**: a Server-Sent Events stream from the control API.
4. **`window.kioskium`**: JavaScript API inside the page.

Start a browser to try them against:

```sh
kiosk --windowed --control-port 7001 --app-id left-screen kioskium:test
```

The examples use port `7001` and a POSIX shell, each with **curl** and **wget** (GNU or busybox, for
minimal images). In PowerShell use `Invoke-RestMethod`.

**Postman:** import [examples/kioskium.postman_collection.json](../examples/kioskium.postman_collection.json).
It covers every endpoint, with example responses and tests. Set the collection variables `baseUrl`
(default `http://127.0.0.1:7001`) and, if you use `--control-token`, `token`. Run the requests one by
one, or the whole collection (the **Event stream** request stays open, so leave it out of runs).
`npx newman run examples/kioskium.postman_collection.json` runs it from the command line.

**From another machine:** the API only listens on 127.0.0.1, so forward the port over SSH and use
the examples (or Postman) against your own `127.0.0.1:7001`: `ssh -N -L 7001:127.0.0.1:7001 root@device`.

---

## 1. Control API (HTTP/JSON)

- Base URL `http://127.0.0.1:<port>`; bodies and responses are JSON. Every response carries
  `"ok": true|false`; failures add `"error"`.
- Every browser opens a control channel unless started with `--no-control`: a Unix socket in the
  runtime directory on Linux, a free `127.0.0.1` port on Windows, with a random token. Both are
  recorded in the registry (section 5), which is how `kioskctl -n NAME` reaches it.
- `--control-port PORT` (or `--control-socket PATH`) picks the channel yourself; `0` = any free port.
  A channel you pick has no token unless you give `--control-token SECRET`.
- With a token, send `X-Kioskium-Token: SECRET` (or `Authorization: Bearer SECRET`); otherwise 401.
  `GET /api/health` never needs it.
  curl: `-H 'X-Kioskium-Token: SECRET'`; wget: `--header='X-Kioskium-Token: SECRET'`.
- On an HTTP error, curl prints the JSON body; wget exits non-zero (GNU wget shows the body only with
  `--content-on-error`).
- HTTP codes: 200 ok, 400 bad request/JSON, 401 bad token, 404 unknown endpoint, 405 wrong method,
  411 body sent without `Content-Length` (chunked), 500 command failed.
- Unix socket alternative on Linux: `--control-socket /run/kioskium/left.sock`
  (`curl --unix-socket /run/kioskium/left.sock http://x/api/status`; wget has no Unix socket support).

### GET /api/health

For monitors and probes: no token needed, and it answers only `ok` and the page `state` (see below).
The API runs on the browser's UI thread, so an answer also means the browser is not hung.

```sh
curl -sf http://127.0.0.1:7001/api/health
```
```json
{"ok":true,"state":"target"}
```

### GET /api/status

```sh
curl -s http://127.0.0.1:7001/api/status
wget -qO- http://127.0.0.1:7001/api/status
```
```json
{"ok":true,"name":"left-screen","appId":"left-screen","pid":233085,
 "url":"http://localhost:8080/home","targetUrl":"http://localhost:8080/",
 "state":"target","loadFailures":0,"split":null,"devtools":false,"fps":false,"kiosk":true,
 "version":["PySide6 6.12.0, Qt 6.12.0, QtWebEngine 6.140.0","Chromium 140.0.7339.225 (security patches up to 152.0.7977.83)"]}
```
`state` is `idle` (loading screen, waiting for a URL), `connecting` (loading screen, first attempt), `loading` (loading screen, retrying) or
`target` (the page is shown). `split` is `null` or `{"url","state"}` for the second pane.

### POST /api/navigate `{"url", "pane"?}`

Loads a new page through the loading screen; the retry loop restarts for the new URL. `pane` is `1`
(the main page, default) or `2` (the second pane of a split; created if there is none).

Besides web addresses and local files, `url` accepts the built-in pages `kioskium:test` (diagnostics),
`kioskium:input` (input test) and `kioskium:idle` (the loading screen, waiting for the next URL).
An unknown `kioskium:` name returns 400.

```sh
curl -s -d '{"url":"http://localhost:8080/other"}' http://127.0.0.1:7001/api/navigate
wget -qO- --post-data='{"url":"http://localhost:8080/other"}' http://127.0.0.1:7001/api/navigate
curl -s -d '{"url":"http://localhost:8080/side","pane":2}' http://127.0.0.1:7001/api/navigate
wget -qO- --post-data='{"url":"http://localhost:8080/side","pane":2}' http://127.0.0.1:7001/api/navigate
```
```json
{"ok":true,"pane":1}
```

### POST /api/reload `{"pane"?}`

```sh
curl -s -d '{}' http://127.0.0.1:7001/api/reload
wget -qO- --post-data='{}' http://127.0.0.1:7001/api/reload
```

### POST /api/split `{"url", "orientation"?, "ratio"?}` and POST /api/unsplit

A second page in the same window. `orientation`: `horizontal` (default, side by side) or
`vertical`; `ratio`: the first pane's share in percent (default 50). Calling `split` again
re-points the second pane.

```sh
curl -s -d '{"url":"http://localhost:8080/side","orientation":"horizontal","ratio":40}' http://127.0.0.1:7001/api/split
wget -qO- --post-data='{"url":"http://localhost:8080/side","orientation":"horizontal","ratio":40}' http://127.0.0.1:7001/api/split
curl -s -d '{}' http://127.0.0.1:7001/api/unsplit
wget -qO- --post-data='{}' http://127.0.0.1:7001/api/unsplit
```

### POST /api/devtools `{"action": "open" | "close" | "toggle"}`

In-window inspector pane under the page; works in kiosk mode, no flag needed.

```sh
curl -s -d '{"action":"open"}' http://127.0.0.1:7001/api/devtools
wget -qO- --post-data='{"action":"open"}' http://127.0.0.1:7001/api/devtools
```
```json
{"ok":true,"devtools":true}
```

### POST /api/fps `{"enabled": true | false | "toggle"}`

Shows or hides a frame-rate overlay in every pane (`--fps` / `--fps-corner` at startup). Each pane
is its own Chromium renderer with its own frame clock, so the number is per pane: the rate at which
that page actually produces frames (capped by the display refresh), with the minimum of the last
five seconds. Green ≥ 50, yellow ≥ 30, red below. Pages can read it as `window.__kioskiumFps.fps`.

```sh
curl -s -d '{"enabled":true}' http://127.0.0.1:7001/api/fps
wget -qO- --post-data='{"enabled":true}' http://127.0.0.1:7001/api/fps
kioskctl --port 7001 js "window.__kioskiumFps.fps" --pane 2
```
```json
{"ok":true,"fps":true}
```

### POST /api/screenshot `{"path"}`

Composed capture of the whole window (page, second pane, DevTools) as PNG, written on
the machine running the browser. On Windows, write the path with forward slashes
(`C:/temp/shot.png`): a backslash would have to be escaped in JSON.

```sh
curl -s -d '{"path":"/tmp/shot.png"}' http://127.0.0.1:7001/api/screenshot
wget -qO- --post-data='{"path":"/tmp/shot.png"}' http://127.0.0.1:7001/api/screenshot
```

### POST /api/js `{"code", "pane"?}`

Evaluates JavaScript in the main page (or the second pane with `"pane": 2`) and returns the result
(JSON-serialisable values; others as strings). Useful for checks and automation: read app state,
click a button, test a selector.

```sh
curl -s -d '{"code":"document.title"}' http://127.0.0.1:7001/api/js
wget -qO- --post-data='{"code":"document.title"}' http://127.0.0.1:7001/api/js
curl -s -d '{"code":"document.querySelector(\"button\").click(); location.href"}' http://127.0.0.1:7001/api/js
wget -qO- --post-data='{"code":"document.querySelector(\"button\").click(); location.href"}' http://127.0.0.1:7001/api/js
```
```json
{"ok":true,"pane":1,"result":"My Application"}
```

- The value of the last statement is returned, like in the DevTools console: `var n = 6; n * 7`
  gives `42`.
- Arrays and objects come back as JSON (`[innerWidth, innerHeight]` gives `[1280, 720]`).
  `undefined` becomes `null`, dates become ISO strings, DOM nodes, functions and promises become
  their string form (promises are not awaited).
- A JavaScript error returns HTTP 500 with `{"ok": false, "error": "JavaScript error: ReferenceError: x is not defined"}`
  (`kioskctl` exits with 1).
- Pages whose Content-Security-Policy forbids `eval` still run the code, but then only strings,
  numbers and booleans come back; other values and errors arrive as `""`.

### POST /api/quit

Ends the browser process cleanly (window closes, registry entry removed, `quit` event sent, exit
code 0).

```sh
curl -s -d '{}' http://127.0.0.1:7001/api/quit
wget -qO- --post-data='{}' http://127.0.0.1:7001/api/quit
```

### GET /api/events — see section 3.

---

## 2. Command-line client

Every command and option is in [KIOSKCTL.md](KIOSKCTL.md); this is the short version.

`kioskctl` (bundles), `kioskctl` (pip install), or `kiosk ctl ...`. A browser is chosen by its
**name** (`-n NAME`, before or after the command; not needed with only one running), which defaults
to its app-id; the app-id itself only decides which output the window goes to on Weston. When the
daemon (section 8) runs on the machine, `open`, `close`, `restart` and `list` go through it. Global
options:

```
-n NAME            the browser, by name             --port PORT       talk to this control port
--socket PATH      Unix socket instead of port      --token T         token (or env KIOSKIUM_TOKEN)
--json             one compact JSON document        --daemon-url URL  a daemon on another machine
--timeout SEC      default 10
```

Exit codes: `0` ok, `1` the browser refused the command (`"ok": false`), `2` not found / not
reachable, `3` `open`: that name is already running. The full reference is
[KIOSKCTL.md](KIOSKCTL.md).

| Command | Example | Notes |
|---|---|---|
| `open [URL] [--app-id ID] [-n NAME] [--restart P] [-- OPTIONS]` | `kioskctl open kioskium:test -- --windowed` | opens a browser (default app-id and name `kioskium`) |
| `close [--all]` | `kioskctl close -n left-screen` | closes it (`quit`, `exit` are the same) |
| `restart` | `kioskctl restart -n left-screen` | browsers the daemon opened |
| `list` | `kioskctl list` | every browser and its state; `--json list` → `{"ok":true,"instances":[…]}` |
| `status` | `kioskctl status -n left-screen` | same JSON as `GET /api/status` |
| `navigate URL [--pane 1\|2]` | `kioskctl --port 7001 navigate http://localhost:8080/side --pane 2` | `--pane 2` changes the second pane of a split |
| `reload [--pane 1\|2]` | `kioskctl --port 7001 reload` | |
| `split URL [--orientation horizontal\|vertical] [--ratio N]` | `kioskctl --port 7001 split http://localhost:8080/side --ratio 40` | |
| `unsplit` | `kioskctl --port 7001 unsplit` | |
| `devtools [open\|close\|toggle]` | `kioskctl --port 7001 devtools open` | default `toggle` |
| `fps [on\|off\|toggle]` | `kioskctl --port 7001 fps on` | frame-rate overlay in every pane, default `toggle` |
| `screenshot PATH` | `kioskctl --port 7001 screenshot /tmp/shot.png` | |
| `js CODE [--pane 1\|2]` | `kioskctl --port 7001 js "document.title" --pane 2` | |
| `events [--since N] [--count N]` | `kioskctl --port 7001 events` | one JSON object per line, Ctrl+C to stop |

A quick tour:

```sh
kioskctl list
kioskctl --json status -n left-screen
kioskctl --port 7001 split kioskium:input --ratio 40
kioskctl --port 7001 devtools open
kioskctl --port 7001 js "document.title"
kioskctl --port 7001 screenshot /tmp/shot.png
kioskctl --port 7001 unsplit
kioskctl --port 7001 close
```

PowerShell:

```powershell
.\kioskctl.exe --json list | ConvertFrom-Json
Invoke-RestMethod http://127.0.0.1:7001/api/status
Invoke-RestMethod -Method Post -ContentType 'application/json' -Body '{"url":"http://localhost:8080/side","ratio":40}' http://127.0.0.1:7001/api/split
```

---

## 3. Events (Server-Sent Events)

`GET /api/events[?since=SEQ]` keeps the connection open and streams:

```
id: 12
event: load
data: {"pane":1,"status":"finished","url":"http://localhost:8080/home"}

```

A comment line `: ping` arrives every 15 s as a heartbeat. The last 200 events are buffered;
`?since=SEQ` replays everything after sequence `SEQ` (use the last `id` you saw).

| event | data | when |
|---|---|---|
| `load` | `status: started\|finished\|failed`, `url`, on failure `error`, `attempt`, `retryIn` | page loads and retries (not the loading screen itself) |
| `navigate` | `url` | `/api/navigate`, a second launch with the same name (section 5) |
| `crash` | `status`, `exitCode` | render process died (the page is reloaded) |
| `split` / `unsplit` | `url`, `orientation`, `ratio` | second pane added / removed |
| `devtools` | `visible` | inspector shown / hidden |
| `fps` | `enabled` | frame-rate overlay shown / hidden |
| `quit` | | browser exiting (the stream closes afterwards) |

Every event carries `pane` (1 = main page, 2 = second pane).

```sh
kioskctl --port 7001 events                 # {"seq":12,"event":"load","pane":1,"status":"finished","url":"..."}
kioskctl --port 7001 events --since 12 --count 5
curl -sN http://127.0.0.1:7001/api/events     # raw SSE
wget -qO- http://127.0.0.1:7001/api/events        # raw SSE
```

.NET: [examples/ControlClient.cs](../examples/ControlClient.cs) (`EventsAsync()` yields `(name, data)`;
with the daemon, see section 8).

---

## 4. JavaScript API in the page (`window.kioskium`)

Available in every page unless `--no-js-api`. Methods are asynchronous (Qt WebChannel); those that
return a value resolve a Promise. Wait for the `kioskiumReady` document event if you need it
before the page has loaded.

```js
document.addEventListener('kioskiumReady', () => console.log('bridge ready'));

window.kioskium.openDevTools();      // closeDevTools(), toggleDevTools()
window.kioskium.reload();
window.kioskium.split('http://localhost:8080/side');
window.kioskium.splitWith('http://localhost:8080/side', 'vertical', 60);
window.kioskium.unsplit();
window.kioskium.showFps(true);       // frame-rate overlay (false hides it)
window.kioskium.info().then(j => console.log(JSON.parse(j)));   // {version:[...], name:"...", appId:"..."}
window.kioskium.log('hello from the page');                     // into the browser log
```

Try it without touching the page: `kioskctl --port 7001 js "window.kioskium.openDevTools()"`.

**Loading-screen hook** (custom `--loading-page FILE`): the browser calls
`window.kioskiumStatus({state, url, attempt, retryIn, error})` with `state` =
`connecting | retrying | crashed | idle` (`idle`: started without a URL, or `kioskium:idle`),
`attempt` = consecutive failures, `retryIn` = seconds to the next try. Define that function in your
page to show the status; images, fonts and CSS can be referenced relative to the HTML file.

---

## 5. Instance registry and a second launch

Each instance writes `<name>.json` (`name, appId, pid, port, socket, url, startedAt`, and `token` when
it has one) to
`$XDG_RUNTIME_DIR/kioskium/` (Linux) or `%LOCALAPPDATA%\kioskium\run\` (Windows) and removes it on
exit. The directory is private to the user, since it holds the tokens. The name (`--name`, default:
the app-id) is what identifies an instance; several may share an app-id. Readers verify the pid and ask the API to confirm `name`/`pid`, so an entry left by a crashed
or killed browser is reported `STALE` once and deleted. Other programs can read those files, or
call `GET /api/health` on known ports (no token needed). When the daemon runs, ask it instead (section 8).

Starting `kiosk` with a name that is already running doesn't open a second browser: the running
one is given the URL (it navigates there) and the new process exits 0. Without a URL it is left as
it is. The new launch's other options don't apply to a running browser; they are logged as ignored.
To run two browsers, give them different names.

```sh
kiosk --app-id left-screen URL          # starts the browser
kiosk --app-id left-screen URL2         # same name: the running browser now shows URL2; exit 0
kiosk --app-id left-screen --fail-if-running URL
echo $?                                 # 3: a browser with that name is already running
```

The second launch also exits 3 when the running browser has no control channel (`--no-control`) or
doesn't answer. `kioskctl open` and the daemon never hand over: for them the name is already taken.

---

## 6. Logs

| Flag | What it writes |
|---|---|
| `--log-file FILE` | the browser's own log (startup, loads, retries, control commands); rotating |
| `--page-log FILE` | the page's `console.log/warn/error` with source:line, uncaught JavaScript errors, load failures, render-process crashes; rotating |
| `--log-max-size MB`, `--log-backups N` | rotation for both (default 5 MB, 3 backups: `FILE`, `FILE.1`, `FILE.2`, `FILE.3`) |
| `--page-console` | also echo the page console into the main log at INFO |
| `--log-level debug` | everything, including the page console, in the main log |

`{name}` in the paths is replaced by the instance name, so instances can share one configuration:

```sh
kiosk --app-id left-screen --log-file /var/log/kioskium/{name}.log --page-log /var/log/kioskium/{name}-page.log URL
tail -f /var/log/kioskium/left-screen-page.log
```
```
2026-10-10 00:02:10,750 INFO    console.log [http://localhost:8080/assets/index.js:108] websocket ready
2026-10-10 00:02:11,142 ERROR   console.error [http://localhost:8080/assets/index.js:75] Uncaught ReferenceError: x is not defined
2026-10-10 00:03:01,003 WARNING load failed (1) http://localhost:8080/: net::ERR_CONNECTION_REFUSED
```

## 7. Signals and exit codes

| | |
|---|---|
| `SIGTERM`, `SIGINT` | clean quit (registry entry removed) |
| `SIGHUP` | reload the page |
| exit `0` | normal exit (`quit`, `--exit-after`, URL handed to a running browser of the same name) |
| exit `1` | fatal error (see the log, or `%LOCALAPPDATA%\kioskium\crash.log` for the windowed Windows build) |
| exit `2` | invalid command line or configuration file |
| exit `3` | a browser with that name is already running and could not be given the URL (or `--fail-if-running`) |

---

## 8. Daemon: start browsers over REST (`kioskctl serve`)

Everything above needs a browser that is already running. `kioskctl serve` is a small, always-running
process (no Qt, no Chromium) with one fixed port. It starts and stops browsers when asked and forwards
their control API, so software that can only make HTTP requests never needs a shell.

```sh
kioskctl serve                                             # http://127.0.0.1:7000/api/
kioskctl serve --instances /etc/kioskium/instances.json    # also start these at launch
```

Each browser gets exactly the options in its `args`; the daemon adds none of its own besides the
control channel, name and app-id. Options every browser on a machine needs (e.g.
`--show-cursor-timeout`) belong in `kioskium.json` next to `kiosk` (see the README's configuration
section), which every browser reads, whoever starts it.

| Method | Endpoint | Body | Does |
|---|---|---|---|
| GET | `/api/health` | | `{ok: true, daemon: true}`; no token needed (Origin and Host are still checked) |
| GET | `/api/status` | | `{ok, daemon: true, version, pid, restart, instances}` (`restart`: the default policy) |
| GET | `/api/instances` | | every instance the daemon manages, with its `state` |
| POST | `/api/instances` | `{"name"?, "appId"?, "url"?, "args"?, "restart"?, "wait"?}` | start a browser |
| GET | `/api/instances/NAME` | | the instance, plus its live `/api/status` while it runs |
| POST | `/api/instances/NAME/stop` | | quit it (it stays listed as `stopped`); `/quit` does the same |
| POST | `/api/instances/NAME/start` | | start a stopped instance again with the same settings |
| POST | `/api/instances/NAME/restart` | | stop, then start |
| DELETE | `/api/instances/NAME` | | stop it and remove it from the list |
| any | `/api/instances/NAME/<endpoint>` | as in section 1 | forwarded to that browser: `navigate`, `js`, `split`, `events`, ... |
| GET | `/api/events[?since=N]` | | the daemon's own events (below) |

`kioskctl` on the same machine uses the daemon by itself (`kioskctl open`, `close`, `restart`,
`list`, ...); see [KIOSKCTL.md](KIOSKCTL.md#rest-and-kioskctl).


- `name` identifies the browser in every later request (`/api/instances/NAME/...`). It defaults to
  `appId`, which defaults to `kioskium`.
- `appId` is the browser's `--app-id`: on Wayland it picks the output (see `--list-app-ids`). Several
  browsers may share one if their names differ.
- `args` are kiosk options as a list, e.g. `["--loading-status", "--split", "http://x/b"]`.
- `restart`: `on-failure` (restart when the browser exits with an error or is killed), `always`
  (also when its window is closed or it quits by itself), or `no`. Only stop or close through the
  daemon keeps an `always` browser closed. Restarts back off 1, 2, 4, ... up to 30 s; a run longer than
  a minute resets it. The default is the daemon's `--restart` (`on-failure` unless set).
- `wait` (default 60 s): how long the POST waits for the page's control API before it answers.
  The reply says `"state": "running"`, or `"starting"` if the browser is still coming up. When the
  browser exits before it is ready (a bad option, no display), the reply is 500 with its last output
  lines, and it is not restarted.
- States: `starting`, `running`, `restarting`, `exited` (ended normally, no restart), `failed`, `stopped`.
- 409 when the name is already running (also when it was started outside the daemon), or when a
  request is forwarded to an instance that is not running.

```sh
curl -s -d '{"appId":"infotainment-screen","url":"http://localhost:8080/"}' http://127.0.0.1:7000/api/instances
curl -s -d '{"url":"http://localhost:8080/route"}' http://127.0.0.1:7000/api/instances/infotainment-screen/navigate
curl -s -d '{"code":"document.title"}' http://127.0.0.1:7000/api/instances/infotainment-screen/js
curl -sN http://127.0.0.1:7000/api/instances/infotainment-screen/events
curl -s -X POST http://127.0.0.1:7000/api/instances/infotainment-screen/stop

wget -qO- --post-data='{"appId":"left-screen"}' http://127.0.0.1:7000/api/instances   # busybox has no DELETE: use /stop
```
```powershell
$d = 'http://127.0.0.1:7000/api'
Invoke-RestMethod -Method Post "$d/instances" -ContentType application/json -Body '{"appId":"left-screen","url":"kioskium:test","args":["--windowed"]}'
Invoke-RestMethod -Method Post "$d/instances/left-screen/navigate" -ContentType application/json -Body '{"url":"https://example.com"}'
Invoke-RestMethod -Method Delete "$d/instances/left-screen"
```

**.NET:** [examples/ControlClient.cs](../examples/ControlClient.cs) (one file, no packages, .NET 6+).
When the daemon runs as a service, the application talks only to it: `KioskiumDaemon` opens, restarts
and closes browsers by name, and `Instance(name)` returns a `KioskiumClient` for one browser through
`/api/instances/NAME/...`. `KioskOptions` has a property for every option the daemon accepts
(`Windowed`, `Size`, `Split`, `ShowCursor`, ...) and `Args` for anything else. `KioskiumClient(port)` talks to a browser's own port when there is no
daemon. Failed requests throw `KioskiumException` with the status (409: already running, or not running).

```csharp
using var kiosk = new KioskiumDaemon("http://127.0.0.1:7000", token);
await kiosk.OpenAsync(name: "info", appId: "infotainment-screen", url: "http://localhost:8080/",
    options: new KioskOptions { Windowed = true, Size = "1280x800" });
var info = kiosk.Instance("info");
await info.NavigateAsync("http://localhost:8080/route");
await foreach (var (name, data) in info.EventsAsync(ct)) { /* load, navigate, crash, ... */ }
await kiosk.CloseAsync("info");
```

Any HTTP client works; send request bodies with a `Content-Length`. Chunked bodies (what .NET's
`JsonContent` sends) are refused with 411.

**Postman:** import [examples/kioskium-daemon.postman_collection.json](../examples/kioskium-daemon.postman_collection.json)
(`baseUrl` defaults to `http://127.0.0.1:7000`, `name` to `postman-test`). Run **Instances / Start
instance** first; the folders after it can run in order, except **Events**, whose streams stay open.

**Daemon events** (`GET /api/events`, same format as section 3): `started {name, pid}`,
`ready {name, pid}`, `exited {name, exitCode}`, `restarting {name, exitCode, delay}`,
`stopped {name, exitCode}`, `failed {name, exitCode, output}`. A crashed browser shows up here
even though its own event stream ended with it.

**How it reaches the browsers.** Each browser it starts gets a private control channel: a Unix socket
in the runtime directory on Linux, a free `127.0.0.1` port on Windows, always with a random token.
Callers only use the daemon's port. The registry lists these browsers, token included, so
`kioskctl list` and `kioskctl -n NAME ...` still work for debugging as the same user. Browser
output goes to the daemon's log, prefixed with the name.

**Security.**

| Option | |
|---|---|
| `--bind ADDR` | default `127.0.0.1`. Any other address requires `--token`. |
| `--token T` | require `X-Kioskium-Token: T` (or `Authorization: Bearer T`); env `KIOSKIUM_TOKEN` |
| `--allow-origin ORIGIN` | requests from web pages (with an `Origin` header) are refused unless their origin is listed; `*` allows any |
| `--restart POLICY` | the restart policy for browsers whose request or `--instances` entry sets none (default `on-failure`); `always` relaunches a browser whose window was closed |
| `--allow-unsafe-args` | let clients pass options that touch files or Chromium internals: `--chromium-flag`, `--qt-arg`, `--inject-js`, `--inject-css`, `--profile-dir`, `--log-file`, `--page-log`, `--download-dir`, `--screenshot`, `--loading-page`, `--config`, `--weston-ini`, `--remote-debugging-port`, `--allow-file-remote-access`, `@file`. The `--instances` file is the operator's own and is not restricted. |

While it listens on loopback, requests whose `Host` is not a loopback name are refused (DNS
rebinding). Options the daemon sets itself (`--name`, `--app-id`, `--control-*`, `--no-control`, `--fail-if-running`) are
refused in `args`; use the `name` and `appId` fields.

**`--instances FILE`** starts browsers when the daemon starts. The file holds the same fields as
`POST /api/instances`, as a list or as `{"instances": [...]}`. If the file can't be read or parsed,
the daemon logs why and starts without them, so the API stays reachable for fixing it. These
browsers are retried even when their first start fails, for example while the compositor is still
coming up:

```json
[
  {"appId": "infotainment-screen", "url": "http://localhost:8080/", "args": ["--loading-status"]},
  {"appId": "driver-screen", "name": "driver", "restart": "always"}
]
```

On `SIGTERM` or Ctrl+C the daemon quits every browser it started (10 s each, then terminates it).
The browsers are its children: they don't outlive it, and the next daemon starts them again from
`--instances`.

The browser it runs is the `kiosk` next to `kioskctl` in a bundle, otherwise `python -m kioskium`
from the same Python. On embedded Linux, run it as the compositor's user (see [EMBEDDED.md](EMBEDDED.md#running-the-daemon-with-systemd)).

# kiosk command line

Every option of `kiosk`, the browser. `kioskctl` has its own reference: [KIOSKCTL.md](KIOSKCTL.md).

Defaults without flags: kiosk mode (frameless fullscreen above other windows, the lock-down described
under Features), pointer hidden, touch events on, scrollbars hidden, loading screen with retries and
a 30 s load timeout, crash reload, a control channel found by name (`kioskctl -n NAME`), and
`window.kioskium` available to pages. Starting a name that already runs hands it the new URL.
`SIGTERM` quits, `SIGHUP` reloads.

**Window and placement**

| Flag | Meaning |
|---|---|
| `URL` | page, local file or built-in page (`kioskium:test`, `kioskium:input`, `kioskium:idle`) to open (env `KIOSKIUM_URL`). Without one, the loading screen waits until a URL is sent |
| `--config FILE`, `--no-config`, `--print-config` | JSON settings file, ignore it, print the effective settings |
| `--windowed` | normal decorated window instead of kiosk mode |
| `--size WxH[+X+Y]` | window size with `--windowed`, optionally with a position |
| `--screen NAME\|INDEX` | screen to show on (`HDMI-A-1`, or an index from `--list-screens`) |
| `--list-screens` | list screens and exit |
| `--app-id ID` | Wayland xdg `app_id`; the compositor can map it to an output (env `KIOSKIUM_APP_ID`) |
| `--list-app-ids` | app-ids the compositor maps to outputs (from `weston.ini`) and which are running |
| `--weston-ini PATH` | `weston.ini` to read for that (default: `$XDG_CONFIG_HOME`, `~/.config`, `/etc/xdg`; env `KIOSKIUM_WESTON_INI`) |
| `--name NAME` | instance name: how `kioskctl -n`, the registry and a second launch identify it (several instances may share an app-id). Also replaces `{name}` in `--log-file`, `--page-log`, `--profile-dir`, `--screenshot` (or is added before the extension); default: the app-id |
| `--background COLOR` | colour behind the page |
| `--show-cursor` | show the pointer (kiosk mode hides it by default; `--windowed` always shows it) |
| `--show-cursor-timeout SEC` | show the pointer while a mouse moves, hide it after SEC idle seconds; touch never shows it |
| `--text-selection` | allow text selection / long-press handles (off by default; form fields always editable) |
| `--context-menu` | allow the right-click / long-press menu (off by default) |
| `--fps`, `--fps-corner CORNER` | frame-rate overlay in every pane; also at runtime: `kioskctl fps on` |

**Split screen**

| Flag | Meaning |
|---|---|
| `--split URL2` | second page in the same window; also at runtime (control API, `window.kioskium.split()`) |
| `--split-orientation horizontal\|vertical` | side by side (default) or stacked |
| `--split-ratio PERCENT` | first page's share (default 50) |
| `--split-gap PX` | divider width (default 0) |

**Platform, GPU and input**

| Flag | Meaning |
|---|---|
| `--opengl auto\|es\|desktop` | GL flavour Qt requests; `es` also forces an RGBA8888 surface. `auto` picks `es` on Linux arm |
| `--no-rgba-surface` | diagnostic: skip the RGBA surface with OpenGL ES |
| `--no-gpu` | software rendering |
| `--gpu-rasterization` | draw page tiles on the GPU (off by default: on small GPU memory pools such as i.MX it causes black, flickering tiles) |
| `--sandbox` | enable the Chromium sandbox (off by default: it refuses to run as root and needs user namespaces, which embedded kernels and services often lack). `--no-sandbox` is still accepted |
| `--no-touch` | disable touch events (on by default); touch then arrives as mouse events |
| `--platform QPA` | Qt platform plugin: `wayland`, `xcb`, `eglfs`, `windows`, `offscreen` |
| `--qt-arg ARG`, `--chromium-flag FLAG` | pass-through to Qt / Chromium (repeatable) |
| `--remote-debugging-port PORT` | Chromium DevTools over the network (`chrome://inspect`) |

**Content**

| Flag | Meaning |
|---|---|
| `--profile-dir PATH` | persistent cookies / storage / cache (default: in-memory, nothing written to disk) |
| `--cache-size MB` | HTTP cache limit (default: 32 MB in memory, 100 MB with `--profile-dir`; `0` = Chromium decides) |
| `--allow-host PATTERN` | restrict main-frame navigation to matching hosts (glob, repeatable; the start URL's host is always allowed) |
| `--allow-file-remote-access` | let `file://` pages load remote URLs (e.g. a local loader page that redirects to a server) |
| `--ignore-certificate-errors` | accept self-signed TLS |
| `--allow-new-windows` | `window.open()` opens a new kiosk window instead of the same view |
| `--allow-dialogs` | show JavaScript alert/confirm/prompt instead of auto-dismissing |
| `--grant-permissions` | auto-grant camera/mic/geolocation |
| `--download-dir DIR` | allow downloads into DIR |
| `--user-agent UA` | override the User-Agent |
| `--show-scrollbars` | show page scrollbars (hidden by default; pages still scroll) |
| `--inject-js FILE`, `--inject-css FILE` | your own script / style in every page (repeatable) |

**Loading screen and resilience**

| Flag | Meaning |
|---|---|
| `--loading-page FILE` | your own HTML instead of the built-in screen; receives status via `window.kioskiumStatus` ([details](API.md#4-javascript-api-in-the-page-windowkioskium)) |
| `--loading-title TEXT`, `--loading-detail TEXT` | title (default `Loading…`) and an optional line under the bar (default none) |
| `--loading-status` | also show the retry countdown, the last load error and crash recovery on the built-in screen |
| `--retry-interval SEC` | base delay for load retries (exponential, max 60 s; default 5) |
| `--load-timeout SEC` | abort and retry a load slower than SEC (default 30; `0` = never) |
| `--reload-interval SEC` | reload periodically |
| `--no-reload-on-crash` | don't reload after a render-process crash |

**Control channel**

| Flag | Meaning |
|---|---|
| `--control-port PORT` | the control API on `127.0.0.1:PORT` instead of the default channel; `0` = any free port. No token unless `--control-token` |
| `--control-socket PATH` | the same API on this Unix socket instead |
| `--control-token TOKEN` | require `X-Kioskium-Token` on control requests |
| `--no-control` | no control channel. By default every browser has one: a socket (Linux) or free port (Windows) with a random token, in the registry |
| `--fail-if-running` | exit with code 3 if a browser with this name is running. Default: hand it the URL and exit 0 |

**DevTools, logging and testing**

| Flag | Meaning |
|---|---|
| `--devtools` | enable the F12 key for the in-window inspector (the pane also opens via API without this flag) |
| `--devtools-open`, `--devtools-key KEYSEQ` | start with the inspector visible; toggle key (default `F12`) |
| `--no-js-api` | don't expose `window.kioskium` to pages |
| `--log-level debug\|info\|warning\|error`, `--log-file FILE` | logging (`--log-file` rotates) |
| `--page-log FILE` | rotating log of the page's console, JavaScript errors, load failures and crashes |
| `--page-console` | also log the page console at INFO in the main log |
| `--log-max-size MB`, `--log-backups N` | rotation (default 5 MB, 3 backups) |
| `--exit-shortcut KEYSEQ` | a key that quits the kiosk, e.g. `Ctrl+Shift+Q` (none by default) |
| `--allow-shortcuts` | don't block navigation keys |
| `--exit-after SEC`, `--screenshot FILE`, `--screenshot-delay SEC` | automated tests |
| `--version` | Kioskium, Qt and Chromium versions |

Options can also come from a JSON file (`kioskium.json` next to the executable, `--config FILE`) or a
text file of arguments (`kiosk @FILE`); see the README's configuration section.

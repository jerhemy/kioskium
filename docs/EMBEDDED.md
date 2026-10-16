# Kioskium on embedded Linux

Running Kioskium on Wayland boards (i.MX, Rockchip, Raspberry Pi, Intel/AMD panel PCs) with Weston
or another compositor: placement on outputs, GPU acceleration and installing the prebuilt arm64
bundle.

## The prebuilt bundle

`kioskium-*-linux-arm64.tar.gz` is one folder with its own Python, Qt 6.8 and Chromium 122 (Qt 6.8
LTS line, with security backports). The device only needs glibc ≥ 2.31 (e.g. Yocto hardknott or
newer), Wayland, an EGL/GLES driver, fontconfig and freetype; nothing has to be built into the image.

The bundle is built in an Ubuntu 20.04 container (glibc 2.31) with PySide6 6.8.0.2, the newest
release with wheels for that glibc. It deliberately does **not** include the GPU/display stack
(`libEGL`, `libGLESv2`, `libgbm`, `libdrm`, `libwayland-*`, `libudev`) or `libstdc++`/`libgcc_s`:
vendor GPU drivers ship their own builds of those, and mixing in other copies breaks them.

To check that a bundle on a device uses nothing outside its folder except the system libraries,
list what its process tree has mapped:

```sh
./kiosk/kiosk --exit-after 20 http://localhost:8080/ &
sleep 10
for p in $(pgrep -x kiosk) $(pgrep -f QtWebEngineProcess); do awk '/\.so/{print $6}' /proc/$p/maps; done \
  | sort -u | grep -v "$PWD/kiosk/"
```

## Placing windows on outputs (Wayland)

Wayland clients can't position themselves; the compositor decides. Kioskium gives it two hints:

- **`--app-id ID`** sets the xdg-shell `app_id` (Qt's `QGuiApplication::desktopFileName`). Kiosk
  shells and Weston configurations commonly map app-ids to outputs, e.g. in `weston.ini`:
  ```ini
  [output]
  name=HDMI-A-1
  app-ids=left-screen[0;0;100%;100%]
  ```
  `kiosk --list-app-ids` reads `weston.ini` (from `$XDG_CONFIG_HOME`, `~/.config`, `/etc/xdg`, or
  `--weston-ini PATH`) and shows each output's app-ids and whether a Kioskium instance is running
  with it.
- **`--screen NAME|INDEX`** asks for fullscreen on that `wl_output`; most compositors honour it.
  `kiosk --list-screens` prints the names Qt sees.

One browser per screen is simply one process per screen, each with its own app-id; each gets its
own control channel automatically. The app-id only places the window; `kioskctl` and the registry know a
browser by its name (`--name`), which defaults to the app-id.

## GPU acceleration

Rendering is GPU accelerated by default (GPU compositing, zero-copy, accelerated canvas;
`--no-gpu` forces software for comparison). Page tiles are drawn by the CPU: Chromium's GPU
rasterization (`--gpu-rasterization`) fills small GPU memory pools (the Vivante driver on i.MX has
one fixed pool, often 256 MB, shared by every app) and shows up as black or flickering tiles once
a few browsers share it. To confirm on a target: `kiosk chrome://gpu`, or
`kiosk kioskium:test` — its WebGL renderer line must name your GPU
(Vivante, Mali, Adreno, Intel, ...), not SwiftShader or llvmpipe. `--fps` shows the frame rate.

**GLES-only GPUs** (Vivante on i.MX, many Mali drivers). The PySide6 wheels contain a desktop-GL Qt
build; asking such a driver for a desktop OpenGL context fails (`QEGLPlatformContext: Failed to
create context: 3004`). On Linux arm, Kioskium therefore requests OpenGL ES automatically
(`--opengl auto`; force it elsewhere with `--opengl es`). With OpenGL ES it also requests an 8-bit
RGBA window surface: compositors that blit client buffers with a 2D engine (i.MX Weston with
`use-g2d=1`) only accept ARGB8888/XRGB8888, and any other EGL config shows up as **wrong colours**
(Weston logs `g2d_getG2dFormat, Format not supported`). `--no-rgba-surface` reproduces the problem
for diagnosis.

**The libGL shim in the arm64 bundle.** Qt's Wayland EGL integration calls `glFlush()` through
`libGL.so.1`. On i.MX that resolves to Vivante's desktop-GL library, which crashes when the current
context is OpenGL ES. The bundle ships libglvnd's `libGL.so.1` (dispatching to the current
context's vendor), which turns the call into a harmless no-op. Rendering itself goes through
EGL/GLES on the GPU.

If Chromium still falls back to software: check `--log-level debug`, the DRM/GPU device permissions
of the service user, and try `--chromium-flag=--use-gl=egl` or `--chromium-flag=--use-angle=gles`.
For hardware video decode, follow your BSP's V4L2/VA-API instructions via
`--chromium-flag=--enable-features=...`.

## Typical embedded flags

| Flag | Why |
|---|---|
| `--show-cursor-timeout 3` | touch panels often present as a mouse; the pointer is hidden by default, and this shows it while a real mouse moves (e.g. over VNC/RDP) |
| `--log-file`, `--page-log` | rotating logs, e.g. under a RAM-backed log directory |

`Detected locale "C" ... not UTF-8` is harmless on images without locales.

## Installing the prebuilt bundle

Releases are plain archives: unpack one and run it. Keeping each version in its own folder behind a
symlink makes upgrades and rollbacks a one-line change.

```sh
VERSION=1.0.0
mkdir -p /opt/kioskium-$VERSION
tar -xzf kioskium-$VERSION-linux-arm64.tar.gz -C /opt/kioskium-$VERSION --strip-components=1
ln -sfn /opt/kioskium-$VERSION /opt/kioskium          # the version in use

/opt/kioskium/kiosk --version                         # Kioskium, Qt and Chromium versions
/opt/kioskium/kiosk kioskium:test                      # GPU, frame rate and kiosk checks on this screen
```

- **Settings:** put the launch arguments in `/etc/kioskium.conf` (one per line, used with
  `kiosk @/etc/kioskium.conf`), or a `kioskium.json` next to the executable, which is loaded
  automatically. Keeping them under `/etc` means an upgrade never touches them.
- **Upgrade:** unpack the new version into its own folder, run `kiosk --version` from it, point the
  link at it (`ln -sfn /opt/kioskium-NEW /opt/kioskium`) and restart Kioskium.
- **Roll back:** point the link at the previous folder and restart.
- **Remove:** stop Kioskium and delete `/opt/kioskium*` and your settings file.
- `kioskctl` is in the same folder; link it into the `PATH` if you use it often:
  `ln -sfn /opt/kioskium/kioskctl /usr/local/bin/kioskctl`.

How Kioskium is started at boot (an init script, a service manager, the compositor's autostart,
or your own application launching it) is up to you. Start it as the compositor's user so
`XDG_RUNTIME_DIR` and `WAYLAND_DISPLAY` match. For supervisors, the signals and exit codes are in
[API.md](API.md#7-signals-and-exit-codes): `SIGHUP` reloads the page, `SIGTERM` quits cleanly with
exit code 0. Starting `kiosk` again with the same name hands its URL to the running browser
(exit 0) instead of opening a second one; `--fail-if-running` exits 3 instead.

## Running the daemon with systemd

If other software should start and stop browsers over HTTP, run `kioskctl serve` as a service
([API.md, section 8](API.md#8-daemon-start-browsers-over-rest-kioskctl-serve)). The daemon itself
is a small Python process; the browsers it starts are its child processes. Example
`/etc/systemd/system/kioskium.service`, for a Weston running as root:

```ini
[Unit]
Description=Kioskium daemon
After=weston.service
Wants=weston.service

[Service]
Environment=XDG_RUNTIME_DIR=/run/user/0 WAYLAND_DISPLAY=wayland-0
# --restart always: a browser whose window gets closed is opened again
ExecStart=/opt/kioskium/kioskctl serve --instances /etc/kioskium/instances.json --restart always
# SIGTERM goes to the daemon only: it quits its browsers cleanly, then exits
KillMode=mixed
TimeoutStopSec=30
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

Options every browser needs (e.g. `--show-cursor-timeout`) go in `/opt/kioskium/kioskium.json`,
for example `{"show-cursor-timeout": 3}`; per-browser options go in that browser's
`args`. `/etc/kioskium/instances.json` lists the browsers to open at start-up, for example:

```json
{"instances": [{"appId": "left-screen", "url": "https://example.com/"}]}
```

Then `systemctl enable --now kioskium`, and `journalctl -u kioskium -f` shows the daemon and every
browser's log. Leave out `--instances` to open browsers only when asked.

`systemctl restart kioskium` closes the browsers and starts them again, which is also what an
upgrade does.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Failed to create context: 3004` | the GPU only does OpenGL ES: `--opengl es` (automatic on Linux arm) |
| Wrong colours | RGBA surface missing: use OpenGL ES (default on arm); check the compositor log for G2D format errors |
| Window on the wrong output | the app-id must match the compositor's rules: `--list-app-ids`, `--list-screens` |
| Pointer needed over remote access | `--show-cursor-timeout 3` (the pointer is hidden by default in kiosk mode) |
| Blank page from a `file://` loader that calls a local server | `--allow-file-remote-access` |
| Crash right after start with a desktop-GL driver present | the libGL shim is missing: use the release bundle, or `KIOSKIUM_EXTRA_LIBS` when building your own (see `packaging/linux-arm64/Dockerfile`) |
| Running instances / ports | `kioskctl list`; registry in `$XDG_RUNTIME_DIR/kioskium/` |
| What is in effect? | `kiosk --print-config [flags]` |

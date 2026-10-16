import pytest

from kioskium.cli import DEFAULT_APP_ID, browser_identity, cache_size_mb, parse_args


def test_browser_identity_matches_what_the_browser_registers_as():
    for name, app_id in ((None, None), (None, "left-screen"), ("menu", "left-screen"), ("lobby", None), ("my menu", None)):
        final, args = browser_identity(name, app_id)
        assert parse_args(["--no-config", *args]).name == final
    assert browser_identity(None, None) == ("kioskium", ["--app-id", "kioskium"])  # not a config file's app-id
    assert browser_identity(None, "left-screen") == ("left-screen", ["--app-id", "left-screen"])  # name = app-id: no --name
    assert browser_identity("menu", "left-screen") == ("menu", ["--app-id", "left-screen", "--name", "menu"])
    assert browser_identity("my menu", None)[0] == "my-menu"


def test_defaults():
    a = parse_args([])
    assert a.url == "kioskium:idle"  # no URL: loading screen until one is sent
    assert a.kiosk is True
    assert a.app_id == DEFAULT_APP_ID
    assert a.chromium_flag == []
    assert a.no_gpu is False


def test_cursor_and_fps_flags():
    a = parse_args([])
    assert a.hide_cursor is True and a.cursor_timeout == 0.0  # kiosk mode hides the pointer
    assert parse_args([]).touch is True and parse_args(["--no-touch"]).touch is False  # touch events on by default
    for argv in ([], ["--windowed"]):  # off in every mode unless asked for
        b = parse_args(argv)
        assert b.text_selection is False and b.context_menu is False
    assert parse_args(["--windowed"]).hide_cursor is False
    assert parse_args(["--show-cursor"]).hide_cursor is False
    b = parse_args(["--windowed", "--show-cursor-timeout", "3"])
    assert b.hide_cursor is True and b.cursor_timeout == 3.0
    with pytest.raises(SystemExit):
        parse_args(["--show-cursor", "--show-cursor-timeout", "3"])
    assert a.fps is False and a.fps_corner == "top-right"
    a = parse_args(["--show-cursor-timeout", "2.5", "--fps", "--fps-corner", "bottom-left"])
    assert a.hide_cursor is True and a.cursor_timeout == 2.5
    assert a.fps is True and a.fps_corner == "bottom-left"


def test_opengl_auto_resolution():
    from kioskium.app import resolve_opengl

    assert resolve_opengl("auto", "linux", "aarch64") == "es"
    assert resolve_opengl("auto", "linux", "armv7l") == "es"
    assert resolve_opengl("auto", "linux", "x86_64") == "auto"
    assert resolve_opengl("auto", "win32", "AMD64") == "auto"
    assert resolve_opengl("desktop", "linux", "aarch64") == "desktop"
    assert resolve_opengl("es", "win32", "AMD64") == "es"


def test_windowed_and_url():
    a = parse_args(["--windowed", "--size", "800x600", "https://example.com"])
    assert a.kiosk is False
    assert a.url == "https://example.com"
    assert a.size == "800x600"


def test_repeatable_options():
    a = parse_args(["--chromium-flag=--foo", "--chromium-flag=--bar=1", "--allow-host", "a.com", "--allow-host", "*.b.com"])
    assert a.chromium_flag == ["--foo", "--bar=1"]
    assert a.allow_host == ["a.com", "*.b.com"]


def test_qt_arg_with_leading_dash_uses_equals_form():
    a = parse_args(["--qt-arg=-plugin", "--qt-arg=evdevtouch"])
    assert a.qt_arg == ["-plugin", "evdevtouch"]


def test_args_from_file(tmp_path):
    conf = tmp_path / "kioskium.conf"
    conf.write_text("# comment\n\n--app-id=sync-left\n--screen=HDMI-A-1\n--allow-host=example.com\nhttps://example.com/app\n")
    a = parse_args([f"@{conf}"])
    assert a.app_id == "sync-left"
    assert a.screen == "HDMI-A-1"
    assert a.allow_host == ["example.com"]
    assert a.url == "https://example.com/app"


def test_instance_name_in_file_paths():
    # default name = app-id: only the placeholder is substituted
    a = parse_args(["--no-config", "--app-id", "left-screen.x", "--log-file", "/l/sb-{name}.log", "--page-log", "/l/page.log"])
    assert a.name == "left-screen.x"
    assert a.log_file == "/l/sb-left-screen.x.log" and a.page_log == "/l/page.log"
    # explicit name: placeholder substituted, otherwise inserted before the extension
    b = parse_args(["--no-config", "--name", "driver", "--log-file", "/l/sb-{name}.log", "--page-log", "/l/page.log",
                    "--profile-dir", "/var/lib/sb", "--screenshot", "shot.png"])
    assert b.name == "driver"
    assert b.log_file == "/l/sb-driver.log" and b.page_log == "/l/page-driver.log"
    assert b.profile_dir == "/var/lib/sb-driver" and b.screenshot == "shot-driver.png"
    # unsafe characters never reach the file system
    c = parse_args(["--no-config", "--name", "a/b c", "--log-file", "x.log"])
    assert c.name == "a-b-c" and c.log_file == "x-a-b-c.log"
    assert parse_args(["--no-config"]).log_file is None


def test_kiosk_first_defaults():
    a = parse_args(["--no-config"])
    assert a.show_scrollbars is False and a.load_timeout == 30.0 and a.kiosk is True
    assert parse_args(["--no-config", "--load-timeout", "0"]).load_timeout == 0.0
    for gone in ("--app-id-prefix=x", "--no-toolbar", "--always-on-top", "--hide-scrollbars"):
        with pytest.raises(SystemExit):
            parse_args(["--no-config", gone])


def test_touch_events_flag_is_explicit_both_ways(monkeypatch):
    import os

    from kioskium.app import configure_environment

    for argv, flag in (([], "--touch-events=enabled"), (["--no-touch"], "--touch-events=disabled")):
        monkeypatch.setattr(os, "environ", {})  # configure_environment sets many variables; keep them out of os.environ
        configure_environment(parse_args(["--no-config", *argv]))
        assert flag in os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].split()


def test_gpu_rasterization_is_off_unless_asked(monkeypatch):
    import os

    from kioskium.app import configure_environment

    for argv, flag in (([], "--disable-gpu-rasterization"), (["--gpu-rasterization"], "--enable-gpu-rasterization")):
        monkeypatch.setattr(os, "environ", {})
        configure_environment(parse_args(["--no-config", *argv]))
        flags = os.environ["QTWEBENGINE_CHROMIUM_FLAGS"].split()
        assert flag in flags
        assert ("--enable-gpu-rasterization" in flags) != ("--disable-gpu-rasterization" in flags)


def test_sandbox_is_off_unless_asked(tmp_path):
    assert parse_args(["--no-config"]).no_sandbox is True
    assert parse_args(["--no-config", "--no-sandbox"]).no_sandbox is True  # still accepted
    assert parse_args(["--no-config", "--sandbox"]).no_sandbox is False
    cfg = tmp_path / "kioskium.json"
    cfg.write_text('{"no-sandbox": true}', encoding="utf-8")  # existing config files keep working
    assert parse_args(["--config", str(cfg)]).no_sandbox is True


def test_control_port_zero_means_any_port():
    assert parse_args(["--no-config", "--control-port", "0"]).control_port == 0
    assert parse_args(["--no-config"]).control_port is None


def test_env_defaults(monkeypatch):
    monkeypatch.setenv("KIOSKIUM_URL", "https://env.example/")
    monkeypatch.setenv("KIOSKIUM_APP_ID", "sync-env")
    a = parse_args([])
    assert a.url == "https://env.example/"
    assert a.app_id == "sync-env"


def test_builtin_urls():
    assert parse_args(["kioskium:test"]).url == "kioskium:test"
    assert parse_args(["kioskium:input"]).url == "kioskium:input"
    assert parse_args([""]).url == "kioskium:idle"


def test_unknown_builtin_page_is_rejected(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        parse_args(["kioskium:nope"])
    assert exc.value.code == 2
    assert "kioskium:test" in capsys.readouterr().err


def test_cache_size_defaults_per_cache_type():
    assert cache_size_mb(parse_args([])) == 32  # in-memory cache (no --profile-dir)
    assert cache_size_mb(parse_args(["--profile-dir", "/tmp/p"])) == 100
    assert cache_size_mb(parse_args(["--cache-size", "8"])) == 8
    assert cache_size_mb(parse_args(["--profile-dir", "/tmp/p", "--cache-size", "500"])) == 500
    assert cache_size_mb(parse_args(["--cache-size", "0"])) == 0  # Chromium decides

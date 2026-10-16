import json

import pytest

from kioskium.cli import ConfigError, effective_settings, load_config, parse_args


def _write(tmp_path, data, name="kioskium.json"):
    p = tmp_path / name
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def test_config_sets_defaults_and_cli_overrides(tmp_path):
    cfg = _write(tmp_path, {
        "no-touch": True, "show_cursor_timeout": 3, "control-port": 7001, "log-level": "debug",
        "chromium-flag": ["--a", "--b"], "allow-host": "x.com", "url": "http://cfg/",
        "// comment": "ignored", "_note": "ignored",
    })
    a = parse_args(["--config", str(cfg)])
    assert a.no_touch is True and a.touch is False and a.show_cursor_timeout == 3.0 and a.control_port == 7001
    assert a.log_level == "debug" and a.chromium_flag == ["--a", "--b"] and a.allow_host == ["x.com"]
    assert a.url == "http://cfg/" and a.config_file == str(cfg)
    b = parse_args(["--config", str(cfg), "--show-cursor-timeout", "5", "--log-level", "info", "http://cli/"])
    assert b.show_cursor_timeout == 5.0 and b.log_level == "info" and b.url == "http://cli/"
    assert b.no_touch is True  # still from the file


def test_config_validation(tmp_path):
    for bad, msg in [
        ({"nope": 1}, "unknown setting"),
        ({"show-cursor": "yes"}, "true or false"),
        ({"show-cursor-timeout": "abc"}, "show-cursor-timeout"),
        ({"log-level": "loud"}, "one of"),
        ({"chromium-flag": [1]}, "list of strings"),
        ({"config": "x"}, "unknown setting"),
    ]:
        with pytest.raises(ConfigError, match=msg):
            load_config(_write(tmp_path, bad))
    (tmp_path / "list.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ConfigError, match="JSON object"):
        load_config(tmp_path / "list.json")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.json")


def test_default_config_discovery(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KIOSKIUM_CONFIG", raising=False)
    assert parse_args([]).config_file is None
    _write(tmp_path, {"no-touch": True})
    a = parse_args([])
    assert a.no_touch is True and a.config_file.endswith("kioskium.json")
    assert parse_args(["--no-config"]).no_touch is False
    other = _write(tmp_path, {"no-touch": False, "fps": True}, name="other.json")
    monkeypatch.setenv("KIOSKIUM_CONFIG", str(other))
    b = parse_args([])
    assert b.no_touch is False and b.touch is True and b.fps is True


def test_bad_config_is_a_usage_error(tmp_path, capsys):
    cfg = _write(tmp_path, {"bogus": True})
    with pytest.raises(SystemExit):
        parse_args(["--config", str(cfg)])
    assert "unknown setting 'bogus'" in capsys.readouterr().err


def test_effective_settings_roundtrip(tmp_path):
    a = parse_args(["--show-cursor-timeout", "2", "--chromium-flag=--x", "http://u/"])
    settings = effective_settings(a)
    assert settings["show-cursor-timeout"] == 2.0 and "hide-cursor" not in settings and settings["url"] == "http://u/"
    assert "config" not in settings and "kiosk" not in settings and "print-config" not in settings
    cfg = _write(tmp_path, settings)
    b = parse_args(["--config", str(cfg)])
    assert vars(b) == {**vars(a), "config": str(cfg), "config_file": str(cfg)}

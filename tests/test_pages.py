import pytest

from kioskium.pages import FILES, IDLE, builtin_name, builtin_path, is_idle, resolve_url


def test_builtin_name():
    assert builtin_name("kioskium:test") == "test"
    assert builtin_name(" KIOSKIUM://Input/ ") == "input"
    assert builtin_name("kioskium:idle") == "idle"
    assert builtin_name("https://example.com") is None
    assert builtin_name("examples/page.html") is None
    with pytest.raises(ValueError, match="kioskium:idle"):
        builtin_name("kioskium:nope")


def test_pages_ship_with_the_package():
    for name in FILES:
        path = builtin_path(name)
        assert path.is_file() and "<html" in path.read_text(encoding="utf-8")


def test_resolve_url(tmp_path):
    assert is_idle(resolve_url("", str(tmp_path)))
    assert is_idle(resolve_url(IDLE, str(tmp_path)))
    test = resolve_url("kioskium:test", str(tmp_path))
    assert test.isLocalFile() and test.toLocalFile().endswith("test-page.html")
    assert resolve_url("example.com", str(tmp_path)).toString() == "http://example.com"
    (tmp_path / "page.html").write_text("<p>x</p>", encoding="utf-8")
    assert resolve_url("page.html", str(tmp_path)).toLocalFile().endswith("page.html")

import pytest

from kioskium.policy import host_allowed, normalize_host_pattern, parse_geometry


def test_empty_patterns_allow_everything():
    assert host_allowed("https://anything.example/", [])


def test_exact_and_glob_patterns():
    pats = ["example.com", "*.internal.local", "10.0.*"]
    assert host_allowed("https://example.com/app", pats)
    assert not host_allowed("https://sub.example.com/", pats)
    assert host_allowed("http://api.internal.local:8080/x", pats)
    assert host_allowed("http://10.0.0.5/", pats)
    assert not host_allowed("http://10.1.0.5/", pats)
    assert not host_allowed("https://evil.com/", pats)


def test_patterns_are_case_insensitive_and_accept_urls():
    assert host_allowed("https://EXAMPLE.com/", ["Example.COM"])
    assert normalize_host_pattern("https://Example.com:8443/path") == "example.com"
    assert normalize_host_pattern("host.local:80/x") == "host.local"


def test_non_network_schemes_always_allowed():
    pats = ["example.com"]
    for url in ("about:blank", "data:text/html,hi", "file:///tmp/x.html", "chrome://gpu", "blob:https://x/1"):
        assert host_allowed(url, pats), url


def test_network_url_without_host_blocked():
    assert not host_allowed("http:///nohost", ["example.com"])


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("1024x600", (1024, 600, None, None)),
        ("1280x800+0+0", (1280, 800, 0, 0)),
        ("1920x1080+1920+0", (1920, 1080, 1920, 0)),
        ("1920x1080-1920+0", (1920, 1080, -1920, 0)),
        ("800x600+10-20", (800, 600, 10, -20)),
    ],
)
def test_parse_geometry(spec, expected):
    assert parse_geometry(spec) == expected


@pytest.mark.parametrize("spec", ["", "abc", "100", "0x100", "100x100+1", "100x100+a+b"])
def test_parse_geometry_rejects_bad_input(spec):
    with pytest.raises(ValueError):
        parse_geometry(spec)

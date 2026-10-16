import json
import types

from kioskium.loading import DEFAULT_LOADING_HTML, build_loading_page, status_script


def test_status_script_is_valid_json_call():
    js = status_script("retrying", "http://x/", 3, 7.26, 'net::ERR_X "quoted"')
    assert js.startswith("window.kioskiumStatus && window.kioskiumStatus(")
    payload = json.loads(js[js.index("(") + 1 : js.rindex(")")])
    assert payload == {"state": "retrying", "url": "http://x/", "attempt": 3, "retryIn": 7.3, "error": 'net::ERR_X "quoted"'}


def test_default_page_substitutes_text_and_escapes():
    cfg = types.SimpleNamespace(loading_page=None, background="#123456", loading_title="Hi <b>", loading_detail="Conn & wait")
    html, base = build_loading_page(cfg)
    assert "#123456" in html and "Hi &lt;b&gt;" in html and "Conn &amp; wait" in html
    assert "__TITLE__" not in html and "kioskiumStatus" in html
    assert base.toString() == "about:blank"


def test_custom_page_uses_file_base_url(tmp_path):
    f = tmp_path / "loading.html"
    f.write_text("<h1>custom</h1>", encoding="utf-8")
    cfg = types.SimpleNamespace(loading_page=str(f), background="#000", loading_title="", loading_detail="")
    html, base = build_loading_page(cfg)
    assert html == "<h1>custom</h1>" and base.isLocalFile() and base.toLocalFile().endswith("loading.html")


def test_default_html_is_self_contained():
    assert "http://" not in DEFAULT_LOADING_HTML and "https://" not in DEFAULT_LOADING_HTML


def test_status_details_only_with_loading_status():
    base = dict(loading_page=None, background="#000", loading_title="Loading", loading_detail="")
    html, _ = build_loading_page(types.SimpleNamespace(**base))
    assert "var showStatus = false;" in html and "__STATUS__" not in html
    html, _ = build_loading_page(types.SimpleNamespace(**base, loading_status=True))
    assert "var showStatus = true;" in html

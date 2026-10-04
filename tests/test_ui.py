"""화면 공통 기반: 직접 제공 자산, 색 토큰 대비, 매크로."""
import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent / "static" / "css" / "app.css"


def _tokens(block):
    return dict(re.findall(r"--([a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})", block))


def _themes():
    text = CSS.read_text(encoding="utf-8")
    light, _, dark = text.partition("@media (prefers-color-scheme: dark)")
    return _tokens(light), {**_tokens(light), **_tokens(dark)}


def _lum(h):
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = ch(h[1:3]), ch(h[3:5]), ch(h[5:7])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(a, b):
    hi, lo = sorted([_lum(a), _lum(b)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
def test_token_contrast(theme):
    t = _themes()[theme]
    for fg, bg in [("text", "bg"), ("text", "card"), ("muted", "bg"), ("muted", "card"),
                   ("ink", "card"), ("ink", "bg"), ("on-ink", "ink"),
                   ("up", "soft"), ("down", "soft"), ("up", "card"), ("down", "card"),
                   ("amber", "soft"), ("muted", "soft")]:
        assert _ratio(t[fg], t[bg]) >= 4.5, f"{fg} on {bg}: {_ratio(t[fg], t[bg]):.2f}"


@pytest.mark.parametrize("theme", [0, 1], ids=["light", "dark"])
def test_nodata_distinct_from_lowest_band(theme):
    t = _themes()[theme]
    assert _ratio(t["nodata"], t["seq-100"]) >= 1.3


def test_base_uses_self_hosted_assets(client):
    html = client.get("/status").get_data(as_text=True)
    assert "/static/fonts/pretendard/pretendard.css" in html and "/static/css/app.css" in html
    assert "<style>" not in html
    assert all(h.startswith("/static/") for h in re.findall(r'<link[^>]+href="([^"]+)"', html))
    assert all(s.startswith("/static/") or s.startswith("https://cdnjs.cloudflare.com/ajax/libs/echarts/")
               for s in re.findall(r'<script[^>]+src="([^"]+)"', html))
    assert 'href="/status" class="on" aria-current="page"' in html
    assert "국토지리정보원" in html and "CC BY" in html


def test_font_files_served(client):
    css = client.get("/static/fonts/pretendard/pretendard.css").get_data(as_text=True)
    assert "Pretendard Variable" in css
    first = re.search(r"url\(['\"]?\.?/?([^'\")]+\.woff2)", css).group(1)
    assert client.get(f"/static/fonts/pretendard/{first}").status_code == 200
    assert client.get("/static/fonts/pretendard/OFL.txt").status_code == 200


def test_pills_macro(app):
    with app.test_request_context():
        ui = app.jinja_env.get_template("_ui.html").module
        html = str(ui.pills("band", [("all", "전체"), ("le60", "60㎡ 이하")], "le60", "면적 구간",
                            {"le60": "--m-price"}))
    assert 'data-value="le60" aria-pressed="true"' in html
    assert 'data-value="all" aria-pressed="false"' in html
    assert 'role="group" aria-label="면적 구간"' in html and "--dot:var(--m-price)" in html
    assert html.count('type="button"') == 2


def test_page_head_macro(app):
    with app.test_request_context():
        ui = app.jinja_env.get_template("_ui.html").module
        html = str(ui.page_head("지도", "설명 <b>"))
    assert '<header class="page-head">' in html and "<h1>지도</h1>" in html and "설명 &lt;b&gt;" in html

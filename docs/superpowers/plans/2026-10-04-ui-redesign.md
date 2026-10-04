# 분석 화면 디자인 개편 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 분석 화면 전체를 참고 사이트(Oxford Economics Global Cities Index) 톤으로 바꾸고, 지도 화면에 순위 목록 카드와 다지표 비교표를 들여온다.

**Architecture:** `base.html`의 인라인 스타일을 `static/css/app.css`(토큰 + 부품)로 옮기고, Pretendard 글꼴을 `static/fonts/pretendard/`에서 직접 제공한다. 화면마다 반복되는 조각(머리 영역, 알약 묶음)은 Jinja 매크로(`templates/_ui.html`)로, 알약·막대 셀 동작은 `static/js/common.js`의 `App.pills`·`App.barCell`·`App.divBarCell`로 둔다. 각 화면 템플릿·스크립트는 이 부품으로 다시 구성한다. API·URL 상태 키는 바꾸지 않는다.

**Tech Stack:** Flask/Jinja2, 바닐라 JS, ECharts 5.6(cdnjs), Pretendard v1.3.9 가변 글꼴(OFL, 분할 woff2), pytest

**Spec:** `docs/superpowers/specs/2026-10-04-ui-redesign-design.md`

## Global Constraints

- 범위: 톤 + 대표 구성 차용. 정보 구조(내비게이션 형태, 대시보드 항목)는 유지. 지구본, 비교 체크박스, 새 데이터·새 API, URL 상태 키 변경은 범위 밖.
- 색의 의미: 크기 = 남색 한 가지 색의 진하기, 증감 = 파랑↔회색↔빨강(상승 = 빨강). 초록→빨강 신호등은 쓰지 않는다.
- 어두운 테마 유지(`@media (prefers-color-scheme: dark)`에서 토큰만 다시 정의).
- 글꼴: Pretendard 가변 글꼴 분할 woff2를 저장소에 넣어 직접 제공. `font-family: "Pretendard Variable", Pretendard, -apple-system, "Malgun Gothic", sans-serif`. 표·숫자는 `font-variant-numeric: tabular-nums`.
- 밝은·어두운 테마 모두 본문 대비 WCAG AA 이상(4.5:1).
- 기존 계획 3의 차트 규칙: 이중 축 금지, 지역 비교 8색(`--series-1`~`--series-8`)은 지역에 붙음, 계열 2개 이상이면 범례, 모든 차트에 툴팁, 글자는 글자 색 토큰.
- 색만으로 정보를 전하지 않는다: 색 점·막대·배지에는 숫자나 부호·문구를 함께 표시.
- 알약은 `button`+`aria-pressed`, 정렬 머리는 `aria-sort`, 키보드로 조작 가능.
- API, URL 상태 키(`regions`, `metric`, `band`, `from`, `to`, `ma`, `idx`, `level`, `parent`, `q`, `region`)를 바꾸지 않는다.
- 외부 스크립트는 cdnjs ECharts 하나(`https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js`), 외부 스타일시트 없음.
- 경계 출처 표시(CC BY)를 모든 화면에 유지.
- 폭 375px에서 가로 스크롤 없이 1열(비교표는 표 안에서만 가로 스크롤).
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`
- 화면 확인(각 Task의 브라우저 확인 단계)은 로컬 시드 DB `molit_seed`(경계 `2026-10`)로 띄운 서버(launch 설정 `molit-seed`, 포트 8001)에서 한다. 실제 개발 DB(`molit_dev`)에는 `scripts/seed_dev.py`를 실행하지 않는다.

## Review Focus

1. **지도의 거래량 지표**: `/api/map` 값은 거래량을 `n`으로 주는데 지금 `map.js`는 `v['n_trades']`를 읽어 값·색이 모두 빈다. 거래량을 고르면 지도 색·순위·범례가 채워져야 한다 → Task 2의 `METRICS` 값 필드 매핑과 브라우저 확인 항목.
2. **옛 URL**: `/map?metric=yoy_n&band=gt85&level=sgg&parent=11`, `/trends?metric=range&band=le60`처럼 이전에 만든 링크는 해당 알약이 선택된 상태로 같은 화면을 보여야 하고, 없는 값(`metric=bogus`)은 기본값으로 돌아가야 한다 → Task 2·4 브라우저 확인.
3. **빈 값**: 거래가 없어 값이 `null`인 지역은 순위 목록·비교표에서 항상 맨 아래, 막대 없이 `-`로 보여야 한다(0이나 맨 위가 아님) → Task 2 `sortRows`, Task 1 `barCell` 빈 값 처리.
4. **좁은 화면**: 폭 375px에서 페이지 가로 스크롤이 없어야 하고 지도·순위 카드가 1열로 쌓여야 한다 → 각 Task 브라우저 확인(`document.documentElement.scrollWidth <= innerWidth`).
5. **어두운 테마 대비**: 어두운 테마에서 본문·보조 문구·선택된 알약 글자가 읽혀야 한다 → Task 1 `test_token_contrast`(토큰 대비 자동 검사).

---

## File Structure

| 파일 | 책임 |
|---|---|
| `static/fonts/pretendard/pretendard.css`, `woff2-dynamic-subset/*.woff2`, `OFL.txt` (새로) | 직접 제공하는 글꼴 |
| `static/css/app.css` (새로) | 색 토큰(밝은·어두운), 기본 요소, 부품(카드·알약·순위 목록·비교표·막대 셀·배지·타일·범례), 옛 클래스 호환 |
| `templates/base.html` (수정) | 인라인 `<style>` 제거, CSS 연결, 상단 막대·`page_head` 블록·상태 줄 |
| `templates/_ui.html` (새로) | 매크로 `page_head(title, desc)`, `pills(id, options, selected, label, dots)` |
| `static/js/common.js` (수정) | `App.pills`, `App.barCell`, `App.divBarCell`, 차트 글꼴 |
| `templates/map.html`, `static/js/map.js` (수정) | 지도 + 순위 목록 카드 + 비교표 |
| `templates/dashboard.html`, `static/js/dashboard.js` (수정) | 타일·2열 차트 카드·상승/하락 순위 목록 |
| `templates/trends.html`, `static/js/trends.js` (수정) | 알약·차트 카드 도구 막대 |
| `templates/complexes.html`, `templates/complex.html` (수정) | 머리 영역·카드·`data-table` |
| `templates/export.html`, `static/js/export.js`, `templates/trades.html`, `templates/status.html`, `templates/login.html` (수정) | 알약·카드·머리 영역 |
| `tests/test_ui.py` (새로), `tests/test_pages.py` (수정) | 자산·토큰 대비·매크로·화면 구조 |
| `README.md` (수정) | 화면 디자인·글꼴 라이선스 |

---

### Task 1: 공통 기반 — 글꼴, 스타일, 상단 막대, 매크로, 공통 JS

**Files:**
- Create: `static/fonts/pretendard/` (글꼴), `static/css/app.css`, `templates/_ui.html`, `tests/test_ui.py`
- Modify: `templates/base.html`, `static/js/common.js`

**Interfaces:**
- Produces:
  - CSS 토큰: `--bg --card --ink --on-ink --text --muted --line --accent --soft --shadow --red --amber --green --track --m-price --m-ppm2 --m-count --m-yoy --series-1..8 --seq-100..700 --div-neg-2 --div-neg-1 --div-mid --div-pos-1 --div-pos-2 --grid --axis --provisional`
  - CSS 클래스: `.topbar .brand .page-head .status-line .card .card-head .card-title .card-sub .filters .filter-row .filter-label .pills .pill .dot .ghost .small .check .grid-2 .map-layout .table-wrap .data-table .sort .sort-ind .table-foot .rank-list .rank-item .rank-no .rank-name .rank-val .rank-dot .rank-empty .list-title .bar-cell .bar-track .bar-num .badge .badge.up .badge.down .legend-bar .ramp .legend-ends .crumbs .side .side-head .chart-map .toolbar` + 옛 클래스(`.bar .wrap .tile .tiles .cols .chips .chip .chart .meter .pager .notes .meta .err .warn .ok .muted .primary .btn`)
  - Jinja: `base.html` 블록 `page_head`(머리 영역 자리), `body`, `scripts`, `title`. `_ui.html` 매크로 `page_head(title, desc='')`, `pills(id, options, selected, label, dots=none)` — `options`는 `[(value, label)]`, `dots`는 `{value: '--토큰'}`.
  - JS: `App.pills(containerEl, onChange) -> { value (get/set), has(v) }`, `App.barCell(v, max, colorVar, text) -> html`, `App.divBarCell(v, maxAbs, text) -> html`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_ui.py`:
```python
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
                   ("ink", "card"), ("ink", "bg"), ("on-ink", "ink")]:
        assert _ratio(t[fg], t[bg]) >= 4.5, f"{fg} on {bg}: {_ratio(t[fg], t[bg]):.2f}"


def test_base_uses_self_hosted_assets(client):
    html = client.get("/status").get_data(as_text=True)
    assert "/static/fonts/pretendard/pretendard.css" in html and "/static/css/app.css" in html
    assert "<style>" not in html
    assert all(h.startswith("/static/") for h in re.findall(r'<link[^>]+href="([^"]+)"', html))
    assert all(s.startswith("/static/") or s.startswith("https://cdnjs.cloudflare.com/ajax/libs/echarts/")
               for s in re.findall(r'<script[^>]+src="([^"]+)"', html))
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
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_ui.py -v`
Expected: FAIL (app.css 없음 → FileNotFoundError, `_ui.html` 없음 → TemplateNotFound)

- [ ] **Step 3: 글꼴 내려받기**

Pretendard v1.3.9 가변 글꼴 분할 판(OFL)을 공식 저장소의 jsDelivr 사본에서 받는다. 사용자가 이 글꼴을 저장소에 넣기로 승인했다.
```bash
BASE=https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/variable
D=static/fonts/pretendard
mkdir -p $D/woff2-dynamic-subset
curl -fsSL "$BASE/pretendardvariable-dynamic-subset.css" -o $D/pretendard.css
grep -o "woff2-dynamic-subset/[^)'\"]*\.woff2" $D/pretendard.css | sort -u | while read f; do curl -fsSL "$BASE/$f" -o "$D/$f"; done
curl -fsSL https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/LICENSE -o $D/OFL.txt
ls $D/woff2-dynamic-subset | wc -l; du -sh $D
```
Expected: woff2 파일 수십 개(약 90), 전체 수 MB. `pretendard.css`의 `url(./woff2-dynamic-subset/…)` 경로가 실제 파일과 맞아야 한다. 경로가 다르면(파일 이름 규칙이 다르면) `curl -fsSL $BASE/` 목록에서 실제 경로를 확인해 맞춘다. 글꼴 이름이 `'Pretendard Variable'`인지 확인한다. `head -c3 $D/pretendard.css | xxd`로 BOM이 없는지 본다.

- [ ] **Step 4: `static/css/app.css` 작성**

```css
/* 분석 화면 공통 스타일. 색은 토큰으로만 쓰고, 어두운 테마는 토큰만 다시 정의한다.
   토큰 이름은 static/js의 App.css('--…')와 tests/test_ui.py(대비 검사)가 읽는다. */
:root {
  --bg:#f3f6fa; --card:#ffffff; --ink:#0b2a55; --on-ink:#ffffff; --text:#1c2a3d; --muted:#5b6b80;
  --line:#e3e8ef; --accent:#0b2a55; --soft:#eef3fa; --track:#e3e8ef;
  --shadow:0 1px 2px rgba(11,42,85,.06), 0 4px 16px rgba(11,42,85,.06);
  --red:#c23a39; --amber:#9a5b00; --green:#1d7a4a;
  /* 지표 고유색: 알약의 점, 비교표 막대 */
  --m-price:#1f6fd1; --m-ppm2:#b4237a; --m-count:#b07d1a; --m-yoy:#1b8f8a;
  /* 지역 비교 8색(지역에 붙음) */
  --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a; --series-4:#eda100;
  --series-5:#e87ba4; --series-6:#008300; --series-7:#4a3aa7; --series-8:#e34948;
  /* 크기: 남색 한 가지 색의 진하기 7단계 */
  --seq-100:#dbe7f6; --seq-200:#b5cdeb; --seq-300:#86abd9; --seq-400:#5687c4; --seq-500:#2f64a8; --seq-600:#1a4683; --seq-700:#0b2a55;
  /* 증감: 파랑 ↔ 회색 ↔ 빨강(상승 = 빨강) */
  --div-neg-2:#1c5cab; --div-neg-1:#86b6ef; --div-mid:#eceff3; --div-pos-1:#f2a3a2; --div-pos-2:#c23a39;
  --grid:#e6ebf1; --axis:#c5cfdb; --provisional:rgba(91,107,128,0.12);
  --radius:16px; --radius-sm:10px;
  --font:"Pretendard Variable", Pretendard, -apple-system, "Malgun Gothic", sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg:#0d1726; --card:#16233a; --ink:#7fb0ff; --on-ink:#0d1726; --text:#e6ecf5; --muted:#9aabc2;
    --line:#26364f; --accent:#7fb0ff; --soft:#1c2c47; --track:#26364f;
    --shadow:0 1px 2px rgba(0,0,0,.35);
    --red:#f08a89; --amber:#f0b54d; --green:#5cc98f;
    --m-price:#5ea2f0; --m-ppm2:#e06aa9; --m-count:#d9a845; --m-yoy:#4cc3bd;
    --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#c98500;
    --series-5:#d55181; --series-6:#008300; --series-7:#9085e9; --series-8:#e66767;
    --div-mid:#33415a; --grid:#22324a; --axis:#33435c; --provisional:rgba(154,171,194,0.16);
  }
}

/* 기본 요소 */
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { margin:0; font-family:var(--font); font-size:15px; line-height:1.55; background:var(--bg); color:var(--text); }
a { color:var(--accent); text-decoration:none; font-weight:600; }
a:hover { text-decoration:underline; }
h1, h2, h3 { color:var(--ink); margin:0; }
h2 { font-size:18px; font-weight:700; margin:28px 0 12px; }
input, select, button, .btn { font:inherit; height:44px; padding:0 14px; border:1px solid var(--line); border-radius:var(--radius-sm);
  background:var(--card); color:var(--text); }
input[type=checkbox] { height:auto; width:16px; height:16px; accent-color:var(--ink); }
input[type=text], input[type=search], input[type=password] { flex:1 1 200px; min-width:0; }
button, .btn { cursor:pointer; display:inline-flex; align-items:center; gap:6px; font-weight:600; text-decoration:none; }
button:focus-visible, a:focus-visible, input:focus-visible, select:focus-visible { outline:2px solid var(--ink); outline-offset:2px; }
.primary { background:var(--ink); color:var(--on-ink); border-color:var(--ink); }
.ghost { background:transparent; }
.small { height:34px; padding:0 10px; font-size:13px; }
.check { display:inline-flex; align-items:center; gap:6px; color:var(--muted); font-size:14px; }
.err { color:var(--red); } .warn { color:var(--amber); } .ok { color:var(--green); } .muted { color:var(--muted); }
.meta { color:var(--muted); font-size:14px; margin:6px 0 14px; }
td.num, th.num { text-align:right; font-variant-numeric:tabular-nums; }

/* 상단 막대 */
.topbar { background:var(--card); box-shadow:var(--shadow); position:sticky; top:0; z-index:10; }
.topbar-in { max-width:1280px; margin:0 auto; padding:0 16px; display:flex; align-items:center; gap:24px; min-height:60px; flex-wrap:wrap; }
.brand { color:var(--ink); font-weight:800; font-size:17px; }
.topbar nav { display:flex; gap:4px; flex-wrap:wrap; }
.topbar nav a { color:var(--muted); font-weight:600; padding:18px 10px 16px; border-bottom:3px solid transparent; }
.topbar nav a.on { color:var(--ink); border-bottom-color:var(--ink); }
.topbar nav a:hover { color:var(--ink); text-decoration:none; }
.topbar .logout { margin-left:auto; }

/* 본문 틀 */
main { max-width:1280px; margin:0 auto; padding:28px 16px 40px; }
.page-head { margin:4px 0 6px; }
.page-head h1 { font-size:30px; font-weight:800; letter-spacing:-0.01em; line-height:1.25; }
.page-head p { color:var(--muted); font-size:15px; margin:8px 0 0; }
.status-line { color:var(--muted); font-size:13px; margin:6px 0 20px; }
.site-foot { color:var(--muted); font-size:13px; margin-top:32px; }

/* 카드 */
.card { background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); padding:22px 24px; margin-bottom:16px; min-width:0; }
.card-head { display:flex; align-items:center; justify-content:space-between; gap:12px; flex-wrap:wrap; margin-bottom:8px; }
.card-title { font-size:18px; font-weight:700; color:var(--ink); margin:0 0 8px; }
.card-head .card-title { margin:0; }
.card-sub { color:var(--muted); font-size:13px; margin:-4px 0 12px; }
.grid-2 { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:16px; }
.toolbar { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }

/* 조건 막대 */
.filters { display:flex; flex-direction:column; gap:12px; }
.filter-row { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
.filter-label { color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.08em; min-width:44px; }

/* 알약 */
.pills { display:flex; gap:8px; flex-wrap:wrap; }
.pill { height:38px; padding:0 14px; border-radius:999px; font-weight:600; color:var(--text); background:var(--card); border:1px solid var(--line); }
.pill .dot { width:8px; height:8px; border-radius:50%; background:var(--dot, var(--muted)); }
.pill[aria-pressed="true"] { background:var(--ink); color:var(--on-ink); border-color:var(--ink); }
.pill[aria-pressed="true"] .dot { box-shadow:0 0 0 2px var(--on-ink); }

/* 표 */
.table-wrap, .wrap { overflow-x:auto; background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); }
.card .table-wrap { box-shadow:none; border-radius:var(--radius-sm); }
table { width:100%; border-collapse:collapse; font-size:14px; }
th, td { padding:11px 14px; border-bottom:1px solid var(--line); text-align:left; white-space:nowrap; }
th { color:var(--muted); font-size:12px; font-weight:700; letter-spacing:.04em; }
.data-table thead th { background:var(--ink); color:var(--on-ink); position:sticky; top:0; }
.data-table tbody tr:hover { background:var(--soft); }
.data-table td a b, .data-table td a { color:var(--ink); }
.data-table td small { display:block; color:var(--muted); font-weight:400; }
.sort { all:unset; cursor:pointer; display:inline-flex; gap:4px; align-items:center; font:inherit; color:inherit; }
.sort:focus-visible { outline:2px solid var(--on-ink); outline-offset:2px; }
.sort-ind { opacity:.75; font-size:10px; }
.table-foot { color:var(--muted); font-size:13px; margin:12px 2px 0; }
tr.cancel td { color:var(--muted); text-decoration:line-through; }

/* 막대 셀 */
.bar-cell { display:inline-flex; align-items:center; gap:8px; justify-content:flex-end; }
.bar-track { position:relative; width:56px; height:6px; border-radius:3px; background:var(--track); overflow:hidden; }
.bar-track i { position:absolute; top:0; bottom:0; left:0; border-radius:3px; }
.bar-cell.div .bar-track::after { content:""; position:absolute; left:50%; top:-2px; bottom:-2px; width:1px; background:var(--axis); }
.bar-num { min-width:64px; text-align:right; font-weight:600; font-variant-numeric:tabular-nums; }

/* 순위 목록 */
.rank-list { list-style:none; margin:0; padding:0; }
.rank-list li + li { border-top:1px solid var(--line); }
.rank-item { all:unset; box-sizing:border-box; cursor:pointer; display:grid; grid-template-columns:48px minmax(0,1fr) auto 14px; gap:10px;
  align-items:center; width:100%; padding:12px 8px; border-radius:var(--radius-sm); }
a.rank-item { grid-template-columns:48px minmax(0,1fr) auto; }
.rank-item:hover, .rank-item.on { background:var(--soft); text-decoration:none; }
.rank-item:focus-visible { outline:2px solid var(--ink); }
.rank-no { color:var(--ink); font-weight:800; font-variant-numeric:tabular-nums; }
.rank-name { min-width:0; }
.rank-name b { display:block; color:var(--ink); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rank-name small { display:block; color:var(--muted); font-size:13px; }
.rank-val { font-weight:700; font-variant-numeric:tabular-nums; color:var(--text); }
.rank-dot { width:12px; height:12px; border-radius:50%; border:1px solid var(--line); }
.rank-empty { color:var(--muted); padding:16px 8px; }
.list-title { font-size:14px; font-weight:700; color:var(--muted); margin:0 0 4px; }

/* 배지 */
.badge { display:inline-flex; align-items:center; gap:4px; padding:3px 10px; border-radius:999px; font-size:13px; font-weight:700;
  background:var(--soft); color:var(--muted); font-variant-numeric:tabular-nums; white-space:nowrap; }
.badge.up { color:var(--div-pos-2); }
.badge.down { color:var(--div-neg-2); }

/* 타일 */
.tiles { display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:16px; margin-bottom:16px; }
.tile { background:var(--card); border-radius:var(--radius); box-shadow:var(--shadow); padding:18px 20px; }
.tile .k { color:var(--muted); font-size:13px; font-weight:600; }
.tile .v { font-size:28px; font-weight:800; color:var(--ink); margin-top:6px; font-variant-numeric:tabular-nums; }
.tile .d { margin-top:8px; }
.meter { height:6px; background:var(--track); border-radius:3px; margin-top:10px; overflow:hidden; }
.meter > i { display:block; height:100%; background:var(--ink); }

/* 차트 */
.chart { height:340px; min-width:0; }
.chart.small { height:200px; }
.chart-map { height:560px; }

/* 지도 화면 */
.map-layout { display:grid; grid-template-columns:minmax(0, 1.35fr) minmax(320px, 1fr); gap:16px; align-items:start; }
.map-card { padding:12px; }
.rank-card { display:flex; flex-direction:column; gap:12px; max-height:584px; }
.rank-card .rank-list { overflow-y:auto; min-height:0; flex:1 1 auto; }
.crumbs { margin-left:auto; color:var(--muted); font-size:14px; }
.crumbs b { color:var(--ink); }
.legend-bar { display:flex; flex-direction:column; gap:4px; }
.ramp { display:flex; height:8px; border-radius:4px; overflow:hidden; }
.ramp i { flex:1; }
.legend-ends { display:flex; justify-content:space-between; color:var(--muted); font-size:12px; }
.side { border:1px solid var(--line); border-radius:var(--radius-sm); padding:10px 12px; }
.side-head { display:flex; justify-content:space-between; gap:8px; flex-wrap:wrap; align-items:center; color:var(--ink); font-size:14px; }

/* 칩(추이 지역) */
.chips { display:flex; flex-wrap:wrap; gap:8px; }
.chip { display:inline-flex; gap:6px; align-items:center; height:34px; padding:0 4px 0 10px; border:1px solid var(--line);
  border-left:4px solid var(--c); border-radius:999px; background:var(--card); font-size:14px; font-weight:600; color:var(--ink); }
.chip button { height:28px; padding:0 8px; border:none; background:none; color:var(--muted); }

/* 옛 클래스 호환(점진 전환용) */
.bar { display:flex; flex-wrap:wrap; gap:10px; margin-bottom:16px; align-items:center; }
.cols { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:16px; }
.pager { display:flex; gap:8px; align-items:center; margin-top:12px; }
ul.notes { color:var(--muted); font-size:14px; padding-left:18px; line-height:1.8; }

/* 좁은 화면 */
@media (max-width: 900px) {
  .map-layout, .grid-2 { grid-template-columns:minmax(0, 1fr); }
  .rank-card { max-height:none; }
  .rank-card .rank-list { max-height:480px; }
  .chart-map { height:420px; }
}
@media (max-width: 560px) {
  .page-head h1 { font-size:24px; }
  .card { padding:16px; }
  .topbar-in { gap:8px; }
  .topbar nav a { padding:10px 8px; }
  .crumbs { margin-left:0; }
}
```
값은 Step 6의 대비 테스트로 확인한다. 어느 쌍이 4.5 미만이면 그 토큰만 조정한다(밝은 테마는 더 어둡게, 어두운 테마는 더 밝게).

- [ ] **Step 5: `templates/_ui.html`과 `templates/base.html` 작성**

`templates/_ui.html`:
```jinja
{# 화면 공통 조각. 화면 템플릿에서 {% import "_ui.html" as ui %} 로 쓴다. #}
{% macro page_head(title, desc='') -%}
<header class="page-head"><h1>{{ title }}</h1>{% if desc %}<p>{{ desc }}</p>{% endif %}</header>
{%- endmacro %}

{# options: [(value, label)], dots: {value: '--토큰'} (범주색 점) #}
{% macro pills(id, options, selected, label, dots=none) -%}
<div class="pills" id="{{ id }}" role="group" aria-label="{{ label }}">
{%- for v, l in options %}<button type="button" class="pill" data-value="{{ v }}" aria-pressed="{{ 'true' if v == selected else 'false' }}">{% if dots and dots.get(v) %}<span class="dot" style="--dot:var({{ dots[v] }})"></span>{% endif %}{{ l }}</button>{% endfor -%}
</div>
{%- endmacro %}
```

`templates/base.html` 전체를 다음으로 바꾼다(기존 `<style>` 블록 삭제):
```html
<!doctype html>
<html lang="ko">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}아파트 실거래가{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='fonts/pretendard/pretendard.css') }}">
  <link rel="stylesheet" href="{{ url_for('static', filename='css/app.css') }}">
</head>
<body>
<header class="topbar"><div class="topbar-in">
  <a class="brand" href="/">아파트 매매 실거래가</a>
  <nav aria-label="주 메뉴">
    <a href="/" class="{{ 'on' if request.path == '/' }}">대시보드</a>
    <a href="/trends" class="{{ 'on' if request.path == '/trends' }}">추이</a>
    <a href="/map" class="{{ 'on' if request.path == '/map' }}">지도</a>
    <a href="/complexes" class="{{ 'on' if request.path.startswith('/complexes') }}">단지</a>
    <a href="/export" class="{{ 'on' if request.path == '/export' }}">추출</a>
    <a href="/trades" class="{{ 'on' if request.path == '/trades' }}">거래 목록</a>
    <a href="/status" class="{{ 'on' if request.path == '/status' }}">수집 현황</a>
  </nav>
  {% if session.get('auth') %}<form method="post" action="/logout" class="logout"><button class="ghost small">로그아웃</button></form>{% endif %}
</div></header>
<main>
  {% block page_head %}{% endblock %}
  {% if p is defined %}
  <div class="status-line">
    출처: 국토교통부 실거래가 정보(공공데이터포털 Open API) ·
    누적 {{ p.trades|comma }}건 · 마지막 수집 {{ p.last_fetched or "없음" }} ·
    오늘 호출 {{ p.calls_today|comma }}/{{ p.daily_limit|comma }}
    {% if p.running %} · <b>수집 중</b> {{ p.current or "" }}{% endif %}
    {% if p.paused_until %} · <span class="warn">한도 도달, {{ p.paused_until }}부터 재개</span>{% endif %}
  </div>
  {% endif %}
  {% block body %}{% endblock %}
  <p class="site-foot">지역 경계: 국토지리정보원 연속수치지형도 행정경계(읍면동), CC BY · 단지 좌표: 주소정보누리집 위치정보요약DB · 글꼴: Pretendard(OFL)</p>
</main>
{% block scripts %}{% endblock %}
</body>
</html>
```
(지금 `base.html` 87줄 중 `</main>` 앞의 경계 출처 줄과 `{% block scripts %}`의 위치를 이 구조로 옮긴다. 상태 줄 내용은 기존 `.meta` 블록과 같다.)

- [ ] **Step 6: `static/js/common.js`에 알약·막대 셀 추가, 차트 글꼴**

`baseOption()`의 `textStyle` 줄을 다음으로 바꾼다(캔버스는 CSS 글꼴을 상속하지 않으므로 계산된 글꼴 이름을 넘긴다):
```javascript
      textStyle: { color: css('--muted'), fontFamily: getComputedStyle(document.body).fontFamily },
```
`escapeHtml` 정의 다음에 추가:
```javascript
  // 알약 묶음(button.pill[aria-pressed]): 하나만 선택. onChange(value)는 사용자가 바꿨을 때만 부른다.
  function pills(el, onChange) {
    const buttons = () => [...el.querySelectorAll('.pill')];
    const api = {
      get value() {
        const on = buttons().find((b) => b.getAttribute('aria-pressed') === 'true') || buttons()[0];
        return on ? on.dataset.value : '';
      },
      set value(v) { buttons().forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === v))); },
      has: (v) => buttons().some((b) => b.dataset.value === v),
    };
    el.addEventListener('click', (ev) => {
      const b = ev.target.closest('.pill');
      if (!b || !el.contains(b) || b.getAttribute('aria-pressed') === 'true') return;
      api.value = b.dataset.value;
      if (onChange) onChange(b.dataset.value);
    });
    return api;
  }

  // 막대 셀: 숫자(text)를 항상 함께 보여 색만으로 전하지 않는다. 빈 값은 막대 없이 text('-')만.
  function barCell(v, max, colorVar, text) {
    const w = v == null || !max ? 0 : Math.max(2, Math.round((100 * Math.abs(v)) / max));
    return `<span class="bar-cell"><span class="bar-track">${w ? `<i style="width:${w}%;background:var(${colorVar})"></i>` : ''}</span><span class="bar-num">${text}</span></span>`;
  }
  // 증감 막대: 가운데 기준, 상승 = 오른쪽 빨강, 하락 = 왼쪽 파랑
  function divBarCell(v, maxAbs, text) {
    let bar = '';
    if (v != null && maxAbs) {
      const w = Math.max(2, Math.round((50 * Math.min(Math.abs(v), maxAbs)) / maxAbs));
      bar = v >= 0 ? `<i style="left:50%;width:${w}%;background:var(--div-pos-2)"></i>`
        : `<i style="left:${50 - w}%;width:${w}%;background:var(--div-neg-2)"></i>`;
    }
    return `<span class="bar-cell div"><span class="bar-track">${bar}</span><span class="bar-num">${text}</span></span>`;
  }
```
반환 객체에 `pills, barCell, divBarCell`을 더한다:
```javascript
  return { css, api, fmt, shiftYm, monthRange, toMonthInput, fromMonthInput, readState, writeState, seriesColor, chart, baseOption, provisionalArea, escapeHtml, message, pills, barCell, divBarCell };
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_ui.py tests/test_pages.py tests/test_web.py tests/test_auth.py -v`
Expected: PASS. 대비 테스트가 실패하면 실패한 쌍의 토큰을 조정하고 다시 돌린다.

- [ ] **Step 8: 브라우저 확인(컨트롤러)**

`molit-seed` 서버를 다시 띄우고 `/status`, `/`, `/map`, `/trends`를 연다. 확인:
- 상단 막대(브랜드·메뉴·현재 화면 밑줄), 글꼴이 Pretendard로 그려짐(`document.fonts.check('16px "Pretendard Variable"')`가 true)
- 기존 화면이 옛 클래스 호환으로 깨지지 않음(카드·표·타일이 새 톤)
- 어두운 테마(`resize_window colorScheme: dark`)에서 글자·메뉴가 읽힘
- 네트워크: 한글 화면에서 받은 woff2 개수와 총 크기를 기록(`performance.getEntriesByType('resource')` 중 `.woff2`)
- 콘솔 오류 없음

- [ ] **Step 9: 커밋**

```bash
git add static/fonts/pretendard static/css/app.css templates/_ui.html templates/base.html static/js/common.js tests/test_ui.py
git commit -m "화면 공통 기반: Pretendard 직접 제공, 공통 스타일·토큰, 상단 막대, 알약·막대 셀 부품

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 지도 화면 — 순위 목록 카드와 비교표

**Files:**
- Modify: `templates/map.html`, `static/js/map.js`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 1 `ui.page_head`, `ui.pills`, `App.pills`, `App.barCell`, `App.divBarCell`, CSS `.map-layout .rank-card .rank-list .data-table .legend-bar .crumbs .side`; `/api/map` 응답 `{version, level, parent, from, to, prev_from, prev_to, coverage, parents[{region_cd, level, name}], values[{region_cd, name, full_name, n, median_price, median_ppm2, yoy_price, yoy_n}]}`; `/api/agg`
- Produces: 지도 화면 DOM id `map`, `metric`(알약), `band`(알약), `from`, `to`, `crumbs`, `side`, `side-name`, `side-link`, `side-drill`, `mini`, `rank-title`, `rank-q`, `legend-ramp`, `legend-lo`, `legend-hi`, `ranking`, `compare`, `note`, `msg`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pages.py`의 `test_map_page`를 다음으로 바꾼다:
```python
def test_map_page(client, seeded):
    html = client.get("/map").get_data(as_text=True)
    assert 'id="map"' in html and 'id="ranking"' in html and "js/map.js" in html
    assert '<header class="page-head">' in html
    assert 'id="compare"' in html and 'class="data-table"' in html and 'id="rank-q"' in html
    assert 'data-value="yoy_n" aria-pressed="false"' in html and 'data-value="median_price" aria-pressed="true"' in html
    assert 'data-value="le60"' in html and "<select" not in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py::test_map_page -v`
Expected: FAIL (`page-head` 없음)

- [ ] **Step 3: `templates/map.html` 작성**

```html
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}지도 · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head('지역 지도', '시도에서 읍면동까지 단계구분도로 보고, 지역 순위와 지표를 한 화면에서 비교합니다.') }}{% endblock %}
{% block body %}
{% set metrics = [('median_price', '중위 거래가'), ('median_ppm2', '㎡당 중위가'), ('n_trades', '거래량'),
                  ('yoy_price', '중위가 전년 대비'), ('yoy_n', '거래량 전년 대비')] %}
{% set dots = {'median_price': '--m-price', 'median_ppm2': '--m-ppm2', 'n_trades': '--m-count',
               'yoy_price': '--m-yoy', 'yoy_n': '--m-yoy'} %}
<section class="card filters">
  <div class="filter-row"><span class="filter-label">지표</span>{{ ui.pills('metric', metrics, 'median_price', '지표', dots) }}</div>
  <div class="filter-row"><span class="filter-label">면적</span>{{ ui.pills('band', bands, 'all', '면적 구간') }}</div>
  <div class="filter-row"><span class="filter-label">기간</span>
    <input type="month" id="from" aria-label="시작월"> ~ <input type="month" id="to" aria-label="종료월">
    <nav id="crumbs" class="crumbs" aria-label="지역 경로"></nav></div>
</section>
<p id="msg" class="meta"></p>
<div class="map-layout">
  <section class="card map-card"><div id="map" class="chart chart-map"></div></section>
  <section class="card rank-card">
    <h2 class="card-title" id="rank-title">순위</h2>
    <div id="side" class="side" hidden>
      <div class="side-head"><b id="side-name"></b>
        <span class="toolbar"><a id="side-link" href="#">추이에서 보기</a>
          <button type="button" id="side-drill" class="ghost small" hidden>하위 지역 보기</button></span></div>
      <div id="mini" class="chart small"></div>
    </div>
    <input type="search" id="rank-q" placeholder="지역 검색" aria-label="순위에서 지역 검색">
    <div class="legend-bar"><span class="ramp" id="legend-ramp"></span>
      <span class="legend-ends"><span id="legend-lo"></span><span id="legend-hi"></span></span></div>
    <ol id="ranking" class="rank-list"></ol>
  </section>
</div>
<section class="card">
  <h2 class="card-title">지표 비교</h2>
  <p class="card-sub">열 머리를 누르면 정렬합니다. 지역 이름을 누르면 추이 화면으로 갑니다.</p>
  <div class="table-wrap"><table id="compare" class="data-table"></table></div>
  <p class="table-foot" id="note"></p>
</section>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script src="{{ url_for('static', filename='js/map.js') }}"></script>{% endblock %}
```

- [ ] **Step 4: `static/js/map.js` 전체 교체**

```javascript
// 지도: 시도 → 시군구 → 읍면동 드릴다운 단계구분도 + 오른쪽 순위 목록 카드 + 아래 지표 비교표
// 색: 크기 지표 = 남색 7단계(--seq-*), 증감 지표 = 파랑↔회색↔빨강 5단계(--div-*, 상승 = 빨강). 지도와 순위 점이 같은 단계를 쓴다.
(async () => {
  const el = (id) => document.getElementById(id);
  const esc = App.escapeHtml;
  const cnt = (v) => `${App.fmt.int(v)}건`;
  // 지표 키(URL metric) → [이름, /api/map values의 필드, 형식, 색 종류]
  const METRICS = {
    median_price: ['중위 거래가', 'median_price', App.fmt.eok, 'seq'],
    median_ppm2: ['㎡당 중위가', 'median_ppm2', App.fmt.ppm2, 'seq'],
    n_trades: ['거래량', 'n', cnt, 'seq'],
    yoy_price: ['중위가 전년 대비', 'yoy_price', App.fmt.pct, 'div'],
    yoy_n: ['거래량 전년 대비', 'yoy_n', App.fmt.pct, 'div'],
  };
  const COLS = [
    { field: 'median_price', label: '중위가', f: App.fmt.eok, color: '--m-price' },
    { field: 'median_ppm2', label: '㎡당가', f: App.fmt.ppm2, color: '--m-ppm2' },
    { field: 'n', label: '거래량', f: cnt, color: '--m-count' },
    { field: 'yoy_price', label: '중위가 전년 대비', f: App.fmt.pct, div: true },
    { field: 'yoy_n', label: '거래량 전년 대비', f: App.fmt.pct, div: true },
  ];
  const RAMP = {
    seq: ['--seq-100', '--seq-200', '--seq-300', '--seq-400', '--seq-500', '--seq-600', '--seq-700'],
    div: ['--div-neg-2', '--div-neg-1', '--div-mid', '--div-pos-1', '--div-pos-2'],
  };
  const NEXT = { sido: 'sgg', sgg: 'umd' };
  const state = App.readState({ level: 'sido', parent: '', metric: 'median_price', band: 'all', from: '', to: '' });
  const chart = App.chart(el('map'));
  const mini = App.chart(el('mini'));
  const geoCache = {};
  let seq = 0;
  let miniSeq = 0;
  let data = null;
  let selected = null;   // 순위에서 고른 지역 코드
  let sort = null;       // 비교표 정렬 { field, dir }. null이면 현재 지표 내림차순

  const metricPills = App.pills(el('metric'), () => { sort = null; render(); });
  const bandPills = App.pills(el('band'), () => render());
  if (!(state.metric in METRICS)) state.metric = 'median_price';
  if (!bandPills.has(state.band)) state.band = 'all';
  if (!['sido', 'sgg', 'umd'].includes(state.level)) { state.level = 'sido'; state.parent = ''; }
  metricPills.value = state.metric;
  bandPills.value = state.band;

  async function geo(version, level, parent) {
    const file = level === 'sido' ? 'sido' : level === 'sgg' ? 'sgg' : `umd_${parent.slice(0, 2)}`;
    const url = `/static/geo/${encodeURIComponent(version)}/${file}.json`;
    if (!geoCache[url]) {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error('경계 파일을 불러오지 못했습니다.');
      geoCache[url] = await resp.json();
    }
    const fc = geoCache[url];
    if (level === 'sido') return fc;
    const key = level === 'sgg' ? 'sido_cd' : 'sgg_cd';
    return { type: 'FeatureCollection', features: fc.features.filter((f) => f.properties[key] === parent) };
  }

  function scaleOf(kind, field) {
    const nums = data.values.map((v) => v[field]).filter((v) => v != null);
    if (!nums.length) return null;
    if (kind === 'div') { const m = Math.max(...nums.map(Math.abs)) || 1; return { min: -m, max: m }; }
    return { min: Math.min(...nums), max: Math.max(...nums) };
  }
  function colorFor(v, kind, sc) {
    if (v == null || !sc) return App.css('--track');
    const stops = RAMP[kind];
    const t = sc.max === sc.min ? 1 : (v - sc.min) / (sc.max - sc.min);
    return App.css(stops[Math.round(Math.max(0, Math.min(1, t)) * (stops.length - 1))]);
  }
  // 빈 값은 방향과 관계없이 맨 아래
  function sortRows(rows, field, dir) {
    return [...rows].sort((a, b) => {
      if (field === 'name') return dir * a.name.localeCompare(b.name, 'ko');
      const x = a[field], y = b[field];
      if (x == null || y == null) return x == null && y == null ? a.name.localeCompare(b.name, 'ko') : x == null ? 1 : -1;
      return dir * (x - y) || a.name.localeCompare(b.name, 'ko');
    });
  }

  async function render() {
    const my = ++seq;
    state.metric = metricPills.value;
    state.band = bandPills.value;
    state.from = App.fromMonthInput(el('from').value) || state.from;
    state.to = App.fromMonthInput(el('to').value) || state.to;
    App.writeState(state);
    App.message(el('msg'), '');
    try {
      const apiData = await App.api('/api/map', { level: state.level, parent: state.parent, band: state.band, from: state.from, to: state.to });
      if (my !== seq) return;
      const fc = await geo(apiData.version, apiData.level, apiData.parent);
      if (my !== seq) return;
      data = apiData;
      if (selected && !data.values.some((v) => v.region_cd === selected)) { selected = null; el('side').hidden = true; }
      state.from = data.from; state.to = data.to;
      el('from').value = App.toMonthInput(data.from);
      el('to').value = App.toMonthInput(data.to);
      App.writeState(state);
      const name = `${data.version}:${data.level}:${data.parent || ''}`;
      echarts.registerMap(name, fc);
      drawMap(name);
      drawCrumbs();
      drawLegend();
      drawRanking();
      drawCompare();
      drawNote();
      if (selected) showMini(data.values.find((v) => v.region_cd === selected));
    } catch (e) { if (my !== seq) return; App.message(el('msg'), e.message); }
  }

  function drawMap(name) {
    const [title, field, f, kind] = METRICS[state.metric];
    const sc = scaleOf(kind, field);
    chart.setOption({
      tooltip: {
        trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
        formatter: (p) => {
          const v = p.data?.raw;
          if (!v) return '';
          return `<b>${esc(v.full_name)}</b><br>${title}: ${f(v[field])}<br>거래 ${cnt(v.n)}${state.metric.startsWith('yoy') ? '' : `<br>중위가 전년 대비 ${App.fmt.pct(v.yoy_price)}`}`;
        },
      },
      series: [{
        type: 'map', map: name, nameProperty: 'region_cd', roam: true, selectedMode: 'single',
        top: 12, bottom: 12, left: 8, right: 8,
        data: data.values.map((v) => {
          const c = colorFor(v[field], kind, sc);
          return { name: v.region_cd, value: v[field], raw: v, itemStyle: { areaColor: c },
            emphasis: { itemStyle: { areaColor: c } }, select: { itemStyle: { areaColor: c } } };
        }),
        itemStyle: { areaColor: App.css('--track'), borderColor: App.css('--card'), borderWidth: 1 },
        emphasis: { label: { show: true, color: App.css('--text'), formatter: (p) => p.data?.raw?.name ?? '' },
          itemStyle: { borderColor: App.css('--ink'), borderWidth: 2 } },
        select: { label: { show: true, color: App.css('--text'), formatter: (p) => p.data?.raw?.name ?? '' },
          itemStyle: { borderColor: App.css('--ink'), borderWidth: 2.5 } },
      }],
    }, true);
    if (selected) chart.dispatchAction({ type: 'select', seriesIndex: 0, name: selected });
  }

  function drawCrumbs() {
    const parts = [{ level: 'sido', parent: '', name: '전국' }];
    for (const p of data.parents) parts.push({ level: NEXT[p.level], parent: p.region_cd, name: p.name });
    el('crumbs').innerHTML = parts.map((p, i) => (i === parts.length - 1 ? `<b>${esc(p.name)}</b>`
      : `<a href="#" data-level="${esc(p.level)}" data-parent="${esc(p.parent)}">${esc(p.name)}</a>`)).join(' › ');
    el('crumbs').querySelectorAll('a').forEach((a) => {
      a.onclick = (ev) => {
        ev.preventDefault();
        state.level = a.dataset.level; state.parent = a.dataset.parent;
        selected = null; el('side').hidden = true;
        render();
      };
    });
  }

  function drawLegend() {
    const [, field, f, kind] = METRICS[state.metric];
    const sc = scaleOf(kind, field);
    el('legend-ramp').innerHTML = RAMP[kind].map((s) => `<i style="background:var(${s})"></i>`).join('');
    if (!sc) { el('legend-lo').textContent = '값 없음'; el('legend-hi').textContent = ''; return; }
    el('legend-lo').textContent = `${kind === 'div' ? '하락' : '낮음'} ${f(sc.min)}`;
    el('legend-hi').textContent = `${kind === 'div' ? '상승' : '높음'} ${f(sc.max)}`;
  }

  function drawRanking() {
    const [title, field, f, kind] = METRICS[state.metric];
    const sc = scaleOf(kind, field);
    el('rank-title').textContent = `${title} 순위`;
    const q = el('rank-q').value.trim();
    const items = sortRows(data.values, field, -1).map((v, i) => ({ v, i }))
      .filter(({ v }) => !q || v.name.includes(q) || v.full_name.includes(q));
    el('ranking').innerHTML = items.map(({ v, i }) => `<li><button type="button" class="rank-item${v.region_cd === selected ? ' on' : ''}" data-cd="${esc(v.region_cd)}">`
      + `<span class="rank-no">${v[field] == null ? '-' : `#${i + 1}`}</span>`
      + `<span class="rank-name"><b>${esc(v.name)}</b><small>거래 ${cnt(v.n)}</small></span>`
      + `<span class="rank-val">${f(v[field])}</span>`
      + `<span class="rank-dot" style="background:${colorFor(v[field], kind, sc)}"></span></button></li>`).join('')
      || '<li class="rank-empty">찾는 지역이 없습니다.</li>';
    el('ranking').querySelectorAll('.rank-item').forEach((b) => { b.onclick = () => select(b.dataset.cd); });
  }

  function drawCompare() {
    const s = sort || { field: METRICS[state.metric][1], dir: -1 };
    const maxes = Object.fromEntries(COLS.map((c) => [c.field, Math.max(0, ...data.values.map((v) => Math.abs(v[c.field] ?? 0)))]));
    const head = [{ field: 'name', label: '지역' }, ...COLS].map((c) => {
      const on = c.field === s.field;
      const ariaSort = on ? (s.dir > 0 ? 'ascending' : 'descending') : 'none';
      return `<th scope="col" aria-sort="${ariaSort}"${c.field === 'name' ? '' : ' class="num"'}><button type="button" class="sort" data-field="${c.field}">${c.label}`
        + `<span class="sort-ind" aria-hidden="true">${on ? (s.dir > 0 ? '▲' : '▼') : '▲▼'}</span></button></th>`;
    }).join('');
    const rows = sortRows(data.values, s.field, s.dir);
    const body = rows.map((v) => `<tr><td><a href="/trends?regions=${encodeURIComponent(`${data.level}:${v.region_cd}`)}&band=${encodeURIComponent(state.band)}"><b>${esc(v.name)}</b></a></td>`
      + COLS.map((c) => `<td class="num">${c.div ? App.divBarCell(v[c.field], maxes[c.field], c.f(v[c.field]))
        : App.barCell(v[c.field], maxes[c.field], c.color, c.f(v[c.field]))}</td>`).join('') + '</tr>').join('');
    el('compare').innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${COLS.length + 1}" class="muted">지역이 없습니다.</td></tr>`}</tbody>`;
    el('compare').querySelectorAll('button.sort').forEach((b) => {
      b.onclick = () => {
        const fld = b.dataset.field;
        const cur = sort || { field: METRICS[state.metric][1], dir: -1 };
        sort = { field: fld, dir: cur.field === fld ? -cur.dir : (fld === 'name' ? 1 : -1) };
        drawCompare();
      };
    });
  }

  function drawNote() {
    el('note').textContent = `${data.values.length}개 지역 · ${App.fmt.ym(data.from)}~${App.fmt.ym(data.to)} · 가격은 월별 중위가의 거래량 가중평균 · 전년 대비는 ${App.fmt.ym(data.prev_from)}~${App.fmt.ym(data.prev_to)}와 비교`
      + (data.coverage == null ? '' : ` · 읍면동 커버리지 ${data.coverage}% (좌표가 있는 단지의 거래 비율)`);
  }

  function select(code) {
    const v = data.values.find((x) => x.region_cd === code);
    if (!v) return;
    selected = code;
    chart.dispatchAction({ type: 'select', seriesIndex: 0, name: code });
    el('ranking').querySelectorAll('.rank-item').forEach((b) => b.classList.toggle('on', b.dataset.cd === code));
    showMini(v);
  }

  async function showMini(v) {
    if (!v) return;
    const my = ++miniSeq;
    const level = data.level, to = data.to;
    const key = `${level}:${v.region_cd}`;
    el('side').hidden = false;
    mini.resize();
    el('side-name').textContent = `${v.full_name} · 중위 거래가 최근 36개월`;
    el('side-link').href = `/trends?regions=${encodeURIComponent(key)}&band=${encodeURIComponent(state.band)}`;
    el('side-drill').hidden = !NEXT[level];
    el('side-drill').onclick = () => {
      state.parent = v.region_cd; state.level = NEXT[level];
      selected = null; el('side').hidden = true;
      render();
    };
    try {
      const agg = await App.api('/api/agg', { regions: key, band: state.band, from: App.shiftYm(to, -35), to });
      if (my !== miniSeq) return;
      const months = App.monthRange(agg.from, agg.to);
      const by = Object.fromEntries(agg.series[0].points.map((p) => [p.ym, p]));
      const base = App.baseOption();
      mini.setOption({
        ...base, legend: { show: false }, grid: { left: 56, right: 12, top: 12, bottom: 24 },
        xAxis: { ...base.xAxis, data: months.map(App.fmt.ym) },
        yAxis: { ...base.yAxis, scale: true, axisLabel: { color: App.css('--muted'), formatter: App.fmt.eok } },
        tooltip: { ...base.tooltip, valueFormatter: App.fmt.eok },
        series: [{ name: '중위 거래가', type: 'line', data: months.map((m) => by[m]?.median_price ?? null), color: App.seriesColor(0), symbol: 'none', lineStyle: { width: 2 } }],
      }, true);
    } catch (e) { if (my !== miniSeq) return; App.message(el('msg'), e.message); }
  }

  chart.on('click', (p) => {
    const v = p.data?.raw;
    if (!v) return;
    if (NEXT[state.level]) {
      showMini(v);
      state.parent = v.region_cd; state.level = NEXT[state.level];
      selected = null;
      render();
    } else {
      select(v.region_cd);
    }
  });
  el('rank-q').oninput = () => { if (data) drawRanking(); };
  for (const id of ['from', 'to']) el(id).onchange = () => render();
  render();
})();
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py tests/test_ui.py -v`
Expected: PASS

- [ ] **Step 6: 브라우저 확인(컨트롤러)**

`molit-seed`(경계 `2026-10`)에서 `/map`:
- 지표 알약 5개·면적 알약, 지도 좌·순위 카드 우, 아래 비교표
- **거래량** 알약: 지도 색·순위 값·범례가 채워짐(Review Focus 1)
- 순위 항목 클릭 → 지도 테두리 강조, 미니 추이, "하위 지역 보기"로 내려감. 지도 클릭 → 하위 단계
- 비교표: 열 머리 정렬(오름·내림 전환, `aria-sort` 변화), 증감 열 양방향 막대, 빈 값 맨 아래·`-`
- 검색: "종로" 입력 시 목록이 걸러짐, 없는 이름은 "찾는 지역이 없습니다."
- 옛 URL `/map?metric=yoy_n&band=gt85&level=sgg&parent=11` → 해당 알약 선택 상태, `/map?metric=bogus` → 중위 거래가(Review Focus 2)
- 폭 375px(`resize_window preset: mobile`)에서 1열, `document.documentElement.scrollWidth <= innerWidth`(Review Focus 4)
- 어두운 테마, 콘솔 오류 없음, 스크린샷

- [ ] **Step 7: 커밋**

```bash
git add templates/map.html static/js/map.js tests/test_pages.py
git commit -m "지도 화면 개편: 지표·면적 알약, 순위 목록 카드, 지표 비교표, 거래량 지표 값 연결 수정

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 대시보드 — 타일, 2열 차트 카드, 상승·하락 순위 목록

**Files:**
- Modify: `templates/dashboard.html`, `static/js/dashboard.js`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 1 `ui.page_head`, CSS `.tiles .tile .badge .grid-2 .card .rank-list .rank-item`; `/api/summary` `{version, provisional_from, kpi{ym, n, median, yoy_n, yoy_median}, series[{ym, n, median}], movers{up[], down[], window, min_trades}}` (movers 항목 `{region_cd, name, n, median, yoy}`)

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pages.py`의 `test_dashboard`에 덧붙인다:
```python
    assert '<header class="page-head">' in html
    assert 'id="up" class="rank-list"' in html and 'id="down" class="rank-list"' in html
    assert 'class="grid-2"' in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py::test_dashboard -v`
Expected: FAIL

- [ ] **Step 3: `templates/dashboard.html` 작성**

```html
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}대시보드 · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head('전국 아파트 매매 한눈에', '확정된 최근 달의 거래량·중위 거래가와 24개월 흐름, 전년보다 많이 오르고 내린 시군구를 봅니다.') }}{% endblock %}
{% block body %}
<p id="msg" class="meta"></p>
<div id="kpis" class="tiles"></div>
<div class="grid-2">
  <section class="card"><h2 class="card-title">전국 월별 거래량</h2><div id="vol" class="chart"></div></section>
  <section class="card"><h2 class="card-title">전국 월별 중위 거래가</h2><div id="price" class="chart"></div></section>
</div>
<p class="meta">음영 구간은 신고기한(30일)이 지나지 않은 잠정 자료입니다. 해제 거래는 제외했습니다.</p>
<section class="card">
  <h2 class="card-title">중위가 전년 대비 상승·하락 시군구</h2>
  <p class="card-sub" id="mover-note"></p>
  <div class="grid-2">
    <div><h3 class="list-title">상승 상위</h3><ol id="up" class="rank-list"></ol></div>
    <div><h3 class="list-title">하락 상위</h3><ol id="down" class="rank-list"></ol></div>
  </div>
</section>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>{% endblock %}
```

- [ ] **Step 4: `static/js/dashboard.js` 수정**

`const delta = …` 줄과 `el('kpis').innerHTML = …` 블록을 다음으로 바꾼다:
```javascript
  // 증감 배지: 색(상승 빨강·하락 파랑) + 화살표·부호를 함께 보여 색만으로 전하지 않는다
  const badge = (v, prefix = '') => (v == null ? `<span class="badge">${prefix}-</span>`
    : `<span class="badge ${v >= 0 ? 'up' : 'down'}">${prefix}${v >= 0 ? '▲' : '▼'} ${App.fmt.pct(v)}</span>`);
  el('kpis').innerHTML = `
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 거래량 (확정)</div><div class="v">${App.fmt.int(kpi.n)}건</div><div class="d">${badge(kpi.yoy_n, '전년 동월 대비 ')}</div></div>
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 전국 중위 거래가</div><div class="v">${App.fmt.eok(kpi.median)}</div><div class="d">${badge(kpi.yoy_median, '전년 동월 대비 ')}</div></div>
    <div class="tile"><div class="k">경계 버전</div><div class="v">${App.escapeHtml(data.version)}</div></div>`;
```
파일 끝의 `el('mover-note').textContent = …`부터 `el('down').innerHTML = …`까지를 다음으로 바꾼다:
```javascript
  el('mover-note').textContent = `${App.fmt.ym(movers.window[0])}~${App.fmt.ym(movers.window[1])} vs 전년 같은 기간 · 두 기간 모두 ${movers.min_trades}건 이상 · 중위가는 월별 중위가의 거래량 가중평균`;
  const list = (rows) => (rows.length ? rows.map((m, i) => `<li><a class="rank-item" href="/trends?regions=sgg:${m.region_cd}">`
    + `<span class="rank-no">#${i + 1}</span>`
    + `<span class="rank-name"><b>${App.escapeHtml(m.name)}</b><small>중위 ${App.fmt.eok(m.median)} · 거래 ${App.fmt.int(m.n)}건</small></span>`
    + `${badge(m.yoy)}</a></li>`).join('') : '<li class="rank-empty">해당 시군구 없음</li>');
  el('up').innerHTML = list(movers.up);
  el('down').innerHTML = list(movers.down);
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -v`
Expected: PASS

- [ ] **Step 6: 브라우저 확인(컨트롤러)**

`/`: 타일 3개(배지: 상승 빨강 ▲·하락 파랑 ▼), 차트 2개 2열(폭 900px 이하 1열), 상승·하락 순위 목록(항목 클릭 → `/trends?regions=sgg:…`), 잠정 음영, 툴팁, 어두운 테마, 폭 375px 가로 스크롤 없음, 콘솔 오류 없음, 스크린샷.

- [ ] **Step 7: 커밋**

```bash
git add templates/dashboard.html static/js/dashboard.js tests/test_pages.py
git commit -m "대시보드 개편: 증감 배지 타일, 2열 차트 카드, 상승·하락 순위 목록

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 추이 화면 — 알약과 차트 카드 도구 막대

**Files:**
- Modify: `templates/trends.html`, `static/js/trends.js`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 1 `ui.page_head`, `ui.pills`, `App.pills`, CSS `.filters .filter-row .card-head .toolbar .check .data-table .table-wrap .chips .chip`
- Produces: DOM id `metric`·`band`(알약), `sel-sido`, `sel-sgg`, `sel-umd`, `add`, `chips`, `from`, `to`, `ma`, `idx`, `toggle-table`, `png`, `chart`, `table`, `msg`, `chart-title`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pages.py`의 `test_trends_page`를 다음으로 바꾼다:
```python
def test_trends_page(client, seeded):
    html = client.get("/trends").get_data(as_text=True)
    assert 'id="chart"' in html and "js/trends.js" in html
    assert '<header class="page-head">' in html
    assert 'data-value="le60" aria-pressed="false"' in html and 'data-value="range"' in html
    assert 'id="metric" role="group"' in html and 'id="band" role="group"' in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py::test_trends_page -v`
Expected: FAIL

- [ ] **Step 3: `templates/trends.html` 작성**

```html
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}추이 · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head('지역별 추이', '지역을 최대 8개까지 골라 월별 중위 거래가·㎡당가·거래량을 비교합니다.') }}{% endblock %}
{% block body %}
{% set metrics = [('median_price', '중위 거래가'), ('median_ppm2', '㎡당 중위가'), ('n_trades', '거래량'), ('range', '25~75% 범위 (지역 1개)')] %}
{% set dots = {'median_price': '--m-price', 'median_ppm2': '--m-ppm2', 'n_trades': '--m-count', 'range': '--m-price'} %}
<section class="card filters">
  <div class="filter-row"><span class="filter-label">지역</span>
    <select id="sel-sido" aria-label="시도"></select>
    <select id="sel-sgg" aria-label="시군구"></select>
    <select id="sel-umd" aria-label="읍면동"></select>
    <button type="button" id="add" class="primary">지역 추가</button>
    <span class="muted">최대 8개 · 아무것도 고르지 않고 추가하면 전국</span></div>
  <div id="chips" class="chips"></div>
  <div class="filter-row"><span class="filter-label">지표</span>{{ ui.pills('metric', metrics, 'median_price', '지표', dots) }}</div>
  <div class="filter-row"><span class="filter-label">면적</span>{{ ui.pills('band', bands, 'all', '면적 구간') }}</div>
  <div class="filter-row"><span class="filter-label">기간</span>
    <input type="month" id="from" aria-label="시작월"> ~ <input type="month" id="to" aria-label="종료월"></div>
</section>
<p id="msg" class="meta"></p>
<section class="card">
  <div class="card-head"><h2 class="card-title" id="chart-title">추이</h2>
    <div class="toolbar">
      <label class="check"><input type="checkbox" id="ma"> 3개월 이동평균</label>
      <label class="check"><input type="checkbox" id="idx"> 시작월=100 지수</label>
      <button type="button" id="toggle-table" class="ghost small">표 보기</button>
      <button type="button" id="png" class="ghost small">PNG 저장</button>
    </div></div>
  <div id="chart" class="chart"></div>
  <p class="meta">음영 구간은 신고기한(30일)이 지나지 않은 잠정 자료입니다. 해제 거래는 제외했고, 거래가 없는 달은 선이 끊깁니다.</p>
  <div id="table" class="table-wrap" hidden></div>
</section>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script src="{{ url_for('static', filename='js/trends.js') }}"></script>{% endblock %}
```

- [ ] **Step 4: `static/js/trends.js` 수정**

(a) `// 지표/면적 검증`부터 `el('idx').checked = state.idx === '1';`까지를 다음으로 바꾼다:
```javascript
  // 지표·면적: 알약. 사용자가 바꾸면 다시 그린다.
  const metricPills = App.pills(el('metric'), () => render());
  const bandPills = App.pills(el('band'), () => render());
  if (!(state.metric in METRICS)) state.metric = 'median_price';
  if (!bandPills.has(state.band)) state.band = 'all';
  metricPills.value = state.metric;
  bandPills.value = state.band;
  el('ma').checked = state.ma === '1';
  el('idx').checked = state.idx === '1';
```
(b) `for (const id of ['metric', 'band', 'ma', 'idx', 'from', 'to']) el(id).onchange = () => render();`를 다음으로 바꾼다:
```javascript
  for (const id of ['ma', 'idx', 'from', 'to']) el(id).onchange = () => render();
```
(c) `readControls()`의 첫 두 줄을 다음으로 바꾼다:
```javascript
    state.metric = metricPills.value;
    state.band = bandPills.value;
```
(d) `draw()`의 `const [title, f] = METRICS[state.metric];` 다음 줄에 추가:
```javascript
    el('chart-title').textContent = state.idx ? `${title} (시작월=100)` : title;
```
(e) `drawTable` 안의 표 두 곳 `<table>`을 `<table class="data-table">`로 바꾼다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -v`
Expected: PASS

- [ ] **Step 6: 브라우저 확인(컨트롤러)**

`/trends`: 지역 추가·칩(왼쪽 띠 색 = 선 색), 지표·면적 알약 전환 시 URL 갱신·새로고침 후 같은 상태, 옛 URL `/trends?metric=range&band=le60&regions=sgg:11110` → 범위 알약·60㎡ 이하 선택(Review Focus 2), `metric=bogus` → 중위 거래가, 이동평균·지수·표 보기(`data-table`)·PNG, 거래 없는 달 끊김, 어두운 테마, 폭 375px, 콘솔 오류 없음, 스크린샷.

- [ ] **Step 7: 커밋**

```bash
git add templates/trends.html static/js/trends.js tests/test_pages.py
git commit -m "추이 화면 개편: 지표·면적 알약, 차트 카드 도구 막대, 비교표 스타일 표

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 단지 검색·상세

**Files:**
- Modify: `templates/complexes.html`, `templates/complex.html`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 1 `ui.page_head`, CSS `.card .filter-row .data-table .table-wrap .badge .grid-2`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pages.py`의 `test_complexes_search_page`와 `test_complex_page`에 덧붙인다:
```python
    # test_complexes_search_page 끝에
    assert '<header class="page-head">' in html and 'class="data-table"' in html
```
```python
    # test_complex_page의 첫 assert 다음에
    assert "<h1>청운아파트</h1>" in html and 'class="data-table"' in html and 'class="badge' in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -k complex -v`
Expected: FAIL

- [ ] **Step 3: `templates/complexes.html` 작성**

```html
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}단지 · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head('단지 검색', '단지명이나 지역 코드로 찾아 거래 이력과 면적·층 분포를 봅니다.') }}{% endblock %}
{% block body %}
<form class="card filters" method="get">
  <div class="filter-row">
    <input type="search" name="q" value="{{ q }}" placeholder="단지명" aria-label="단지명">
    <input type="text" name="region" value="{{ region }}" placeholder="지역 코드(시도 2·시군구 5·읍면동 8자리)" aria-label="지역 코드" style="flex:0 1 300px">
    <button class="primary">검색</button>
  </div>
</form>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
<div class="table-wrap"><table class="data-table">
  <thead><tr><th scope="col">단지</th><th scope="col">법정동(신고)</th><th scope="col" class="num">건축년도</th><th scope="col">최근 계약일</th></tr></thead>
  <tbody>
  {% for r in rows %}
    <tr><td><a href="/complexes/{{ r.apt_seq|urlencode }}"><b>{{ r.apt_nm or r.apt_seq }}</b></a><small>{{ r.region_name or '지역 미판정' }}</small></td>
      <td>{{ r.api_umd_nm or '' }}</td><td class="num">{{ r.build_year or '' }}</td><td>{{ r.last_deal_date or '' }}</td></tr>
  {% else %}
    <tr><td colspan="4" class="muted">검색 결과가 없습니다.</td></tr>
  {% endfor %}
  </tbody>
</table></div>
<p class="table-foot">{{ rows|length }}개 단지 · 최근 거래 순 최대 50개</p>
{% endblock %}
```

- [ ] **Step 4: `templates/complex.html` 작성**

```html
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}{{ c.apt_nm }} · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head(c.apt_nm, c.region_name or '지역 미판정') }}{% endblock %}
{% block body %}
<div class="toolbar" style="margin-bottom:16px">
  <span class="badge">신고 법정동 {{ c.api_umd_nm or '-' }}</span>
  <span class="badge">지번 {{ c.jibun or '-' }}{% if c.road_nm %} · {{ c.road_nm }}{% endif %}</span>
  <span class="badge">건축 {{ c.build_year or '-' }}년</span>
  <span class="badge">좌표 {% if c.lon is not none %}{{ '%.5f'|format(c.lon) }}, {{ '%.5f'|format(c.lat) }} ({{ c.geocode_status }}{% if c.region_match %}, {{ c.region_match }}{% endif %}){% else %}없음 ({{ c.geocode_status }}){% endif %}</span>
  {% if c.sgg_mismatch %}<span class="badge warn">신고 시군구와 좌표 시군구가 다름</span>{% endif %}
</div>
<p id="msg" class="meta"></p>
<section class="card"><h2 class="card-title">거래 가격 (해제 제외)</h2><div id="scatter" class="chart"></div></section>
<div class="grid-2">
  <section class="card"><h2 class="card-title">면적 구간별 거래 수</h2><div id="areas" class="chart small"></div></section>
  <section class="card"><h2 class="card-title">층별 거래 수</h2><div id="floors" class="chart small"></div></section>
</div>
<section class="card">
  <h2 class="card-title">최근 거래 {{ trades|length }}건</h2>
  <div class="table-wrap"><table class="data-table">
    <thead><tr><th scope="col">계약일</th><th scope="col" class="num">거래금액(만원)</th><th scope="col" class="num">전용(㎡)</th><th scope="col" class="num">㎡당(만원)</th><th scope="col" class="num">층</th><th scope="col">동</th><th scope="col">거래유형</th></tr></thead>
    <tbody>
    {% for t in trades %}
      <tr class="{{ 'cancel' if t.is_cancelled }}"><td>{{ t.deal_date or '' }}</td><td class="num">{{ t.deal_amount|comma }}</td>
        <td class="num">{{ t.area if t.area is not none else '' }}</td><td class="num">{{ '%.0f'|format(t.ppm2) if t.ppm2 is not none else '' }}</td>
        <td class="num">{{ t.floor if t.floor is not none else '' }}</td><td>{{ t.apt_dong or '' }}</td><td>{{ t.dealing_gbn or '' }}</td></tr>
    {% endfor %}
    </tbody>
  </table></div>
  <p class="table-foot">해제 거래는 취소선으로 표시합니다.</p>
</section>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script>window.APT_SEQ = {{ c.apt_seq|tojson }};</script>
<script src="{{ url_for('static', filename='js/complex.js') }}"></script>{% endblock %}
```
(`tests/test_pages.py::test_complex_page`의 `"서울특별시 종로구 청운동" in html`은 머리 영역 설명으로 계속 통과한다.)

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -v`
Expected: PASS

- [ ] **Step 6: 브라우저 확인(컨트롤러)**

`/complexes?q=단지` → 표(단지명 굵은 남색·지역 회색), 상세 → 머리 영역·배지·차트 카드 3개(산점도 범례 3개·툴팁)·거래 표(취소선), 어두운 테마, 폭 375px(표는 안에서 가로 스크롤), 콘솔 오류 없음, 스크린샷.

- [ ] **Step 7: 커밋**

```bash
git add templates/complexes.html templates/complex.html tests/test_pages.py
git commit -m "단지 검색·상세 개편: 머리 영역, 배지, 차트 카드, 비교표 스타일 표

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 추출·거래 목록·수집 현황·로그인, README

**Files:**
- Modify: `templates/export.html`, `static/js/export.js`, `templates/trades.html`, `templates/status.html`, `templates/login.html`, `README.md`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 1 `ui.page_head`, `ui.pills`, `App.pills`; `/export.csv`·`/export.parquet` 인자 `target`, `band`, `region`, `from`, `to`, `cancelled`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_pages.py`에 덧붙이고 `test_export_page`·`test_status_shows_aggregates`를 다음으로 바꾼다:
```python
def test_export_page(client, seeded):
    html = client.get("/export").get_data(as_text=True)
    assert 'id="export-form"' in html and "/export/codebook.csv" in html
    assert '<header class="page-head">' in html
    assert 'type="hidden" name="target" id="target-v" value="raw"' in html
    assert 'type="hidden" name="band" id="band-v" value="all"' in html
    assert 'data-value="parquet"' in html and "<select name=" not in html


def test_status_shows_aggregates(client, seeded):
    html = client.get("/status").get_data(as_text=True)
    assert "집계 대기 계약월" in html and '<header class="page-head">' in html


def test_trades_and_login_heads(client, app, seeded):
    assert '<header class="page-head">' in client.get("/trades").get_data(as_text=True)
    anon = app.test_client()
    html = anon.get("/login").get_data(as_text=True)
    assert '<header class="page-head">' in html and 'type="password"' in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -k "export or status or heads" -v`
Expected: FAIL

- [ ] **Step 3: `templates/export.html` 작성**

```html
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}추출 · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head('데이터 추출', '원본 거래나 월별 집계를 CSV·Parquet로 내려받습니다. 열 설명은 코드북에 있습니다.') }}{% endblock %}
{% block body %}
<form id="export-form" class="card filters" method="get" action="/export.csv">
  <input type="hidden" name="target" id="target-v" value="raw">
  <input type="hidden" name="band" id="band-v" value="all">
  <div class="filter-row"><span class="filter-label">대상</span>{{ ui.pills('target', [('raw', '원본 거래'), ('agg', '월별 집계')], 'raw', '대상') }}
    <label class="check"><input type="checkbox" name="cancelled" value="1"> 해제 거래 포함(원본)</label></div>
  <div class="filter-row"><span class="filter-label">형식</span>{{ ui.pills('format', [('csv', 'CSV (엑셀·Stata)'), ('parquet', 'Parquet (Python·R)')], 'csv', '형식') }}</div>
  <div class="filter-row"><span class="filter-label">면적</span>{{ ui.pills('band', bands, 'all', '면적 구간') }}</div>
  <div class="filter-row"><span class="filter-label">지역</span>
    <select id="sel-sido" aria-label="시도"></select><select id="sel-sgg" aria-label="시군구"></select><select id="sel-umd" aria-label="읍면동"></select>
    <input type="hidden" name="region" id="region"></div>
  <div class="filter-row"><span class="filter-label">기간</span>
    <input type="month" id="from" aria-label="시작월"> ~ <input type="month" id="to" aria-label="종료월">
    <input type="hidden" name="from" id="from-v"><input type="hidden" name="to" id="to-v"></div>
  <div class="filter-row"><button class="primary">내려받기</button></div>
</form>
<p id="msg" class="meta"></p>
<section class="card">
  <h2 class="card-title">안내</h2>
  <ul class="notes">
    <li>원본은 한 번에 최대 60개월까지 받을 수 있습니다. 전국 전체 기간은 연도별로 나눠 받으세요.</li>
    <li>지역은 최신 경계 기준입니다(단지 좌표로 판정, 좌표가 없으면 시군구는 신고 코드). 지역을 고르지 않으면 전국입니다.</li>
    <li>원본의 열 이름은 국토부 API 필드명입니다. <a href="/export/codebook.csv">코드북(열 설명) 내려받기</a></li>
    <li>CSV는 UTF-8(BOM 포함)이라 엑셀에서 한글이 깨지지 않습니다. Parquet는 숫자·날짜 형식이 보존되고 크기가 작습니다.</li>
  </ul>
</section>
{% endblock %}
{% block scripts %}<script src="{{ url_for('static', filename='js/common.js') }}"></script>
<script src="{{ url_for('static', filename='js/export.js') }}"></script>{% endblock %}
```

- [ ] **Step 4: `static/js/export.js` 수정**

`const form = el('export-form');` 다음 줄에 추가:
```javascript
  // 알약 선택값은 같은 이름의 숨은 입력(target, band)으로 폼에 실린다. 형식은 폼 주소로 고른다.
  const targetPills = App.pills(el('target'), (v) => { el('target-v').value = v; });
  App.pills(el('band'), (v) => { el('band-v').value = v; });
  const formatPills = App.pills(el('format'));
```
`form.onsubmit` 안에서 다음 두 줄을 바꾼다:
```javascript
    if (el('export-form').querySelector('[name=target]').value === 'raw' && fromV && toV) {
```
→
```javascript
    if (targetPills.value === 'raw' && fromV && toV) {
```
```javascript
    form.action = el('format').value === 'parquet' ? '/export.parquet' : '/export.csv';
```
→
```javascript
    form.action = formatPills.value === 'parquet' ? '/export.parquet' : '/export.csv';
```
또한 같은 함수 안의 영어 주석 `// Check if from > to`, `// Check if target is 'raw' and month span > 60`을 각각 `// 시작월이 종료월보다 늦으면 막는다`, `// 원본은 60개월까지`로 바꾼다.

- [ ] **Step 5: 거래 목록·수집 현황·로그인 머리 영역**

`templates/trades.html` 첫 줄 `{% extends "base.html" %}` 다음에 추가하고, `{% block body %}` 다음의 `<form class="bar" method="get">`를 `<form class="card filters" method="get"><div class="filter-row">`로, 그 폼의 닫는 `</form>` 바로 앞에 `</div>`를 넣는다. 표 `<div class="wrap">\n  <table>`은 `<div class="table-wrap">\n  <table class="data-table">`로 바꾼다:
```html
{% import "_ui.html" as ui %}
{% block title %}거래 목록 · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head('거래 목록', '수집한 원본 거래를 지역·기간·검색어로 찾아봅니다.') }}{% endblock %}
```
`templates/status.html` 첫 줄 다음(제목 블록 다음)에 추가:
```html
{% import "_ui.html" as ui %}
{% block page_head %}{{ ui.page_head('수집 현황', '수집 진행, 누락·오류 점검, 단지 좌표와 월별 집계 상태를 봅니다.') }}{% endblock %}
```
`templates/login.html` 전체를 다음으로 바꾼다:
```html
{% extends "base.html" %}
{% import "_ui.html" as ui %}
{% block title %}로그인 · 아파트 실거래가{% endblock %}
{% block page_head %}{{ ui.page_head('로그인', '연구실 공유 비밀번호로 들어갑니다.') }}{% endblock %}
{% block body %}
<form method="post" class="card filters" style="max-width:480px">
  <div class="filter-row">
    <input type="password" name="password" placeholder="비밀번호" aria-label="비밀번호" autofocus required>
    <button class="primary">로그인</button>
  </div>
  {% if error %}<p class="err">{{ error }}</p>{% endif %}
</form>
{% endblock %}
```

- [ ] **Step 6: README 갱신**

`README.md`의 `## 지리 데이터` 절 바로 앞에 추가:
```markdown
## 화면 디자인
공통 스타일은 `static/css/app.css`(색 토큰·부품), 반복 조각은 `templates/_ui.html`(머리 영역·알약 매크로)에 있습니다.
글꼴은 Pretendard 가변 글꼴(SIL OFL 1.1, `static/fonts/pretendard/OFL.txt`)을 저장소에서 직접 제공합니다.
색 규칙: 크기는 남색 한 가지 색의 진하기, 증감은 파랑↔빨강(상승 = 빨강). 토큰 대비는 `tests/test_ui.py`가 검사합니다.
```

- [ ] **Step 7: 전체 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -q`
Expected: PASS(전체)

- [ ] **Step 8: 브라우저 확인(컨트롤러)**

`/export`: 알약 전환 → 숨은 입력 값 변경(`target-v`, `band-v`), 형식 Parquet 선택 시 폼 주소 `/export.parquet`(fetch로 헤더만 확인, 파일 저장 없이), 61개월 원본 사전 차단 메시지. `/trades`, `/status`, `/login`(로그아웃 후) 머리 영역·카드. 전 화면 어두운 테마·폭 375px·콘솔 오류 없음, 스크린샷.

- [ ] **Step 9: 커밋**

```bash
git add templates/export.html static/js/export.js templates/trades.html templates/status.html templates/login.html README.md tests/test_pages.py
git commit -m "추출·거래 목록·수집 현황·로그인 개편과 화면 디자인 문서

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

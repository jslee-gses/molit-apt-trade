from datetime import datetime

import pytest

import settings
from collector import store
from tests.analytics_fixtures import seed_analytics


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])


@pytest.fixture
def seeded(pg, monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [])   # client 픽스처의 wire()가 바꾼 것을 되돌림
    with pg.connection() as conn:
        seed_analytics(conn)


def test_dashboard(client, seeded):
    html = client.get("/").get_data(as_text=True)
    assert 'id="kpis"' in html and "echarts.min.js" in html and "js/dashboard.js" in html
    assert 'href="/trends"' in html and 'href="/map"' in html and 'href="/export"' in html
    assert "국토지리정보원" in html and "CC BY" in html    # 경계 출처 표시(라이선스 조건)
    assert "통계청 SGIS" in html and "공공누리 제1유형" in html and "vuski/admdongkor" in html and "CC BY 4.0" in html
    assert '<header class="page-head">' in html
    assert 'id="up" class="rank-list"' in html and 'id="down" class="rank-list"' in html
    assert 'class="grid-2"' in html


def test_trades_list_moved(client, seeded):
    html = client.get("/trades?lawd_cd=11140").get_data(as_text=True)
    assert "무교타워" in html and "청운아파트" not in html


def test_trends_page(client, seeded):
    html = client.get("/trends").get_data(as_text=True)
    assert 'id="chart"' in html and "js/trends.js" in html
    assert '<header class="page-head">' in html
    assert 'data-value="le60" aria-pressed="false"' in html and 'data-value="range"' in html
    assert 'id="metric" role="group"' in html and 'id="band" role="group"' in html


def test_map_page(client, seeded):
    html = client.get("/map").get_data(as_text=True)
    assert 'id="map"' in html and 'id="ranking"' in html and "js/map.js" in html
    assert '>행정구역</a>' in html and "<h1>행정구역</h1>" in html
    assert '<meta name="asset-version" content="' in html     # 경계 파일 주소의 배포 버전(common.js App.geoUrl)
    assert '<header class="page-head">' in html
    assert 'id="compare"' in html and 'class="data-table"' in html and 'id="rank-q"' in html
    assert 'data-value="yoy_n" aria-pressed="false"' in html and 'data-value="median_price" aria-pressed="true"' in html
    assert 'data-value="le60"' in html and "<select" not in html
    assert 'id="pts"' not in html and "단지 표시" not in html      # 단지 위치는 단지 화면에서만
    # 행정구역 경로는 지도 상자 안(지도 위 왼쪽)
    card = html.split('class="card map-card">')[1].split("</section>")[0]
    assert '<nav id="crumbs" class="map-crumbs"' in card and 'id="map"' in card


def test_complexes_search_page(client, seeded):
    html = client.get("/complexes?q=청운").get_data(as_text=True)
    assert "청운아파트" in html and 'href="/complexes/A"' in html and "무교타워" not in html
    assert '<header class="page-head">' in html and 'class="data-table"' in html
    assert 'id="cmap" class="basemap"' in html and "leaflet" in html and "js/complexes.js" in html
    assert '<meta name="vworld-key"' in html


def test_complexes_region_selects(client, seeded):
    """지역은 시도 → 시군구 → 읍면동 선택 상자. 고른 시군구·읍면동의 단지는 목록에 모두 나온다."""
    html = client.get("/complexes").get_data(as_text=True)
    assert '<select name="sido"' in html and '<option value="11">서울특별시</option>' in html
    assert '<select name="sgg"' in html and '<select name="umd"' in html and 'name="region"' not in html
    assert "최근 거래 순 최대 50개" in html
    html = client.get("/complexes?sido=11&sgg=11110").get_data(as_text=True)
    assert '<option value="11" selected>서울특별시</option>' in html
    assert '<option value="11110" selected>종로구</option>' in html and '<option value="11110101">청운동</option>' in html
    assert "청운아파트" in html and "신교빌" in html and "무교타워" not in html
    assert "이 지역 단지 모두" in html
    html = client.get("/complexes?sido=11&sgg=11110&umd=11110102").get_data(as_text=True)
    assert "신교빌" in html and "청운아파트" not in html
    assert '"region": "11110102"' in html                       # 지도도 같은 지역


def test_complexes_map_crumbs(client, seeded):
    """단지 지도 왼쪽 위 경로: 전국 › 시도 › 시군구 › 읍면동, 상위 단계는 그 지역을 고른 검색으로."""
    def crumbs(url):
        html = client.get(url).get_data(as_text=True)
        return html.split('class="map-crumbs"')[1].split("</nav>")[0]
    c = crumbs("/complexes")
    assert "<b>전국</b>" in c and "<a" not in c
    c = crumbs("/complexes?sido=11&sgg=11110&umd=11110101")
    assert 'href="/complexes">전국</a>' in c and 'href="/complexes?sido=11">서울특별시</a>' in c
    assert 'href="/complexes?sido=11&amp;sgg=11110">종로구</a>' in c and "<b>청운동</b>" in c


def test_complexes_mismatched_selects_use_consistent_part(client, seeded):
    """상위를 바꿨는데 하위 값이 남은 주소(예: sido=26&sgg=11110)는 상위에 속하지 않는 하위를 버린다."""
    html = client.get("/complexes?sido=26&sgg=11110&umd=11110101").get_data(as_text=True)
    assert '"region": "26"' in html and "청운아파트" not in html


def test_complexes_bad_region(client, seeded):
    html = client.get("/complexes?region=1").get_data(as_text=True)
    assert "지역 코드는" in html


def test_complex_page_breadcrumb_back_to_regions(client, seeded):
    """단지 상세에서 판정된 행정구역 단계로 돌아가는 경로(단지 검색의 지역 선택 상태로)."""
    html = client.get("/complexes/A").get_data(as_text=True)
    head = html.split('class="loc-head"')[1].split('id="loc"')[0]      # 경로는 '위치' 제목 옆
    assert '<nav class="loc-crumbs"' in head and "<h2 class=\"card-title\">위치</h2>" in head
    assert 'class="page-crumbs"' not in html and 'class="map-crumbs"' not in html and "점을 누르면 이동" not in html
    assert 'href="/complexes">단지</a>' in html
    assert 'href="/complexes?sido=11">서울특별시</a>' in html
    assert 'href="/complexes?sido=11&amp;sgg=11110">종로구</a>' in html
    assert 'href="/complexes?sido=11&amp;sgg=11110&amp;umd=11110101">청운동</a>' in html
    c = client.get("/complexes/C").get_data(as_text=True)      # 읍면동 미판정: 신고 시군구까지
    assert 'href="/complexes?sido=11&amp;sgg=11140">중구</a>' in c and "umd=" not in c.split('class="loc-crumbs"')[1].split("</nav>")[0]


def test_complex_page(client, seeded):
    html = client.get("/complexes/A").get_data(as_text=True)
    assert "청운아파트" in html and "서울특별시 종로구 청운동" in html and 'id="scatter"' in html
    assert "<h1>청운아파트</h1>" in html and 'class="data-table"' in html and 'class="badge' in html
    assert 'id="loc"' in html and "지역 판정: 경계" in html and "위치: 수동" not in html
    # 왼쪽 지도 + 오른쪽 그래프(위쪽 고정), 아래 최근 거래
    assert '<body class="fit">' in html and 'class="complex-top"' in html and 'class="card trades-card"' in html
    top = html.split('class="complex-top"')[1].split('class="card trades-card"')[0]
    assert 'id="loc"' in top and 'id="scatter"' in top and 'id="stat-areas"' in top
    assert 'id="stat-max"' in top and 'id="stat-min"' in top and 'id="areas"' not in html and 'id="floors"' not in html
    assert "126.955" not in html and "37.575" not in html          # 좌표 숫자를 화면에 쓰지 않는다
    c = client.get("/complexes/C").get_data(as_text=True)
    assert 'id="loc"' not in c and "위치 정보 없음" in c and "지역 판정: 미판정" in c
    assert client.get("/complexes/ZZZ").status_code == 404


def test_no_coords_csv(client, seeded):
    """좌표 없는 단지 전부를 주소와 함께 CSV로(엑셀용 BOM)."""
    resp = client.get("/status/no-coords.csv")
    assert resp.status_code == 200 and "no_coords_complexes.csv" in resp.headers["Content-Disposition"]
    text = resp.get_data(as_text=True)
    assert text.startswith("﻿단지 코드,단지명,상태,시군구,법정동,지번,지번 주소")
    assert "무교타워,못 찾음" in text and "청운아파트" not in text
    assert "/status/no-coords.csv" in client.get("/status").get_data(as_text=True)


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

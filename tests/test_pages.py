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
    assert 'id="pts" role="group"' in html and 'data-value="1" aria-pressed="true"' in html
    assert "단지 표시" in html


def test_complexes_search_page(client, seeded):
    html = client.get("/complexes?q=청운").get_data(as_text=True)
    assert "청운아파트" in html and 'href="/complexes/A"' in html and "무교타워" not in html
    assert '<header class="page-head">' in html and 'class="data-table"' in html


def test_complexes_bad_region(client, seeded):
    html = client.get("/complexes?region=1").get_data(as_text=True)
    assert "지역 코드는" in html


def test_complex_page(client, seeded):
    html = client.get("/complexes/A").get_data(as_text=True)
    assert "청운아파트" in html and "서울특별시 종로구 청운동" in html and 'id="scatter"' in html
    assert "<h1>청운아파트</h1>" in html and 'class="data-table"' in html and 'class="badge' in html
    assert 'id="loc"' in html and "지역 판정: 경계" in html and "위치: 수동" not in html
    assert "126.955" not in html and "37.575" not in html          # 좌표 숫자를 화면에 쓰지 않는다
    c = client.get("/complexes/C").get_data(as_text=True)
    assert 'id="loc"' not in c and "위치 정보 없음" in c and "지역 판정: 미판정" in c
    assert client.get("/complexes/ZZZ").status_code == 404


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

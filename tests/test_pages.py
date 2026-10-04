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
    assert '<header class="page-head">' in html
    assert 'id="compare"' in html and 'class="data-table"' in html and 'id="rank-q"' in html
    assert 'data-value="yoy_n" aria-pressed="false"' in html and 'data-value="median_price" aria-pressed="true"' in html
    assert 'data-value="le60"' in html and "<select" not in html


def test_complexes_search_page(client, seeded):
    html = client.get("/complexes?q=청운").get_data(as_text=True)
    assert "청운아파트" in html and 'href="/complexes/A"' in html and "무교타워" not in html


def test_complexes_bad_region(client, seeded):
    html = client.get("/complexes?region=1").get_data(as_text=True)
    assert "지역 코드는" in html


def test_complex_page(client, seeded):
    html = client.get("/complexes/A").get_data(as_text=True)
    assert "청운아파트" in html and "서울특별시 종로구 청운동" in html and 'id="scatter"' in html
    assert client.get("/complexes/ZZZ").status_code == 404


def test_export_page(client, seeded):
    html = client.get("/export").get_data(as_text=True)
    assert 'id="export-form"' in html and "/export/codebook.csv" in html


def test_status_shows_aggregates(client, seeded):
    html = client.get("/status").get_data(as_text=True)
    assert "집계 대기 계약월" in html

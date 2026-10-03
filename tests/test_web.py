from datetime import datetime

import pytest

import settings
from collector import store
from tests.helpers import add_job, item


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])


@pytest.fixture
def seeded(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        add_job(conn, "11140", "202601")
        store.save_job(conn, "11110", "202601", [item(aptNm="종로아파트"), item(aptNm="해제단지", cdealType="O")], 2)
        store.save_job(conn, "11140", "202601", [item(sggCd="11140", aptNm="중구아파트")], 1)


def test_index_lists_trades(client, seeded):
    html = client.get("/").get_data(as_text=True)
    assert "종로아파트" in html and "중구아파트" in html
    assert "조건에 맞는 거래 3건" in html


def test_index_filters(client, seeded):
    html = client.get("/?lawd_cd=11140").get_data(as_text=True)
    assert "중구아파트" in html and "종로아파트" not in html
    html = client.get("/?q=종로").get_data(as_text=True)
    assert "조건에 맞는 거래 1건" in html
    html = client.get("/?sido=서울특별시&exclude_cancelled=1").get_data(as_text=True)
    assert "해제단지" not in html and "조건에 맞는 거래 2건" in html


def test_status_page(client, seeded):
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "누락 점검" in resp.get_data(as_text=True)


def test_download_requires_filter(client, seeded):
    resp = client.get("/download.csv")
    assert resp.status_code == 400


def test_download_csv(client, seeded):
    resp = client.get("/download.csv?year=2026")
    body = resp.get_data()
    assert resp.status_code == 200
    assert body.startswith("\ufeff".encode("utf-8"))
    text = body.decode("utf-8-sig")
    header = text.splitlines()[0].split(",")
    assert header[:3] == ["계약일", "시도", "시군구"]
    assert "종로아파트" in text and "2026-01-05" in text


def test_api_trades_camel_case(client, seeded):
    data = client.get("/api/trades?lawd_cd=11110").get_json()
    assert len(data) == 2
    row = data[0]
    assert row["dealDate"] == "2026-01-05"
    assert row["dealAmount"] == 84000
    assert row["excluUseAr"] == 84.97
    assert row["sido"] == "서울특별시" and row["sigungu"] == "종로구"
    assert "deal_amount" not in row and "id" not in row


def test_api_status_formats_datetime(client, seeded):
    data = client.get("/api/status").get_json()
    assert data["last_fetched"] == "2026-10-03 07:00:00"
    assert data["by_status"]["done"] == 2


def test_api_quality(client, seeded):
    data = client.get("/api/quality").get_json()
    assert data["cancelled"] == 1


def test_index_bad_page_is_400(client, seeded):
    r = client.get("/?page=abc")
    assert r.status_code == 400
    assert "page" in r.get_data(as_text=True)
    assert client.get("/?page=0").status_code == 200       # 1로 보정


def test_api_trades_bad_limit(client, seeded):
    r = client.get("/api/trades?limit=abc")
    assert r.status_code == 400 and "error" in r.get_json()
    assert client.get("/api/trades?limit=0").status_code == 400
    assert client.get("/api/trades?limit=-5").status_code == 400
    assert len(client.get("/api/trades?limit=1").get_json()) == 1

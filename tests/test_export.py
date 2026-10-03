import csv
import io
from datetime import date, datetime

import pyarrow.parquet as pq
import pytest

import settings
from analytics import export
from collector import api, store
from tests.analytics_fixtures import seed_analytics


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])


@pytest.fixture
def seeded(pg, monkeypatch):
    from analytics import aggregates
    from tests.analytics_fixtures import V
    monkeypatch.setattr(store, "AFTER_SAVE", [])   # client 픽스처의 wire()가 바꾼 것을 되돌림
    with pg.connection() as conn:
        seed_analytics(conn)
        conn.execute("UPDATE trades SET cdeal_type = 'O' WHERE apt_seq = 'B' AND deal_ymd = '202601' "
                     "AND deal_day = '4'")
        aggregates.refresh_month(conn, "202601", V)


def read_csv(resp):
    body = resp.get_data()
    assert body.startswith("﻿".encode())
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def test_raw_csv(client, seeded):
    resp = client.get("/export.csv?target=raw&region=sgg:11110&from=202601&to=202602")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    rows = read_csv(resp)
    assert len(rows) == 9                                # 5건 x 2개월 - 해제 1건
    assert list(rows[0])[:4] == ["lawd_cd", "deal_ymd", "sggCd", "umdCd"]
    assert {"aptSeq", "dealAmount", "dealDate", "region_sgg_cd", "region_umd_cd", "is_cancelled"} <= set(rows[0])
    assert rows[0]["dealDate"].startswith("2026-0")


def test_raw_csv_include_cancelled(client, seeded):
    rows = read_csv(client.get("/export.csv?target=raw&region=sgg:11110&from=202601&to=202602&cancelled=1"))
    assert len(rows) == 10 and sum(r["is_cancelled"] == "1" for r in rows) == 1


def test_raw_region_umd_uses_complex_region(client, seeded):
    rows = read_csv(client.get("/export.csv?target=raw&region=umd:11110102&from=202601&to=202601"))
    assert {r["aptSeq"] for r in rows} == {"B"}


def test_raw_parquet_types(client, seeded):
    resp = client.get("/export.parquet?target=raw&region=sgg:11140&from=202601&to=202603")
    table = pq.read_table(io.BytesIO(resp.get_data()))
    assert table.num_rows == 6
    schema = table.schema
    assert str(schema.field("dealAmount").type) == "int32"
    assert str(schema.field("excluUseAr").type) == "double"
    assert str(schema.field("dealDate").type) == "date32[day]"
    assert table.column("dealDate")[0].as_py() == date(2026, 1, 6)


def test_agg_csv_and_parquet(client, seeded):
    rows = read_csv(client.get("/export.csv?target=agg&region=sgg:11110&from=202601&to=202603&band=all"))
    assert {r["size_band"] for r in rows} == {"all", "le60", "gt85"}   # 지역을 고르면 면적 구간 전부
    rows = [r for r in rows if r["size_band"] == "all"]
    assert [r["ym"] for r in rows] == ["202601", "202602", "202603"]
    assert rows[0]["region_name"] == "서울특별시 종로구" and rows[0]["n_trades"] == "4"
    table = pq.read_table(io.BytesIO(client.get(
        "/export.parquet?target=agg&region=nation:00&from=202601&to=202603").get_data()))
    assert table.num_rows == 3 * 4                       # 3개월 x 면적 구간 4개(전체 포함)
    assert str(table.schema.field("n_trades").type) == "int32"


def test_export_raw_limit(client, seeded):
    resp = client.get("/export.csv?target=raw&from=202001&to=202501")
    assert resp.status_code == 400 and "60개월" in resp.get_json()["error"]


def test_export_bad_region(client, seeded):
    assert client.get("/export.csv?target=raw&region=sgg:1&from=202601&to=202601").status_code == 400


def test_legacy_download_redirects(client, seeded):
    resp = client.get("/download.csv?lawd_cd=11110&year=2026")
    assert resp.status_code == 301
    assert resp.headers["Location"].startswith("/export.csv?")
    rows = read_csv(client.get(resp.headers["Location"]))
    assert len(rows) == 9 * 5 - 1                        # 2026-01~09, 해제 1건 제외


def test_legacy_sido_name(client, seeded):
    p = export.parse_args({"sido": "서울특별시", "ymd": "202601", "target": "raw"})
    assert p["region"] == ("sido", "11") and (p["ym_from"], p["ym_to"]) == ("202601", "202601")


def test_codebook_covers_raw_columns(client):
    rows = list(csv.DictReader(io.StringIO(client.get("/export/codebook.csv").get_data().decode("utf-8-sig"))))
    names = {r["열 이름"] for r in rows}
    assert {c[1] for c in export.RAW_COLUMNS} <= names
    assert {c[1] for c in export.AGG_COLUMNS} <= names
    assert set(api.FIELDS) <= names


def test_legacy_download_with_search(client, seeded):
    resp = client.get("/download.csv?lawd_cd=11110&year=2026&q=신교")
    assert resp.status_code == 301
    rows = read_csv(client.get(resp.headers["Location"]))
    assert len(rows) == 17                                      # 9 * 2 - 1 해제 제외
    assert {r["aptSeq"] for r in rows} == {"B"}                # 신교동 단지는 B만

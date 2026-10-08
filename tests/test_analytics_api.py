from datetime import datetime

import pytest

import settings
from analytics import params, queries
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


def test_params_helpers():
    assert params.ym("2026-01", "from") == "202601"
    with pytest.raises(params.BadParam):
        params.ym("202613", "from")
    assert params.region_items("sgg:11110, umd:11110101") == [("sgg", "11110"), ("umd", "11110101")]
    assert params.region_items("") == []
    for bad in ("sgg:1111", "xx:11", "umd:1111010", "nation:01", "sgg"):
        with pytest.raises(params.BadParam):
            params.region_items(bad)
    with pytest.raises(params.BadParam):
        params.region_items(",".join(f"sgg:{11110 + i}" for i in range(9)))
    assert queries.shift_ym("202601", -1) == "202512" and queries.shift_ym("202612", 13) == "202801"
    assert queries.provisional_from() == "202609" and queries.confirmed_ym() == "202608"


def test_regions_api(client, seeded):
    sido = client.get("/api/regions?level=sido").get_json()
    assert sido["version"] == "2026-10" and [r["region_cd"] for r in sido["regions"]] == ["11"]
    sgg = client.get("/api/regions?level=sgg&parent=11").get_json()
    assert [r["name"] for r in sgg["regions"]] == ["종로구", "중구"]
    assert client.get("/api/regions?level=nation").get_json()["regions"][0]["region_cd"] == "00"


def test_agg_series(client, seeded):
    resp = client.get("/api/agg?regions=sgg:11110,umd:11110101&from=202601&to=202603")
    data = resp.get_json()
    assert resp.headers["Cache-Control"] == "private, max-age=600"
    assert (data["from"], data["to"], data["provisional_from"]) == ("202601", "202603", "202609")
    s0, s1 = data["series"]
    assert (s0["key"], s0["name"]) == ("sgg:11110", "서울특별시 종로구")
    assert s1["name"] == "서울특별시 종로구 청운동"
    assert [p["ym"] for p in s0["points"]] == ["202601", "202602", "202603"]
    assert s0["points"][0]["n_trades"] == 5


def test_agg_default_range(client, seeded):
    data = client.get("/api/agg?regions=nation:00").get_json()
    assert data["to"] == "202610" and data["from"] == "202111"


def test_agg_series_has_gaps(client, seeded):
    data = client.get("/api/agg?regions=sgg:11140&band=le60&from=202601&to=202602").get_json()
    assert data["series"][0]["points"] == []     # 중구 거래는 84㎡뿐 → 60㎡ 이하 집계 행 없음


@pytest.mark.parametrize("qs", [
    "regions=sgg:1111", "regions=sgg:11110&band=xx", "regions=sgg:11110&from=202605&to=202601",
    "regions=" + ",".join(f"sgg:{11110 + i}" for i in range(9)), "regions=sgg:11110&from=2026",
])
def test_agg_rejects_bad_params(client, seeded, qs):
    resp = client.get(f"/api/agg?{qs}")
    assert resp.status_code == 400 and resp.get_json()["error"]


def test_not_ready_without_active_version(client, pg):
    resp = client.get("/api/agg?regions=nation:00")
    assert resp.status_code == 503 and "경계" in resp.get_json()["error"]


def test_map_sgg(client, seeded):
    data = client.get("/api/map?level=sgg&parent=11&from=202607&to=202609").get_json()
    assert (data["prev_from"], data["prev_to"]) == ("202507", "202509")
    by = {v["region_cd"]: v for v in data["values"]}
    assert by["11110"]["n"] == 15 and by["11140"]["n"] == 6
    assert by["11140"]["yoy_price"] == pytest.approx(
        100 * ((40000 + 50 * 31) - (40000 + 50 * 19)) / (40000 + 50 * 19), abs=0.1)
    assert [p["name"] for p in data["parents"]] == ["서울특별시"]
    assert data["coverage"] is None


def test_map_umd_coverage(client, seeded):
    data = client.get("/api/map?level=umd&parent=11140&from=202607&to=202609").get_json()
    assert data["coverage"] == 0.0                    # 중구 단지 C는 좌표 없음
    assert [v["n"] for v in data["values"]] == [0]
    assert [p["region_cd"] for p in data["parents"]] == ["11", "11140"]
    data = client.get("/api/map?level=umd&parent=11110&from=202607&to=202609").get_json()
    assert data["coverage"] == 100.0


def test_map_rejects_bad_level(client, seeded):
    assert client.get("/api/map?level=dong").status_code == 400
    assert client.get("/api/map?level=umd").status_code == 400    # 읍면동은 시군구 지정 필요


def test_summary(client, seeded):
    data = client.get("/api/summary").get_json()
    assert data["kpi"]["ym"] == "202608" and data["kpi"]["n"] == 7
    assert data["series"][0]["ym"] == "202411" and data["series"][-1]["ym"] == "202610"
    assert [m["region_cd"] for m in data["movers"]["up"]] == ["11110"]   # 중구는 3개월 6건 < 10건
    assert data["movers"]["window"] == ["202606", "202608"]


def test_complex_search_and_detail(client, seeded):
    found = client.get("/api/complexes?q=청운").get_json()
    assert [c["apt_seq"] for c in found] == ["A"] and found[0]["region_name"] == "서울특별시 종로구 청운동"
    by_region = client.get("/api/complexes?region=11140").get_json()
    assert [c["apt_seq"] for c in by_region] == ["C"] and by_region[0]["region_name"] == "서울특별시 중구"
    detail = client.get("/api/complexes/A").get_json()
    assert detail["complex"]["apt_nm"] == "청운아파트" and len(detail["trades"]) == 33 * 3
    t = detail["trades"][0]
    assert set(t) == {"deal_date", "deal_amount", "area", "floor", "apt_dong", "dealing_gbn", "is_cancelled", "ppm2"}
    assert client.get("/api/complexes/ZZZ").status_code == 404
    assert client.get("/api/complexes?region=1").status_code == 400


def test_complex_locations(client, seeded):
    """단지 화면 지도: 검색어·지역 조건에 맞는 좌표 있는 단지(목록과 같은 조건, 최대 3,000개)."""
    r = client.get("/api/complexes/locations?q=청운").get_json()
    assert [(c["apt_seq"], c["apt_nm"], c["lon"], c["lat"]) for c in r["complexes"]] == [("A", "청운아파트", 126.955, 37.575)]
    assert {c["apt_seq"] for c in client.get("/api/complexes/locations?region=11110").get_json()["complexes"]} == {"A", "B"}
    assert client.get("/api/complexes/locations?region=11140").get_json()["complexes"] == []   # C는 좌표 없음
    assert {c["apt_seq"] for c in client.get("/api/complexes/locations").get_json()["complexes"]} == {"A", "B"}
    assert client.get("/api/complexes/locations?region=1").status_code == 400


def test_nearby(client, seeded):
    r = client.get("/api/complexes/A/nearby").get_json()
    assert r["umd_cd"] == "11110101" and r["umd_name"] == "서울특별시 종로구 청운동"
    assert [(c["apt_seq"], c["is_self"]) for c in r["complexes"]] == [("A", True)]
    c = client.get("/api/complexes/C/nearby").get_json()
    assert c["umd_cd"] is None and c["complexes"] == []
    assert client.get("/api/complexes/ZZZ/nearby").status_code == 404

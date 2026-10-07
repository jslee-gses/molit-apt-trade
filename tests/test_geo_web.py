from datetime import datetime

import pytest

import settings
from geo import complexes, hooks


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])


@pytest.fixture
def seeded(pg):
    with pg.connection() as conn:
        conn.execute("""INSERT INTO complexes (apt_seq, apt_nm, api_sgg_cd, geocode_status, geocode_source, lon, lat,
                            region_sgg_cd, region_umd_cd, region_match, boundary_version) VALUES
            ('A', '가단지', '11110', 'ok', 'parcel', 126.95, 37.57, '11110', '11110101', 'within', '2026-10'),
            ('B', '나단지', '11110', 'failed', NULL, NULL, NULL, '11110', '11110102', 'code', '2026-10'),
            ('P', '다단지', '11110', 'pending', NULL, NULL, NULL, NULL, NULL, 'none', '2026-10')""")


def test_set_manual(pg, seeded):
    seen = []
    hooks.ON_REGION_CHANGE.append(lambda conn, seqs: seen.append(seqs))
    with pg.connection() as conn:
        complexes.set_manual(conn, "A", 126.97, 37.58)
        a = conn.execute("SELECT * FROM complexes WHERE apt_seq = 'A'").fetchone()
    assert (a["lon"], a["lat"], a["geocode_status"], a["geocode_source"]) == (126.97, 37.58, "manual", "manual")
    assert a["boundary_version"] is None and a["region_umd_cd"] is None   # 다음 판정에서 다시 정함
    assert seen == [["A"]]                                                 # 바뀌기 전 지역을 알림


@pytest.mark.parametrize("lon, lat", [(37.5, 127.0), (200, 37), ("x", 37), (None, 37),
                                      (float("nan"), 37), (127, float("inf"))])
def test_set_manual_rejects(pg, seeded, lon, lat):
    with pg.connection() as conn, pytest.raises(ValueError):
        complexes.set_manual(conn, "A", lon, lat)


def test_set_manual_unknown(pg, seeded):
    with pg.connection() as conn, pytest.raises(LookupError):
        complexes.set_manual(conn, "ZZZ", 126.97, 37.58)


def test_summary_and_failed(pg, seeded):
    with pg.connection() as conn:
        s = complexes.summary(conn)
        f = complexes.failed(conn)
    assert s["total"] == 3 and s["by_status"] == {"ok": 1, "failed": 1, "pending": 1}
    assert (s["parcel"], s["manual"], s["pending"], s["failed"]) == (1, 0, 1, 1)
    assert (s["by_boundary"], s["by_code"], s["unassigned"]) == (1, 1, 1)
    assert s["located"] == 1 and s["located_pct"] == 33.3
    assert [r["apt_seq"] for r in f] == ["B"] and f[0]["n_trades"] == 0


def test_summary_has_no_address_points(pg, seeded):
    with pg.connection() as conn:
        s = complexes.summary(conn)
        assert "address_points" not in s
        assert conn.execute("SELECT to_regclass('address_points') AS t").fetchone()["t"] is None


def test_manual_coords_api(client, seeded):
    resp = client.post("/api/complexes/B/coords", json={"lon": 126.97, "lat": 37.58})
    assert resp.status_code == 200 and resp.get_json() == {"ok": True}


def test_manual_coords_rejects_bad_values(client, seeded, pg):
    assert client.post("/api/complexes/B/coords", json={"lon": 37.58, "lat": 126.97}).status_code == 400
    assert client.post("/api/complexes/B/coords", json={"lon": "abc", "lat": 37}).status_code == 400
    assert client.post("/api/complexes/B/coords", data="not json",
                       content_type="application/json").status_code == 400
    assert client.post("/api/complexes/ZZZ/coords", json={"lon": 126.97, "lat": 37.58}).status_code == 404
    with pg.connection() as conn:
        assert conn.execute("SELECT geocode_status FROM complexes WHERE apt_seq = 'B'").fetchone() == \
            {"geocode_status": "failed"}


def test_status_page_shows_geo(client, seeded):
    html = client.get("/status").get_data(as_text=True)
    assert "단지 좌표·지역 판정" in html and "나단지" in html
    assert "법정동 코드" in html and "런북" in html and "위치정보요약DB" not in html


def test_failed_ranks_candidates_by_recent_deal_before_counting(pg):
    with pg.connection() as conn:
        conn.execute("DELETE FROM complexes")
        conn.execute("INSERT INTO complexes (apt_seq, api_sgg_cd, geocode_status, last_deal_date) VALUES "
                     "('OLD', '11110', 'failed', '2020-01-01'), ('NEW', '11110', 'failed', '2026-01-01'), "
                     "('NUL', '11110', 'failed', NULL)")
        got = [r["apt_seq"] for r in complexes.failed(conn, limit=2)]
    assert got == ["NEW", "OLD"]

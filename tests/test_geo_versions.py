from datetime import datetime

import pytest

import settings
from geo import assign, hooks, pipeline, versions
from tests.geo_fixtures import make_version


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(versions, "GEO_DATA", tmp_path)
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])
    monkeypatch.setattr(hooks, "BEFORE_ACTIVATE", [])
    monkeypatch.setattr(hooks, "AFTER_ACTIVATE", [])
    monkeypatch.setattr(pipeline, "AFTER_RUN", [])


def test_register_loads_regions(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    with pg.connection() as conn:
        assert versions.register(conn, "2026-10") is True
        assert versions.register(conn, "2026-10") is False
        levels = {r["level"]: r["n"] for r in conn.execute(
            "SELECT level, COUNT(*) AS n FROM regions GROUP BY level")}
        assert versions.active(conn) is None
    assert levels == {"sido": 1, "sgg": 2, "umd": 3}


def test_sync_activates_first_and_switches_to_newer(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    seen = []
    hooks.BEFORE_ACTIVATE.append(lambda conn, v: seen.append(
        (v, conn.execute("SELECT COUNT(*) AS n FROM staged_regions").fetchone()["n"])))
    with pg.connection() as conn:
        conn.execute("INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status) "
                     "VALUES ('A', '11110', 126.955, 37.575, 'ok')")
        assert versions.sync(conn) == "2026-10"
        assert versions.active(conn) == "2026-10"
        assert versions.sync(conn) is None                      # 바뀐 것 없음

        # 새 버전: 청운동 코드가 바뀐 경계
        make_version(tmp_path, "2027-01", {"11110103": (126.95, 37.57, 126.96, 37.58)})
        assert versions.sync(conn) == "2027-01"
        a = conn.execute("SELECT * FROM complexes").fetchone()
        active = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchall()
        old_regions = conn.execute(
            "SELECT COUNT(*) AS n FROM regions WHERE boundary_version = '2026-10'").fetchone()["n"]
    assert seen == [("2026-10", 1), ("2027-01", 1)]
    assert (a["region_umd_cd"], a["boundary_version"]) == ("11110103", "2027-01")
    assert [r["version"] for r in active] == ["2027-01"]
    assert old_regions == 0


def test_switch_failure_keeps_old_version(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    with pg.connection() as conn:
        versions.sync(conn)
        make_version(tmp_path, "2027-01")

        def boom(conn, v):
            raise RuntimeError("집계 실패")

        hooks.BEFORE_ACTIVATE.append(boom)
        with pytest.raises(RuntimeError):
            versions.sync(conn)
        assert versions.active(conn) == "2026-10"


def test_pipeline_run_end_to_end(pg, tmp_path, monkeypatch):
    from collector import store
    from geo import address_points
    from tests.helpers import add_job, item

    make_version(tmp_path, "2026-10")
    ran = []
    pipeline.AFTER_RUN.append(lambda conn: ran.append(True))
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        monkeypatch.setattr(store, "AFTER_SAVE", [])        # 단지는 pipeline의 bootstrap이 만든다
        store.save_job(conn, "11110", "202601", [item(aptSeq="A")], 1)
        address_points.load(conn, [("111104100135|0|1|0", 126.955, 37.575, "가")], "202609")
    pipeline.run()
    with pg.connection() as conn:
        a = conn.execute("SELECT * FROM complexes").fetchone()
    assert (a["geocode_status"], a["region_umd_cd"]) == ("ok", "11110101")
    assert pipeline.state["last_error"] is None
    assert pipeline.state["last_result"]["assigned"] == 1
    assert ran == [True]

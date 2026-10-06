from datetime import datetime

import pytest

import settings
from geo import assign, hooks, pipeline, versions
from tests.geo_fixtures import make_version


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])
    monkeypatch.setattr(hooks, "BEFORE_ACTIVATE", [])
    monkeypatch.setattr(hooks, "AFTER_ACTIVATE", [])
    monkeypatch.setattr(pipeline, "AFTER_RUN", [])
    monkeypatch.setattr(pipeline, "_failed", None)


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
    from geo import complexes
    from tests.helpers import add_job, item

    make_version(tmp_path, "2026-10")
    ran = []
    pipeline.AFTER_RUN.append(lambda conn: ran.append(True))
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        monkeypatch.setattr(store, "AFTER_SAVE", [])        # 단지는 pipeline의 bootstrap이 만든다
        store.save_job(conn, "11110", "202601", [item(aptSeq="A")], 1)
    pipeline.run()                                          # 단지 생성
    with pg.connection() as conn:
        complexes.set_manual(conn, "A", 126.955, 37.575)
    pipeline.run()                                          # 좌표로 판정
    with pg.connection() as conn:
        a = conn.execute("SELECT * FROM complexes").fetchone()
    assert (a["geocode_status"], a["region_umd_cd"], a["region_match"]) == ("manual", "11110101", "within")
    assert pipeline.state["last_error"] is None
    assert pipeline.state["last_result"]["assigned"] == 1
    assert "located" not in pipeline.state["last_result"]
    assert ran == [True, True]


def test_switch_skips_complex_changed_after_staging(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    with pg.connection() as conn:
        conn.execute("INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status) "
                     "VALUES ('A', '11110', 126.955, 37.575, 'ok')")
        versions.sync(conn)
        make_version(tmp_path, "2027-01")
        hooks.BEFORE_ACTIVATE.append(lambda conn, v: conn.execute(
            "UPDATE complexes SET lon = 126.965 WHERE apt_seq = 'A'"))
        versions.sync(conn)
        a = conn.execute("SELECT * FROM complexes").fetchone()
        assert a["boundary_version"] == "2026-10"
        assert assign.assign_pending(conn, "2027-01") == 1
        a = conn.execute("SELECT * FROM complexes").fetchone()
    assert (a["boundary_version"], a["region_umd_cd"]) == ("2027-01", "11110102")


def test_switch_unregistered_version_rolls_back(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    with pg.connection() as conn:
        versions.sync(conn)
        make_version(tmp_path, "2027-01")           # 등록하지 않고 바로 전환
        with pytest.raises(ValueError):
            versions.switch(conn, "2027-01")
        assert versions.active(conn) == "2026-10"


def test_available_ignores_non_version_folders(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    make_version(tmp_path, "backup")
    assert versions.available() == ["2026-10"]


def test_pipeline_sync_failure_does_not_block_and_backs_off(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    calls = []
    with pg.connection() as conn:
        versions.sync(conn)
        make_version(tmp_path, "2027-01")
        conn.execute("INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status) "
                     "VALUES ('A', '11110', 126.955, 37.575, 'ok')")

    def boom(conn, v):
        calls.append(v)
        raise RuntimeError("집계 실패")

    hooks.BEFORE_ACTIVATE.append(boom)
    pipeline.run()
    with pg.connection() as conn:
        a = conn.execute("SELECT * FROM complexes").fetchone()
        assert versions.active(conn) == "2026-10"
    assert (a["boundary_version"], a["region_umd_cd"]) == ("2026-10", "11110101")
    assert "집계 실패" in pipeline.state["sync_error"]
    assert pipeline.state["last_attempt"] is not None
    pipeline.run()                                   # 1시간 안: 다시 시도하지 않는다
    assert calls == ["2027-01"]

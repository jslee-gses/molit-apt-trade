"""단지 쓰기 직렬화(어드바이저리 락) 테스트."""
from datetime import datetime

import psycopg
import pytest

import settings
from collector import api
from geo import assign, complexes, hooks, pipeline, versions
from tests.geo_fixtures import make_version
from tests.helpers import item


@pytest.fixture(autouse=True)
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    for name in ("ON_REGION_CHANGE", "BEFORE_ACTIVATE", "AFTER_ACTIVATE"):
        monkeypatch.setattr(hooks, name, [])
    monkeypatch.setattr(pipeline, "AFTER_RUN", [])
    monkeypatch.setattr(pipeline, "_failed", None)


@pytest.fixture
def other(pg):
    """두 번째 연결(autocommit)."""
    with psycopg.connect(settings.env("TEST_DATABASE_URL"), autocommit=True) as c:
        yield c


def _try_lock(c):
    """다른 연결이 락을 잡고 있는지 보기 위한 시도(트랜잭션 안에서 시도하고 바로 끝낸다)."""
    with c.transaction():
        return c.execute("SELECT pg_try_advisory_xact_lock(%s)", (complexes.COMPLEXES_LOCK,)).fetchone()[0]


def test_lock_complexes_blocks_other_connection(pg, other):
    with pg.connection() as a:
        with a.transaction():
            complexes.lock_complexes(a)
            assert _try_lock(other) is False
        assert _try_lock(other) is True


def _seed(conn):
    conn.execute("INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status) "
                 "VALUES ('A', '11110', 126.955, 37.575, 'ok')")


def _result():
    return {"apt_seq": "A", "umd": "11110101", "sgg": "11110", "match": "within",
            "version": "v", "mismatch": False, "lon": 126.955, "lat": 37.575}


def test_register_takes_lock(pg, other):
    rows = api.to_rows([item(aptSeq="A")], "11110", "202601", None)
    with pg.connection() as a, a.transaction():
        complexes.register(a, "11110", "202601", rows)
        assert _try_lock(other) is False


def test_register_without_complexes_skips_lock(pg, other):
    with pg.connection() as a, a.transaction():
        complexes.register(a, "11110", "202601", [])
        assert _try_lock(other) is True


def test_apply_results_takes_lock(pg, other):
    with pg.connection() as a:
        _seed(a)
        with a.transaction():
            assign.apply_results(a, [_result()])
            assert _try_lock(other) is False
        assert _try_lock(other) is True


def test_set_manual_takes_lock(pg, other):
    with pg.connection() as a:
        _seed(a)
        with a.transaction():
            complexes.set_manual(a, "A", 126.97, 37.58)
            assert _try_lock(other) is False


def test_switch_takes_lock(pg, other, tmp_path):
    make_version(tmp_path, "2026-10")
    with pg.connection() as a:
        versions.register(a, "2026-10")
        with a.transaction():
            versions.switch(a, "2026-10")
            assert _try_lock(other) is False


def test_bootstrap_takes_lock(pg, other):
    with pg.connection() as a:
        with a.transaction():
            complexes.bootstrap(a)
            assert _try_lock(other) is False
        assert _try_lock(other) is True


def test_pipeline_bootstrap_failure_isolated(pg, tmp_path, monkeypatch):
    ran = []
    pipeline.AFTER_RUN.append(lambda conn: ran.append(True))

    def boom(conn):
        raise RuntimeError("bootstrap 실패")
    monkeypatch.setattr(complexes, "bootstrap", boom)
    pipeline.state["bootstrap_error"] = None
    pipeline.run()
    assert "bootstrap 실패" in pipeline.state["bootstrap_error"]
    assert pipeline.state["last_error"] is None
    assert ran == [True]
    assert pipeline.state["last_result"]["added"] == 0


def test_pipeline_assign_failure_isolated(pg, tmp_path, monkeypatch):
    make_version(tmp_path, "2026-10")          # 활성 버전이 있어야 판정 단계가 실행된다
    ran = []
    pipeline.AFTER_RUN.append(lambda conn: ran.append(True))

    def boom(conn, version, boundary=None):
        raise RuntimeError("판정 실패")
    monkeypatch.setattr(assign, "assign_pending", boom)
    pipeline.state["assign_error"] = None
    pipeline.run()
    assert "판정 실패" in pipeline.state["assign_error"]
    assert pipeline.state["last_error"] is None
    assert ran == [True]
    assert pipeline.state["last_result"]["assigned"] == 0

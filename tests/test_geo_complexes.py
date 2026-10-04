from datetime import date, datetime

import pytest

import settings
from collector import store
from geo import complexes, keys
from tests.helpers import add_job, item


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [complexes.register])


@pytest.mark.parametrize("args, expected", [
    (("11110", "4100135", "0", "00012", "00000"), "111104100135|0|12|0"),
    (("11110", "4100135", "", "12", ""), "111104100135|0|12|0"),
    ((" 11110", "4100135 ", "1", "7", "3"), "111104100135|1|7|3"),
    (("11110", "4100135", "0", "", "0"), None),
    (("1111", "4100135", "0", "1", "0"), None),
    (("11110", "", "0", "1", "0"), None),
    ((None, None, None, None, None), None),
    (("11110", "4100135", "0", "9999999999999999999", "0"), None),
    (("１１１１０", "4100135", "0", "1", "0"), None),
    (("11110	", "4100135", "0", "1", "0"), None),
    (("11110", "4100135", "0", "000000012", "0"), "111104100135|0|12|0"),
    (("11110", "4100135", "0", "1", "00a"), "111104100135|0|1|0"),
    (("11110", "4100135", "0", "1", "0012"), "111104100135|0|1|12"),
    (("11110", "4100135", "0", "  ", "0"), None),
])
def test_road_key_python(args, expected):
    assert keys.road_key(*args) == expected


def test_road_key_sql_matches_python(pg):
    cases = [("11110", "4100135", "0", "00012", "00000"), ("11110", "4100135", "", "12", ""),
             (" 11110", "4100135 ", "1", "7", "3"), ("11110", "4100135", "0", "", "0"),
             ("11110", "4100135", "0", "1a", "0"), (None, None, None, None, None),
             ("11110", "4100135", "0", "9999999999999999999", "0"), ("１１１１０", "4100135", "0", "1", "0"),
             ("11110	", "4100135", "0", "1", "0"), ("11110", "4100135", "0", "000000012", "0"),
             ("11110", "4100135", "0", "1", "00a"), ("11110", "4100135", "0", "1", "0012"),
             ("11110", "4100135", "0", "  ", "0"), ("11110", "4100135", "0", "1", "9999999999999")]
    with pg.connection() as conn:
        for c in cases:
            got = conn.execute("SELECT road_key(%s, %s, %s, %s, %s) AS k", c).fetchone()["k"]
            assert got == keys.road_key(*c), c


def test_register_upserts_latest_info(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        add_job(conn, "11110", "202602")
        store.save_job(conn, "11110", "202602", [item(aptNm="새이름", dealMonth="2")], 1)
        store.save_job(conn, "11110", "202601", [item(aptNm="옛이름", dealMonth="1"), item(aptSeq=" ")], 2)
        rows = conn.execute("SELECT * FROM complexes").fetchall()
    assert len(rows) == 1                       # 빈 aptSeq는 등록하지 않음
    c = rows[0]
    assert c["apt_nm"] == "새이름"              # 더 오래된 거래는 정보를 덮지 않음
    assert c["last_deal_date"] == date(2026, 2, 5)
    assert c["api_sgg_cd"] == "11110" and c["api_umd_cd"] == "10100" and c["api_umd_nm"] == "청운동"
    assert c["geocode_status"] == "pending"


def test_register_retries_failed_on_newer_trade(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        store.save_job(conn, "11110", "202601", [item()], 1)
        conn.execute("UPDATE complexes SET geocode_status = 'failed'")
        store.save_job(conn, "11110", "202601", [item(dealDay="20")], 1)
        status = conn.execute("SELECT geocode_status FROM complexes").fetchone()["geocode_status"]
    assert status == "pending"


def test_bootstrap_from_existing_trades(pg, monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        store.save_job(conn, "11110", "202601", [item(), item(aptSeq="11110-2", aptNm="둘")], 2)
        assert complexes.bootstrap(conn) == 2
        assert complexes.bootstrap(conn) == 0     # 이미 있으면 하지 않음
        names = {r["apt_nm"] for r in conn.execute("SELECT apt_nm FROM complexes")}
    assert names == {"테스트아파트", "둘"}


def test_bootstrap_after_register_inserted_rows(pg, monkeypatch):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        monkeypatch.setattr(store, "AFTER_SAVE", [complexes.register])
        store.save_job(conn, "11110", "202601", [item()], 1)       # register가 1건 먼저 등록
        monkeypatch.setattr(store, "AFTER_SAVE", [])
        store.save_job(conn, "11110", "202601", [item(aptSeq="11110-2", aptNm="둘")], 1)
        assert complexes.bootstrap(conn) >= 1
        assert complexes.bootstrap(conn) == 0
        names = {r["apt_nm"] for r in conn.execute("SELECT apt_nm FROM complexes")}
    assert names == {"테스트아파트", "둘"}


def test_wire_registers_hook(monkeypatch):
    import wiring
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    wiring.wire()
    assert complexes.register in store.AFTER_SAVE

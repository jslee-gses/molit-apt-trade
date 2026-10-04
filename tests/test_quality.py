from datetime import datetime

import pytest

import settings
from collector import jobs, quality, store
from tests.helpers import add_job, item


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    jobs.reset_state()


def seed(conn):
    add_job(conn, "11110", "202509")
    add_job(conn, "11110", "202610")
    add_job(conn, "11140", "202610")
    add_job(conn, "11170", "202609", status="done", stored_count=0)
    add_job(conn, "11170", "202610", status="done", stored_count=0)
    store.save_job(conn, "11110", "202509", [item(dealYear="2025", dealMonth="9")], 1)
    store.save_job(conn, "11110", "202610", [item(dealMonth="10", aptNm=""), item(dealMonth="10")], 2)
    store.save_job(conn, "11140", "202610", [item(sggCd="11140", dealMonth="10")], 5)  # incomplete


def test_progress(pg):
    with pg.connection() as conn:
        seed(conn)
    p = quality.progress()
    assert p["by_status"] == {"done": 4, "incomplete": 1}
    assert p["trades"] == 4
    assert p["total_jobs"] == 5
    assert [y["year"] for y in p["by_year"]] == ["2025", "2026"]
    assert p["by_year"][1]["jobs"] == 4 and p["by_year"][1]["bad"] == 1
    assert p["last_fetched"] == datetime(2026, 10, 3, 7, 0)
    assert p["storage"]["total_bytes"] > 0 and p["storage"]["bytes_per_trade"] > 0
    assert p["eta_days"] == 0


def test_jobs_progress_includes_state(pg):
    with pg.connection() as conn:
        seed(conn)
    jobs.state["last_error"] = "boom"
    p = jobs.progress()
    assert p["last_error"] == "boom" and p["trades"] == 4


def test_quality_report(pg):
    with pg.connection() as conn:
        seed(conn)
    q = quality.quality_report()
    assert [r["lawd_cd"] for r in q["problems"]] == ["11140"]
    assert q["problems"][0]["name"] == "서울특별시 중구"
    assert [r["lawd_cd"] for r in q["zero_codes"]] == ["11170"]
    assert q["sgg_mismatch"] == []
    blanks = {b["field"]: b["missing"] for b in q["blanks"]}
    assert blanks["aptNm"] == 1 and blanks["dealAmount"] == 0
    assert q["total"] == 3                        # done 작업의 저장 건수 합
    assert q["changes"] == []


def test_backfill_quality_fills_missing(pg):
    with pg.connection() as conn:
        seed(conn)
        conn.execute("UPDATE jobs SET q_blank = NULL, q_dup = NULL")
        n = quality.backfill_quality(conn)
        job = conn.execute("SELECT q_blank FROM jobs WHERE lawd_cd = '11110' AND deal_ymd = '202610'").fetchone()
    assert n == 4
    assert job["q_blank"] == {"apt_nm": 1}

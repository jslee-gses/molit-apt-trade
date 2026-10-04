from datetime import datetime, timedelta

import pytest

import settings
from collector import store, usage
from tests.helpers import add_job, item

NOW = datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST)


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: NOW)


@pytest.fixture(autouse=True)
def no_hooks(monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [])


def test_usage_counts_per_day(pg):
    with pg.connection() as conn:
        assert usage.calls_today(conn) == 0
        usage.count_call(conn)
        usage.count_call(conn)
        assert usage.calls_today(conn) == 2
        assert usage.quota_left(conn) == settings.DAILY_LIMIT - 2


def test_ensure_jobs_idempotent(pg):
    with pg.connection() as conn:
        store.ensure_jobs(conn, ["11110", "11140"], ["202601", "202602"])
        store.ensure_jobs(conn, ["11110", "11140"], ["202601", "202602"])
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'pending'").fetchone()["n"]
    assert n == 4


def test_save_job_inserts_and_marks_done(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        status, n = store.save_job(conn, "11110", "202601", [item(), item(aptSeq="11110-2")], 2)
        job = conn.execute("SELECT * FROM jobs").fetchone()
        rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
    assert (status, n) == ("done", 2)
    assert job["status"] == "done" and job["total_count"] == 2 and job["stored_count"] == 2
    assert job["attempts"] == 1 and job["error"] is None and job["next_try_at"] is None
    assert job["fetched_at"] == datetime(2026, 10, 3, 7, 0)
    assert rows[0]["deal_amount"] == 84000 and rows[0]["apt_seq"] == "11110-1"
    assert rows[0]["collected_at"] == datetime(2026, 10, 3, 7, 0)


def test_save_job_replaces_and_records_change(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        store.save_job(conn, "11110", "202601", [item(), item()], 2)
        store.save_job(conn, "11110", "202601", [item()], 1)
        n = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        change = conn.execute('SELECT lawd_cd, deal_ymd, "before", "after" FROM changes').fetchone()
    assert n == 1
    assert change == {"lawd_cd": "11110", "deal_ymd": "202601", "before": 2, "after": 1}


def test_save_job_incomplete_schedules_retry(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        status, _ = store.save_job(conn, "11110", "202601", [item()], 3)
        job = conn.execute("SELECT * FROM jobs").fetchone()
    assert status == "incomplete"
    assert job["error"] == "저장 1건 / 전체 3건"
    assert job["next_try_at"] == datetime(2026, 10, 3, 8, 0)


def test_save_job_quality_counts(pg):
    items = [
        item(),
        item(),                                    # 완전 중복
        item(dealMonth="13", aptNm=""),            # 날짜 불량 + 단지명 빈 값
        item(sggCd="99999", cdealType="O"),        # 코드 불일치 + 해제
    ]
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        store.save_job(conn, "11110", "202601", items, 4)
        job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["q_dup"] == 1
    assert job["q_ymd_bad"] == 1
    assert job["q_sgg_bad"] == 1
    assert job["q_cancelled"] == 1
    assert job["q_blank"] == {"apt_nm": 1, "deal_date": 1}


def test_after_save_hook_runs_in_same_transaction(pg, monkeypatch):
    seen = []

    def hook(conn, lawd_cd, deal_ymd, rows):
        seen.append((lawd_cd, deal_ymd, len(rows)))
        raise RuntimeError("후처리 실패")

    monkeypatch.setattr(store, "AFTER_SAVE", [hook])
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        with pytest.raises(RuntimeError):
            store.save_job(conn, "11110", "202601", [item()], 1)
        n = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        status = conn.execute("SELECT status FROM jobs").fetchone()["status"]
    assert seen == [("11110", "202601", 1)]
    assert n == 0 and status == "pending"


def test_mark_error_backoff_keeps_done(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601", attempts=2)
        add_job(conn, "11140", "202601", status="done", attempts=0)
        store.mark_error(conn, "11110", "202601", "HTTP 500")
        store.mark_error(conn, "11140", "202601", "HTTP 500")
        a, b = conn.execute("SELECT * FROM jobs ORDER BY lawd_cd").fetchall()
    assert a["status"] == "error" and a["attempts"] == 3 and a["error"] == "HTTP 500"
    assert a["next_try_at"] == datetime(2026, 10, 3, 7, 0) + timedelta(minutes=8)
    assert b["status"] == "done" and b["attempts"] == 1

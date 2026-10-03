from datetime import datetime

import pytest

import settings
from collector import api, codes, jobs, store
from tests.helpers import add_job, item

AT_7AM = datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: AT_7AM)
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    jobs.reset_state()
    yield
    jobs.reset_state()


def ts(s):
    return datetime.fromisoformat(s)


def test_next_jobs_priority_order(pg):
    # 기준: 2026-10-03 07:00, 최근 3개월 = 202608~, 재확인 구간 = 202508~202607
    with pg.connection() as conn:
        add_job(conn, "11110", "202610")                                                    # 1 최근 미수집
        add_job(conn, "11110", "202609", status="done", fetched_at=ts("2026-10-02 06:10"),
                checked_at=ts("2026-10-02 06:10"))                                          # 2 오늘 재수집
        add_job(conn, "11110", "202001", status="error", next_try_at=ts("2026-10-03 06:59"))  # 3 재시도
        add_job(conn, "11110", "200601")                                                    # 4 과거 미수집
        add_job(conn, "11110", "202508", status="done", fetched_at=ts("2026-09-01 06:00"),
                checked_at=ts("2026-09-01 06:00"))                                          # 5 1년 내 재확인
        add_job(conn, "11110", "201001", status="done", fetched_at=ts("2026-03-01 06:00"),
                checked_at=ts("2026-03-01 06:00"))                                          # 6 오래된 달 재확인
        add_job(conn, "11140", "202609", status="done", fetched_at=ts("2026-10-03 06:30"),
                checked_at=ts("2026-10-03 06:30"))                                          # 오늘 이미 받음 → 제외
        add_job(conn, "11140", "202001", status="error", next_try_at=ts("2026-10-03 08:00"))  # 아직 대기 → 제외
        picked = jobs.next_jobs(conn, 10)
        no_backfill = jobs.next_jobs(conn, 10, allow_backfill=False)
    assert [(j["deal_ymd"], j["mode"], j["pri"]) for j in picked] == [
        ("202610", "fetch", 1), ("202609", "fetch", 2), ("202001", "fetch", 3),
        ("200601", "fetch", 4), ("202508", "check", 5), ("201001", "check", 6),
    ]
    assert "200601" not in [j["deal_ymd"] for j in no_backfill]


def test_daily_start_and_last_refresh(monkeypatch):
    assert jobs.daily_start() == datetime(2026, 10, 3, 6, 0, tzinfo=settings.KST)
    assert jobs.last_refresh_time() == datetime(2026, 10, 3, 6, 0, tzinfo=settings.KST)
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 5, 0, tzinfo=settings.KST))
    assert jobs.last_refresh_time() == datetime(2026, 10, 2, 6, 0, tzinfo=settings.KST)


def fake_fetch_page(pages_by_job):
    """(lawd_cd, deal_ymd) -> 항목 목록. 한 페이지에 모두 돌려준다."""
    def fetch_page(session, key, lawd_cd, deal_ymd, page_no, num_rows=1000, on_call=None):
        if on_call:
            on_call()
        items = pages_by_job.get((lawd_cd, deal_ymd), [])
        return (items[:num_rows], len(items))
    return fetch_page


def test_check_job_marks_changed_month_pending(pg, monkeypatch):
    monkeypatch.setattr(api, "fetch_page", fake_fetch_page({("11110", "202001"): [item(), item()]}))
    with pg.connection() as conn:
        add_job(conn, "11110", "202001", status="done", stored_count=1, checked_at=ts("2026-01-01 00:00"))
        add_job(conn, "11140", "202001", status="done", stored_count=0, checked_at=ts("2026-01-01 00:00"))
        jobs.check_job(None, "KEY", conn, "11110", "202001")
        jobs.check_job(None, "KEY", conn, "11140", "202001")
        a, b = conn.execute("SELECT * FROM jobs ORDER BY lawd_cd").fetchall()
    assert a["status"] == "pending" and a["error"] == "재확인 시 건수 변동 1→2"
    assert b["status"] == "done" and b["checked_at"] == datetime(2026, 10, 3, 7, 0)


def test_run_batch_waits_before_daily_start(pg, monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 5, 0, tzinfo=settings.KST))
    jobs.run_batch(max_jobs=3)
    with pg.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_run_batch_collects_recent_first(pg, monkeypatch):
    monkeypatch.setattr(settings, "START_YMD", "202609")
    monkeypatch.setattr(api, "fetch_page", fake_fetch_page({("11110", "202609"): [item(dealMonth="9")]}))
    jobs.run_batch(max_jobs=3)
    with pg.connection() as conn:
        done = conn.execute("SELECT lawd_cd, deal_ymd FROM jobs WHERE status = 'done' "
                            "ORDER BY deal_ymd, lawd_cd").fetchall()
        n_jobs = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        trades = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        calls = conn.execute("SELECT calls FROM api_usage").fetchone()["calls"]
    n_codes = len(codes.load_codes())
    assert n_jobs == 2 * n_codes                     # 202609, 202610 x 전 시군구
    assert len(done) == 3 and done[0]["deal_ymd"] == "202609"
    assert trades == 1 and calls == 3
    assert jobs.state["running"] is False


def test_run_batch_pauses_when_quota_runs_out(pg, monkeypatch):
    monkeypatch.setattr(settings, "START_YMD", "202610")
    monkeypatch.setattr(settings, "DAILY_LIMIT", 2)
    # 첫 작업은 2페이지(호출 2번)로 끝나고, 두 번째 작업의 첫 호출에서 한도에 걸린다
    many = [item(aptSeq=f"s{i}") for i in range(3)]

    def fetch_page(session, key, lawd_cd, deal_ymd, page_no, num_rows=1000, on_call=None):
        if on_call:
            on_call()
        items = many if lawd_cd == "11110" else [item()]
        chunk = items[(page_no - 1) * 2: page_no * 2]
        return chunk, len(items)

    monkeypatch.setattr(api, "fetch_page", fetch_page)
    jobs.run_batch(max_jobs=5)
    with pg.connection() as conn:
        done = conn.execute("SELECT lawd_cd FROM jobs WHERE status = 'done'").fetchall()
        trades = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
    assert [d["lawd_cd"] for d in done] == ["11110"]
    assert trades == 3                                   # 두 번째 작업은 부분 저장되지 않음
    assert jobs.state["paused_until"] == "2026-10-04 06:00:00"


def test_run_batch_records_api_error(pg, monkeypatch):
    monkeypatch.setattr(settings, "START_YMD", "202610")

    def fetch_page(*a, on_call=None, **k):
        if on_call:
            on_call()
        raise api.ApiError("HTTP 500")

    monkeypatch.setattr(api, "fetch_page", fetch_page)
    jobs.run_batch(max_jobs=1)
    with pg.connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE status = 'error'").fetchone()
    assert job["error"] == "HTTP 500" and job["attempts"] == 1
    assert "HTTP 500" in jobs.state["last_error"]


def test_storage_pct_uses_db_size(pg, monkeypatch):
    from collector import quality
    monkeypatch.setattr(settings, "DB_LIMIT_MB", 1)
    assert quality.storage_pct() > 0

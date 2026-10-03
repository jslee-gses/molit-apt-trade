"""무엇을 언제 받을지 고르고, 스케줄러가 부르는 수집 배치를 돌린다."""
import logging
import threading
from datetime import timedelta

import requests

import db
import settings
from collector import api, codes, quality, store, usage

log = logging.getLogger(__name__)

_lock = threading.Lock()
state = {}
_ensured = {}


def reset_state():
    state.clear()
    state.update(running=False, current=None, last_error=None, paused_until=None, backfill_stopped=None)
    _ensured.clear()
    _ensured.update(month=None, quality=False)


reset_state()


def ensure_jobs():
    """시작월~이번 달 x 전 시군구 작업이 없으면 만든다(달이 바뀌면 자동 추가)."""
    this_month = settings.now_kst().strftime("%Y%m")
    if _ensured["month"] == this_month:
        return
    months = codes.month_range(settings.START_YMD, this_month)
    with db.connection() as conn:
        store.ensure_jobs(conn, codes.load_codes()["LAWD_CD"].tolist(), months)
    _ensured["month"] = this_month


def daily_start(day_offset=0):
    """그날의 수집 시작 시각(REFRESH_AT, KST)."""
    hour, minute = map(int, settings.REFRESH_AT.split(":"))
    at = settings.now_kst().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at + timedelta(days=day_offset)


def last_refresh_time():
    """가장 최근에 지난 매일 갱신 시각. 이 시각 전에 받은 최근 달은 다시 받는다."""
    at = daily_start()
    return at if settings.now_kst() >= at else at - timedelta(days=1)


def _naive(dt):
    return dt.replace(tzinfo=None, microsecond=0)


def next_jobs(conn, limit, allow_backfill=True):
    """우선순위
    ① 최근 N개월 미수집(새 달 포함)  ② 최근 N개월 매일 재수집  ③ 오류·불일치 재시도
    ④ 과거 자료 미수집(오래된 달부터, 용량 여유가 있을 때만)
    ⑤ 지난 1년 건수 재확인(RECHECK_DAYS 주기)  ⑥ 그보다 오래된 달 건수 재확인(OLD_RECHECK_DAYS 주기)
    """
    now = settings.now_kst()
    params = dict(
        now=_naive(now),
        recent=codes.months_ago(settings.REFRESH_MONTHS - 1),
        check_from=codes.months_ago(settings.REFRESH_MONTHS - 1 + settings.RECHECK_MONTHS),
        refresh=_naive(last_refresh_time()),
        recheck=_naive(now - timedelta(days=settings.RECHECK_DAYS)),
        old_recheck=_naive(now - timedelta(days=settings.OLD_RECHECK_DAYS)),
        backfill=allow_backfill,
        limit=limit,
    )
    sql = """
    SELECT lawd_cd, deal_ymd, 'fetch' AS mode, 1 AS pri FROM jobs
     WHERE status = 'pending' AND deal_ymd >= %(recent)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 2 FROM jobs
     WHERE status = 'done' AND deal_ymd >= %(recent)s AND fetched_at < %(refresh)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 3 FROM jobs
     WHERE status IN ('error', 'incomplete') AND (next_try_at IS NULL OR next_try_at <= %(now)s)
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 4 FROM jobs
     WHERE status = 'pending' AND deal_ymd < %(recent)s AND %(backfill)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'check', 5 FROM jobs
     WHERE status = 'done' AND deal_ymd < %(recent)s AND deal_ymd >= %(check_from)s
       AND checked_at <= %(recheck)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'check', 6 FROM jobs
     WHERE status = 'done' AND deal_ymd < %(check_from)s AND checked_at <= %(old_recheck)s
    ORDER BY pri, deal_ymd, lawd_cd LIMIT %(limit)s
    """
    return conn.execute(sql, params).fetchall()


def check_job(session, key, conn, lawd_cd, deal_ymd, on_call=None):
    """누락 점검: 지난 달은 1건만 요청해 전체 건수만 비교, 달라졌으면 다시 받도록 표시."""
    _, total = api.fetch_page(session, key, lawd_cd, deal_ymd, 1, num_rows=1, on_call=on_call)
    row = conn.execute("SELECT stored_count FROM jobs WHERE lawd_cd = %s AND deal_ymd = %s",
                       (lawd_cd, deal_ymd)).fetchone()
    if row["stored_count"] != total:
        conn.execute("UPDATE jobs SET status = 'pending', error = %s WHERE lawd_cd = %s AND deal_ymd = %s",
                     (f"재확인 시 건수 변동 {row['stored_count']}→{total}", lawd_cd, deal_ymd))
    else:
        conn.execute("UPDATE jobs SET checked_at = %s WHERE lawd_cd = %s AND deal_ymd = %s",
                     (settings.now_ts(), lawd_cd, deal_ymd))


def run_batch(max_jobs=20):
    """스케줄러가 1분마다 호출. 매일 REFRESH_AT부터 하루 한도까지 수집하고, 0시~REFRESH_AT에는 쉰다."""
    if not _lock.acquire(blocking=False):
        return
    state["running"] = True
    try:
        if state["paused_until"] and settings.now_str() < state["paused_until"]:
            return
        state["paused_until"] = None
        if settings.now_kst() < daily_start():
            return  # 오늘 수집 시작 전
        key = settings.service_key()
        ensure_jobs()
        if not _ensured["quality"]:
            with db.connection() as conn:
                quality.backfill_quality(conn)
            _ensured["quality"] = True

        pct = quality.storage_pct()
        allow_backfill = pct < settings.STORAGE_STOP_PCT
        state["backfill_stopped"] = None if allow_backfill else (
            f"DB 사용률 {pct:.1f}% ≥ {settings.STORAGE_STOP_PCT:g}% → 과거 자료 수집 중단(최근 자료는 계속)")

        with db.connection() as conn, requests.Session() as session:
            def on_call():
                if usage.quota_left(conn) <= 0:
                    raise api.QuotaExceeded("오늘 호출 상한 도달")
                usage.count_call(conn)

            for job in next_jobs(conn, max_jobs, allow_backfill):
                lawd_cd, deal_ymd, mode = job["lawd_cd"], job["deal_ymd"], job["mode"]
                state["current"] = f"{deal_ymd} {lawd_cd} ({mode})"
                try:
                    if mode == "check":
                        check_job(session, key, conn, lawd_cd, deal_ymd, on_call=on_call)
                    else:
                        items, total = api.fetch_job(session, key, lawd_cd, deal_ymd, on_call=on_call)
                        status, n = store.save_job(conn, lawd_cd, deal_ymd, items, total)
                        log.info("%s %s: %d건 (%s)", deal_ymd, lawd_cd, n, status)
                except api.ApiError as e:
                    log.warning("%s %s 실패: %s", deal_ymd, lawd_cd, e)
                    store.mark_error(conn, lawd_cd, deal_ymd, str(e))
                    state["last_error"] = f"{settings.now_str()} {deal_ymd} {lawd_cd}: {e}"
    except api.QuotaExceeded as e:
        # 다음 날 수집 시작 시각까지 쉰다
        resume = daily_start(1).strftime("%Y-%m-%d %H:%M:%S")
        state["paused_until"] = resume
        log.info("%s → %s까지 대기", e, resume)
    except Exception as e:  # noqa: BLE001
        state["last_error"] = f"{settings.now_str()} {type(e).__name__}: {e}"
        log.exception("수집 배치 실패")
    finally:
        state["running"] = False
        state["current"] = None
        _lock.release()

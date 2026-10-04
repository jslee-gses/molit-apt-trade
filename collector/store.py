"""수집 결과 저장: 작업 생성, 작업 단위 교체 저장, 오류 기록."""
from datetime import timedelta

import settings
from collector import api, quality

TRADE_COLS = ["lawd_cd", "deal_ymd", *api.COLUMNS, "deal_date", "collected_at"]

# 작업 하나를 저장한 같은 트랜잭션 안에서 불리는 후처리(단지 등록·집계 대기열 등).
# 함수 형태: hook(conn, lawd_cd, deal_ymd, rows). 예외를 내면 저장 전체가 취소된다.
AFTER_SAVE = []


def ensure_jobs(conn, codes, months):
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO jobs(lawd_cd, deal_ymd) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(c, ym) for ym in months for c in codes],
        )


def save_job(conn, lawd_cd, deal_ymd, items, total):
    """작업 하나를 통째로 교체 저장한다. 삭제·삽입·작업 상태 갱신·후처리가 한 트랜잭션이다."""
    stored_at = settings.now_ts()
    rows = api.to_rows(items, lawd_cd, deal_ymd, stored_at)
    q = quality.job_quality(rows, lawd_cd, deal_ymd)
    # 누락 점검 1: API가 알려준 전체 건수와 실제 저장 건수 비교
    status = "done" if len(rows) == total else "incomplete"
    with conn.transaction():
        prev = conn.execute(
            "SELECT stored_count FROM jobs WHERE lawd_cd = %s AND deal_ymd = %s FOR UPDATE",
            (lawd_cd, deal_ymd),
        ).fetchone()
        conn.execute("DELETE FROM trades WHERE lawd_cd = %s AND deal_ymd = %s", (lawd_cd, deal_ymd))
        with conn.cursor() as cur, cur.copy(f"COPY trades ({', '.join(TRADE_COLS)}) FROM STDIN") as copy:
            for r in rows:
                copy.write_row([r[c] for c in TRADE_COLS])
        conn.execute(
            "UPDATE jobs SET status = %(status)s, total_count = %(total)s, stored_count = %(n)s, "
            "fetched_at = %(at)s, checked_at = %(at)s, attempts = attempts + 1, "
            "error = %(error)s, next_try_at = %(next)s "
            "WHERE lawd_cd = %(lawd_cd)s AND deal_ymd = %(deal_ymd)s",
            dict(status=status, total=total, n=len(rows), at=stored_at,
                 error=None if status == "done" else f"저장 {len(rows)}건 / 전체 {total}건",
                 next=None if status == "done" else stored_at + timedelta(hours=1),
                 lawd_cd=lawd_cd, deal_ymd=deal_ymd),
        )
        quality.write_quality(conn, lawd_cd, deal_ymd, q)
        if prev and prev["stored_count"] is not None and prev["stored_count"] != len(rows):
            conn.execute(
                'INSERT INTO changes(at, lawd_cd, deal_ymd, "before", "after") VALUES (%s, %s, %s, %s, %s)',
                (stored_at, lawd_cd, deal_ymd, prev["stored_count"], len(rows)),
            )
        for hook in AFTER_SAVE:
            hook(conn, lawd_cd, deal_ymd, rows)
    return status, len(rows)


def mark_error(conn, lawd_cd, deal_ymd, msg):
    with conn.transaction():
        row = conn.execute(
            "SELECT attempts FROM jobs WHERE lawd_cd = %s AND deal_ymd = %s FOR UPDATE",
            (lawd_cd, deal_ymd),
        ).fetchone()
        attempts = (row["attempts"] or 0) + 1
        wait = min(2 ** attempts, 24 * 60)  # 분 단위 지수 백오프, 최대 하루
        conn.execute(
            "UPDATE jobs SET status = CASE WHEN status = 'done' THEN 'done' ELSE 'error' END, "
            "attempts = %s, error = %s, next_try_at = %s WHERE lawd_cd = %s AND deal_ymd = %s",
            (attempts, msg, settings.now_ts() + timedelta(minutes=wait), lawd_cd, deal_ymd),
        )

"""누락 점검: 작업별 품질 집계와 진행 상황·누락 점검 보고."""
from psycopg.types.json import Jsonb

import db
import settings
from collector import api, codes, usage

# 누락 점검 대상: 위치·가격 분석에 꼭 필요한 필드
KEY_FIELDS = ["apt_nm", "umd_nm", "jibun", "road_nm", "exclu_use_ar", "floor", "build_year", "apt_seq"]
BLANK_FIELDS = KEY_FIELDS + ["deal_amount", "deal_date"]


def job_quality(rows, lawd_cd, deal_ymd):
    """작업 하나의 누락 점검 집계: 필드별 빈 값, 완전 중복, 계약월·코드 불일치, 해제 건수."""
    blank = {f: sum(1 for r in rows if r.get(f) is None or str(r.get(f)).strip() == "")
             for f in BLANK_FIELDS}
    keys = [tuple(r.get(c) for c in api.COLUMNS) for r in rows]
    return dict(
        q_blank={f: n for f, n in blank.items() if n},
        q_dup=len(keys) - len(set(keys)),
        q_ymd_bad=sum(1 for r in rows if not r.get("deal_date")
                      or r["deal_date"].strftime("%Y%m") != deal_ymd),
        q_sgg_bad=sum(1 for r in rows if (r.get("sgg_cd") or "") != lawd_cd),
        q_cancelled=sum(1 for r in rows if (r.get("cdeal_type") or "").strip()),
    )


def write_quality(conn, lawd_cd, deal_ymd, q):
    conn.execute(
        "UPDATE jobs SET q_blank = %(q_blank)s, q_dup = %(q_dup)s, q_ymd_bad = %(q_ymd_bad)s, "
        "q_sgg_bad = %(q_sgg_bad)s, q_cancelled = %(q_cancelled)s "
        "WHERE lawd_cd = %(lawd_cd)s AND deal_ymd = %(deal_ymd)s",
        {**q, "q_blank": Jsonb(q["q_blank"]), "lawd_cd": lawd_cd, "deal_ymd": deal_ymd},
    )


def db_size(conn):
    return conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]


def storage_pct():
    with db.connection() as conn:
        return 100 * db_size(conn) / (settings.DB_LIMIT_MB * 1024 * 1024)


def backfill_quality(conn):
    """품질 집계 열이 비어 있는 완료 작업을 채운다(이전 버전에서 받은 자료)."""
    todo = conn.execute(
        "SELECT lawd_cd, deal_ymd FROM jobs WHERE status = 'done' AND q_blank IS NULL").fetchall()
    for job in todo:
        rows = conn.execute("SELECT * FROM trades WHERE lawd_cd = %s AND deal_ymd = %s",
                            (job["lawd_cd"], job["deal_ymd"])).fetchall()
        write_quality(conn, job["lawd_cd"], job["deal_ymd"],
                      job_quality(rows, job["lawd_cd"], job["deal_ymd"]))
    return len(todo)


def storage(conn, trades):
    """Postgres DB 크기와 기준 용량(DB_LIMIT_MB) 대비 사용률."""
    total = db_size(conn)
    limit = settings.DB_LIMIT_MB * 1024 * 1024
    return dict(total_bytes=total, limit_bytes=limit, stop_pct=settings.STORAGE_STOP_PCT,
                pct=round(100 * total / limit, 1),
                bytes_per_trade=round(total / trades) if trades else None)


def progress():
    """진행 상황. 거래 건수는 jobs의 저장 건수 합으로 계산한다(대용량에서 COUNT(*) 피함)."""
    with db.connection() as conn:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
        by_month = conn.execute("""
            SELECT deal_ymd, COUNT(*) AS jobs,
                   COUNT(*) FILTER (WHERE status = 'done') AS done,
                   COUNT(*) FILTER (WHERE status IN ('error', 'incomplete')) AS bad,
                   COALESCE(SUM(stored_count), 0) AS trades, MAX(fetched_at) AS last_fetched
              FROM jobs GROUP BY deal_ymd ORDER BY deal_ymd""").fetchall()
        last = conn.execute("SELECT MAX(fetched_at) AS t FROM jobs").fetchone()["t"]
        used = usage.calls_today(conn)
        trades = sum(m["trades"] for m in by_month)
        st = storage(conn, trades)
    by_year = {}
    for m in by_month:
        y = by_year.setdefault(m["deal_ymd"][:4], dict(year=m["deal_ymd"][:4], jobs=0, done=0, bad=0,
                                                         trades=0, last_fetched=None))
        for k in ("jobs", "done", "bad", "trades"):
            y[k] += m[k] or 0
        y["last_fetched"] = max(filter(None, [y["last_fetched"], m["last_fetched"]]), default=None)
    total_jobs = sum(by_status.values())
    pending = by_status.get("pending", 0)
    per_day = max(settings.DAILY_LIMIT - settings.REFRESH_MONTHS * len(codes.load_codes()), 1)
    return dict(by_status=by_status, by_month=by_month, by_year=list(by_year.values()), trades=trades,
                last_fetched=last, calls_today=used, daily_limit=settings.DAILY_LIMIT,
                total_jobs=total_jobs, eta_days=-(-pending // per_day) if pending else 0,
                refresh_at=settings.REFRESH_AT, storage=st)


def quality_report():
    """누락 점검 결과 모음. 모두 jobs의 작업별 집계에서 계산한다."""
    names = codes.names()
    with db.connection() as conn:
        # 1) 아직 못 받았거나 건수가 안 맞는 작업
        problems = conn.execute("""
            SELECT deal_ymd, lawd_cd, status, total_count, stored_count, attempts, error, next_try_at
              FROM jobs WHERE status IN ('error', 'incomplete')
             ORDER BY deal_ymd, lawd_cd LIMIT 500""").fetchall()
        # 2) 받은 달이 모두 0건인 시군구 → 코드 변경(행정구역 개편) 의심
        zero_codes = conn.execute("""
            SELECT lawd_cd, COUNT(*) AS months, MAX(deal_ymd) AS last_ymd FROM jobs
             WHERE status = 'done' GROUP BY lawd_cd
            HAVING SUM(stored_count) = 0 AND COUNT(*) >= 2""").fetchall()
        # 3) 응답의 sggCd가 요청 코드와 다른 작업(코드 체계 변화 감지)
        sgg_mismatch = conn.execute("""
            SELECT lawd_cd, deal_ymd, q_sgg_bad AS n FROM jobs WHERE q_sgg_bad > 0
             ORDER BY deal_ymd, lawd_cd LIMIT 500""").fetchall()
        agg = conn.execute("""
            SELECT COALESCE(SUM(stored_count), 0) AS total, COALESCE(SUM(q_ymd_bad), 0) AS ymd_bad,
                   COALESCE(SUM(q_dup), 0) AS dup, COALESCE(SUM(q_cancelled), 0) AS cancelled
              FROM jobs WHERE status = 'done'""").fetchone()
        # 4) 필드별 빈 값 합계
        blank_rows = conn.execute("""
            SELECT b.key AS field, SUM(b.value::int) AS n
              FROM jobs, jsonb_each_text(q_blank) AS b
             WHERE q_blank IS NOT NULL GROUP BY b.key""").fetchall()
        # 5) 재수집 시 건수 변동 이력(최근 50건)
        changes = conn.execute('SELECT at, lawd_cd, deal_ymd, "before", "after" FROM changes '
                               "ORDER BY at DESC LIMIT 50").fetchall()

    blank_sum = dict.fromkeys(BLANK_FIELDS, 0)
    for r in blank_rows:
        blank_sum[r["field"]] = r["n"]
    total = agg["total"]
    blanks = [dict(field=api.CAMEL.get(f, f), missing=n, pct=round(100 * n / total, 2) if total else 0)
              for f, n in blank_sum.items()]
    for r in problems + zero_codes + sgg_mismatch + changes:
        r["name"] = names.get(r["lawd_cd"], "?")
    return dict(total=total, problems=problems, zero_codes=zero_codes, sgg_mismatch=sgg_mismatch,
                blanks=blanks, ymd_mismatch=agg["ymd_bad"], duplicates=agg["dup"],
                cancelled=agg["cancelled"], changes=changes)

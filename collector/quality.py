"""누락 점검: 작업별 품질 집계와 진행 상황·누락 점검 보고."""
from psycopg.types.json import Jsonb

import db
import settings
from collector import api

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

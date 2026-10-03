"""기존 SQLite(trades.db)를 Postgres로 한 번 옮긴다.

사용: python scripts/migrate_sqlite.py [--sqlite /data/trades.db] [--replace]
DATABASE_URL의 DB에 마이그레이션을 적용한 뒤 trades·jobs·api_usage·changes를 한 트랜잭션으로 복사하고,
작업별 저장 건수가 원본과 같은지 검증한다. 검증에 실패하면 1을 돌려준다(복사한 내용은 남는다).
"""
import argparse
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import settings  # noqa: E402
from collector import api  # noqa: E402
from collector.store import TRADE_COLS  # noqa: E402

BATCH = 5000
JOB_COLS = ["lawd_cd", "deal_ymd", "status", "total_count", "stored_count", "fetched_at", "checked_at",
            "attempts", "next_try_at", "error", "q_blank", "q_dup", "q_ymd_bad", "q_sgg_bad", "q_cancelled"]


def _blank_to_none(v):
    return None if v is None or (isinstance(v, str) and v.strip() == "") else v


def trade_row(r):
    """옛 trades 행(camelCase) → 새 trades 행(TRADE_COLS 순서의 값 목록)."""
    row = {api.snake(f): r[f] for f in api.FIELDS}
    row["deal_amount"] = api.to_int(r["dealAmount"])
    row["exclu_use_ar"] = api.to_float(r["excluUseAr"])
    row["floor"] = api.to_int(r["floor"])
    row["build_year"] = api.to_int(r["buildYear"])
    row["deal_date"] = date.fromisoformat(r["dealDate"]) if r["dealDate"] else None
    row.update(lawd_cd=r["lawd_cd"], deal_ymd=r["deal_ymd"], collected_at=_blank_to_none(r["collected_at"]))
    return [row[c] for c in TRADE_COLS]


def job_row(r, have):
    vals = {c: (r[c] if c in have else None) for c in JOB_COLS}
    for c in ("fetched_at", "checked_at", "next_try_at", "q_blank"):
        vals[c] = _blank_to_none(vals[c])
    vals["attempts"] = vals["attempts"] or 0
    return [vals[c] for c in JOB_COLS]


def copy_rows(conn, table, cols, rows):
    n = 0
    with conn.cursor() as cur, cur.copy(f"COPY {table} ({', '.join(cols)}) FROM STDIN") as copy:
        for values in rows:
            copy.write_row(values)
            n += 1
    return n


def iter_query(src, sql):
    cur = src.execute(sql)
    while batch := cur.fetchmany(BATCH):
        yield from batch


def verify(conn):
    """작업별 저장 건수(stored_count)와 실제 trades 건수가 다른 작업 목록."""
    return conn.execute("""
        SELECT j.lawd_cd, j.deal_ymd, j.stored_count, COALESCE(t.n, 0) AS n
          FROM jobs j
          LEFT JOIN (SELECT lawd_cd, deal_ymd, COUNT(*) AS n FROM trades GROUP BY 1, 2) t
            USING (lawd_cd, deal_ymd)
         WHERE j.stored_count IS NOT NULL AND j.stored_count <> COALESCE(t.n, 0)
         ORDER BY 2, 1""").fetchall()


def main(argv=None):
    parser = argparse.ArgumentParser(description="SQLite trades.db → Postgres 이전")
    parser.add_argument("--sqlite", default=str(Path(settings.env("DATA_DIR", "data")) / "trades.db"))
    parser.add_argument("--replace", action="store_true", help="대상 테이블을 비우고 다시 복사")
    args = parser.parse_args(argv)

    src_path = Path(args.sqlite)
    if not src_path.exists():
        print(f"SQLite 파일이 없습니다: {src_path}")
        return 1
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    db.migrate()
    with db.connection() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        if existing and not args.replace:
            print(f"Postgres jobs에 이미 {existing}행이 있습니다. 다시 옮기려면 --replace를 붙이세요.")
            return 1
        have = {r["name"] for r in src.execute("PRAGMA table_info(jobs)")}
        with conn.transaction():
            if args.replace:
                conn.execute("TRUNCATE trades, jobs, api_usage, changes RESTART IDENTITY")
            n_trades = copy_rows(conn, "trades", TRADE_COLS,
                                 (trade_row(r) for r in iter_query(src, "SELECT * FROM trades ORDER BY rowid")))
            n_jobs = copy_rows(conn, "jobs", JOB_COLS,
                               (job_row(r, have) for r in iter_query(src, "SELECT * FROM jobs")))
            copy_rows(conn, "api_usage", ["day", "calls"],
                      ([r["day"], r["calls"]] for r in iter_query(src, "SELECT * FROM api_usage")))
            copy_rows(conn, "changes", ["at", "lawd_cd", "deal_ymd", "before", "after"],
                      ([_blank_to_none(r["at"]), r["lawd_cd"], r["deal_ymd"], r["before"], r["after"]]
                       for r in iter_query(src, "SELECT * FROM changes")))
        src_trades = src.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        bad = verify(conn)
    src.close()
    print(f"trades {n_trades:,}행 (원본 {src_trades:,}) · jobs {n_jobs:,}행 복사")
    if n_trades != src_trades or bad:
        for b in bad[:20]:
            print(f"  불일치 {b['deal_ymd']} {b['lawd_cd']}: 작업 기록 {b['stored_count']} / 실제 {b['n']}")
        print(f"검증 실패: 건수 불일치 작업 {len(bad)}개")
        return 1
    print("검증 통과: 모든 작업의 저장 건수가 일치합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

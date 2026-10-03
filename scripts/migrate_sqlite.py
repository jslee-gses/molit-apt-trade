"""기존 SQLite(trades.db)를 Postgres로 한 번 옮긴다.

사용: python scripts/migrate_sqlite.py [--sqlite /data/trades.db] [--replace]
DATABASE_URL의 DB에 마이그레이션을 적용한 뒤 trades·jobs·api_usage·changes를 한 트랜잭션으로 복사하고,
원본과 표별 건수·(지역, 계약월)별 거래 건수가 같은지 검증한다. 검증에 실패하면 1을 돌려준다(복사한 내용은 남는다).
실행 전에 기존 수집기를 멈출 것(원본을 한 시점의 스냅샷으로 읽는다).
"""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import settings  # noqa: E402
from collector import api  # noqa: E402
from collector.store import TRADE_COLS  # noqa: E402

BATCH = 5000
TABLES = ["trades", "jobs", "api_usage", "changes"]
JOB_COLS = ["lawd_cd", "deal_ymd", "status", "total_count", "stored_count", "fetched_at", "checked_at",
            "attempts", "next_try_at", "error", "q_blank", "q_dup", "q_ymd_bad", "q_sgg_bad", "q_cancelled"]


def _blank_to_none(v):
    return None if v is None or (isinstance(v, str) and v.strip() == "") else v


def _snake_blank(v):
    """q_blank JSON의 키를 camelCase에서 snake_case로 바꾼다. 해석할 수 없으면 None(나중에 재계산)."""
    v = _blank_to_none(v)
    if v is None:
        return None
    try:
        d = json.loads(v)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    return json.dumps({api.snake(k): n for k, n in d.items()})


def trade_row(r):
    """옛 trades 행(camelCase) → 새 trades 행(TRADE_COLS 순서의 값 목록)."""
    row = {api.snake(f): r[f] for f in api.FIELDS}
    row["deal_amount"] = api.to_int(r["dealAmount"])
    row["exclu_use_ar"] = api.to_float(r["excluUseAr"])
    row["floor"] = api.to_smallint(r["floor"])
    row["build_year"] = api.to_smallint(r["buildYear"])
    try:
        row["deal_date"] = date.fromisoformat(r["dealDate"]) if r["dealDate"] else None
    except ValueError:
        row["deal_date"] = None
    row.update(lawd_cd=r["lawd_cd"], deal_ymd=r["deal_ymd"], collected_at=_blank_to_none(r["collected_at"]))
    return [row[c] for c in TRADE_COLS]


def job_row(r, have):
    vals = {c: (r[c] if c in have else None) for c in JOB_COLS}
    for c in ("fetched_at", "checked_at", "next_try_at"):
        vals[c] = _blank_to_none(vals[c])
    vals["q_blank"] = _snake_blank(vals["q_blank"])
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


def stored_count_warnings(conn):
    """작업별 저장 건수(stored_count)와 실제 trades 건수가 다른 작업 목록(원본 자체의 일치 여부)."""
    return conn.execute("""
        SELECT j.lawd_cd, j.deal_ymd, j.stored_count, COALESCE(t.n, 0) AS n
          FROM jobs j
          LEFT JOIN (SELECT lawd_cd, deal_ymd, COUNT(*) AS n FROM trades GROUP BY 1, 2) t
            USING (lawd_cd, deal_ymd)
         WHERE j.stored_count IS NOT NULL AND j.stored_count <> COALESCE(t.n, 0)
         ORDER BY 2, 1""").fetchall()


def fidelity_problems(src, conn):
    """복사 충실도: 표별 건수와 (지역, 계약월)별 거래 건수를 원본과 비교. (표별 건수 dict, 문제 목록)."""
    problems, counts = [], {}
    for t in TABLES:
        a = src.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        b = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
        counts[t] = (a, b)
        if a != b:
            problems.append(f"{t} 건수 불일치: 원본 {a} / Postgres {b}")
    s = {(r[0], r[1]): r[2] for r in src.execute(
        "SELECT lawd_cd, deal_ymd, COUNT(*) FROM trades GROUP BY lawd_cd, deal_ymd")}
    p = {(r["lawd_cd"], r["deal_ymd"]): r["n"] for r in conn.execute(
        "SELECT lawd_cd, deal_ymd, COUNT(*) AS n FROM trades GROUP BY lawd_cd, deal_ymd")}
    for k in sorted(set(s) | set(p), key=lambda k: (str(k[1]), str(k[0]))):
        if s.get(k, 0) != p.get(k, 0):
            problems.append(f"거래 건수 불일치 {k[1]} {k[0]}: 원본 {s.get(k, 0)} / Postgres {p.get(k, 0)}")
    return counts, problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="SQLite trades.db → Postgres 이전")
    parser.add_argument("--sqlite", default=str(Path(settings.env("DATA_DIR", "data")) / "trades.db"))
    parser.add_argument("--replace", action="store_true",
                        help="주의: Postgres에 이미 있는 trades/jobs/api_usage/changes를 모두 지우고 다시 복사")
    args = parser.parse_args(argv)

    src_path = Path(args.sqlite)
    if not src_path.exists():
        print(f"SQLite 파일이 없습니다: {src_path}")
        return 1
    db.migrate()
    with closing(sqlite3.connect(f"file:{src_path}?mode=ro", uri=True, isolation_level=None)) as src:
        src.row_factory = sqlite3.Row
        with db.connection() as conn:
            if not args.replace:
                for t in TABLES:
                    n = conn.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"]
                    if n:
                        print(f"Postgres {t}에 이미 {n}행이 있습니다. 다시 옮기려면 --replace를 붙이세요.")
                        return 1
            src.execute("BEGIN")  # 원본을 한 시점의 스냅샷으로 읽는다
            try:
                have = {r["name"] for r in src.execute("PRAGMA table_info(jobs)")}
                with conn.transaction():
                    if args.replace:
                        conn.execute("TRUNCATE trades, jobs, api_usage, changes RESTART IDENTITY")
                    copy_rows(conn, "trades", TRADE_COLS,
                              (trade_row(r) for r in iter_query(src, "SELECT * FROM trades ORDER BY rowid")))
                    copy_rows(conn, "jobs", JOB_COLS,
                              (job_row(r, have) for r in iter_query(src, "SELECT * FROM jobs")))
                    copy_rows(conn, "api_usage", ["day", "calls"],
                              ([r["day"], r["calls"]] for r in iter_query(src, "SELECT * FROM api_usage")))
                    copy_rows(conn, "changes", ["at", "lawd_cd", "deal_ymd", "before", "after"],
                              ([_blank_to_none(r["at"]), r["lawd_cd"], r["deal_ymd"], r["before"], r["after"]]
                               for r in iter_query(src, "SELECT * FROM changes")))
                counts, problems = fidelity_problems(src, conn)
                warns = stored_count_warnings(conn)
            finally:
                src.execute("ROLLBACK")
    for t, (a, b) in counts.items():
        print(f"{t}: 원본 {a:,}행 / Postgres {b:,}행")
    for w in warns[:20]:
        print(f"  경고 {w['deal_ymd']} {w['lawd_cd']}: 작업 기록 {w['stored_count']} / 실제 {w['n']}")
    if warns:
        print(f"경고: 원본에서 저장 건수와 실제 건수가 다른 작업 {len(warns)}개(복사 오류 아님)")
    if problems:
        for m in problems[:20]:
            print(f"  {m}")
        print(f"검증 실패: 복사 불일치 {len(problems)}건")
        return 1
    print("검증 통과: 표별 건수와 계약월·지역별 거래 건수가 원본과 같습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

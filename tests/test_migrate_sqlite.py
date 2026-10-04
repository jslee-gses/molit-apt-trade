import sqlite3
from datetime import date, datetime

from collector import api
from scripts import migrate_sqlite

OLD_COLS = ", ".join(f'"{f}" TEXT' for f in api.FIELDS if f not in ("dealAmount", "excluUseAr"))
OLD_DDL = f"""
CREATE TABLE trades (lawd_cd TEXT NOT NULL, deal_ymd TEXT NOT NULL, {OLD_COLS},
    dealAmount INTEGER, excluUseAr REAL, dealDate TEXT, collected_at TEXT);
CREATE TABLE jobs (lawd_cd TEXT NOT NULL, deal_ymd TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    total_count INTEGER, stored_count INTEGER, fetched_at TEXT, checked_at TEXT, attempts INTEGER DEFAULT 0,
    next_try_at TEXT, error TEXT, q_blank TEXT, q_dup INTEGER, q_ymd_bad INTEGER, q_sgg_bad INTEGER,
    q_cancelled INTEGER, PRIMARY KEY (lawd_cd, deal_ymd));
CREATE TABLE api_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
CREATE TABLE changes (at TEXT, lawd_cd TEXT, deal_ymd TEXT, before INTEGER, after INTEGER);
"""


def make_sqlite(path, stored_count=2):
    conn = sqlite3.connect(path)
    conn.executescript(OLD_DDL)
    base = {f: "" for f in api.FIELDS}
    rows = [
        {**base, "aptNm": "가", "floor": "3", "buildYear": "2001", "dealAmount": 50000, "excluUseAr": 59.9,
         "dealYear": "2026", "dealMonth": "1", "dealDay": "2", "aptSeq": "11110-1"},
        {**base, "aptNm": "나", "floor": "", "buildYear": "", "dealAmount": None, "excluUseAr": None},
    ]
    for r, dd in zip(rows, ["2026-01-02", None]):
        cols = ["lawd_cd", "deal_ymd", *api.FIELDS, "dealDate", "collected_at"]
        vals = ["11110", "202601", *[r[f] for f in api.FIELDS], dd, "2026-10-01 06:10:00"]
        conn.execute(f"INSERT INTO trades({', '.join(chr(34) + c + chr(34) for c in cols)}) "
                     f"VALUES ({', '.join('?' * len(cols))})", vals)
    conn.execute("INSERT INTO jobs VALUES ('11110','202601','done',2,?,'2026-10-01 06:10:00',"
                 "'2026-10-01 06:10:00',NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL)", (stored_count,))
    conn.execute("INSERT INTO jobs(lawd_cd, deal_ymd) VALUES ('11140','202601')")
    conn.execute("INSERT INTO api_usage VALUES ('2026-10-01', 120)")
    conn.execute("INSERT INTO changes VALUES ('2026-10-01 06:10:00','11110','202601',1,2)")
    conn.commit()
    conn.close()


def test_migrate_copies_and_verifies(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    with pg.connection() as conn:
        trades = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        jobs = conn.execute("SELECT * FROM jobs ORDER BY lawd_cd").fetchall()
        usage = conn.execute("SELECT * FROM api_usage").fetchone()
        change = conn.execute("SELECT * FROM changes").fetchone()
    assert len(trades) == 2
    assert trades[0]["floor"] == 3 and trades[0]["deal_date"] == date(2026, 1, 2)
    assert trades[0]["deal_amount"] == 50000 and float(trades[0]["exclu_use_ar"]) == 59.9
    assert trades[0]["collected_at"] == datetime(2026, 10, 1, 6, 10)
    assert jobs[0]["status"] == "done" and jobs[0]["fetched_at"] == datetime(2026, 10, 1, 6, 10)
    assert usage == {"day": date(2026, 10, 1), "calls": 120}
    assert change["after"] == 2


def test_migrate_handles_nulls(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    with pg.connection() as conn:
        second = conn.execute("SELECT floor, build_year, deal_amount, deal_date FROM trades ORDER BY id").fetchall()[1]
        job = conn.execute("SELECT attempts, q_blank FROM jobs WHERE lawd_cd = '11110'").fetchone()
    assert second == {"floor": None, "build_year": None, "deal_amount": None, "deal_date": None}
    assert job == {"attempts": 0, "q_blank": None}


def test_migrate_refuses_non_empty_without_replace(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 1
    assert migrate_sqlite.main(["--sqlite", str(src), "--replace"]) == 0
    with pg.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"] == 2


def test_source_count_mismatch_is_warning_only(pg, tmp_path, capsys):
    src = tmp_path / "trades.db"
    make_sqlite(src, stored_count=3)
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    out = capsys.readouterr().out
    assert "경고" in out and "작업 기록 3 / 실제 2" in out


def test_prints_per_table_counts(pg, tmp_path, capsys):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    out = capsys.readouterr().out
    assert "trades: 원본 2행 / Postgres 2행" in out
    assert "jobs: 원본 2행 / Postgres 2행" in out
    assert "api_usage: 원본 1행" in out and "changes: 원본 1행" in out


def test_jobs_without_q_columns(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    conn = sqlite3.connect(src)
    conn.executescript("""
        CREATE TABLE jobs2 AS SELECT lawd_cd, deal_ymd, status, total_count, stored_count, fetched_at,
            checked_at, attempts, next_try_at, error FROM jobs;
        DROP TABLE jobs; ALTER TABLE jobs2 RENAME TO jobs;""")
    conn.commit()
    conn.close()
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    with pg.connection() as c:
        job = c.execute("SELECT status, q_blank, q_dup FROM jobs WHERE lawd_cd = '11110'").fetchone()
    assert job == {"status": "done", "q_blank": None, "q_dup": None}


def test_q_blank_keys_become_snake_and_invalid_json_null(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    conn = sqlite3.connect(src)
    conn.execute("UPDATE jobs SET q_blank = ? WHERE lawd_cd = '11110'", ('{"aptNm": 2, "dealDate": 1}',))
    conn.execute("UPDATE jobs SET q_blank = ? WHERE lawd_cd = '11140'", ("{not json",))
    conn.commit()
    conn.close()
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    with pg.connection() as c:
        rows = {r["lawd_cd"]: r["q_blank"] for r in c.execute("SELECT lawd_cd, q_blank FROM jobs")}
    assert rows["11110"] == {"apt_nm": 2, "deal_date": 1}
    assert rows["11140"] is None


def test_invalid_deal_date_becomes_null(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    conn = sqlite3.connect(src)
    conn.execute("UPDATE trades SET dealDate = '2026-02-30' WHERE dealDate = '2026-01-02'")
    conn.commit()
    conn.close()
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    with pg.connection() as c:
        assert c.execute("SELECT COUNT(*) AS n FROM trades WHERE deal_date IS NULL").fetchone()["n"] == 2


def test_out_of_range_smallint_becomes_null(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    conn = sqlite3.connect(src)
    conn.execute("UPDATE trades SET floor = '99999', buildYear = '-99999' WHERE aptNm = '가'")
    conn.commit()
    conn.close()
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 0
    with pg.connection() as c:
        r = c.execute("SELECT floor, build_year FROM trades WHERE apt_nm = '가'").fetchone()
    assert r == {"floor": None, "build_year": None}


def test_refuses_when_only_trades_non_empty(pg, tmp_path, capsys):
    src = tmp_path / "trades.db"
    make_sqlite(src)
    with pg.connection() as c:
        c.execute("INSERT INTO trades(lawd_cd, deal_ymd) VALUES ('1', '202601')")
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 1
    assert "trades" in capsys.readouterr().out

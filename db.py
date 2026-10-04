"""Postgres 커넥션 풀과 스키마 마이그레이션.

연결은 autocommit이고 행은 dict로 돌려준다. 여러 문장을 묶을 때는 `with conn.transaction():`.
"""
import atexit
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import settings

MIGRATIONS_DIR = settings.BASE_DIR / "migrations"
_pool = None
MIGRATE_LOCK_ID = 7_265_001_001  # 동시에 시작한 프로세스끼리 마이그레이션을 직렬화


def pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.require("DATABASE_URL"),
            min_size=1,
            max_size=int(settings.env("DB_POOL_MAX", "8")),
            kwargs={"autocommit": True, "row_factory": dict_row},
            check=ConnectionPool.check_connection,
            open=True,
        )
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


atexit.register(close_pool)


@contextmanager
def connection():
    with pool().connection() as conn:
        yield conn


def migrate():
    """migrations/*.sql 중 아직 적용하지 않은 파일을 이름 순서대로 하나씩 트랜잭션으로 적용한다."""
    with connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATE_LOCK_ID,))
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version = %s",
                                (path.stem,)).fetchone():
                    continue  # 잠금을 기다리는 동안 다른 프로세스가 적용함
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.stem,))

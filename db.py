"""Postgres 커넥션 풀과 스키마 마이그레이션.

연결은 autocommit이고 행은 dict로 돌려준다. 여러 문장을 묶을 때는 `with conn.transaction():`.
"""
from contextlib import contextmanager

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

import settings

MIGRATIONS_DIR = settings.BASE_DIR / "migrations"
_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            settings.require("DATABASE_URL"),
            min_size=1,
            max_size=int(settings.env("DB_POOL_MAX", "5")),
            kwargs={"autocommit": True, "row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


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
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in done:
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.stem,))

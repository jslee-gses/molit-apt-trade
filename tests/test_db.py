from decimal import Decimal


def test_migrate_creates_core_tables(pg):
    with pg.connection() as conn:
        names = {r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    assert {"trades", "jobs", "api_usage", "changes", "schema_migrations"} <= names


def test_migrate_is_idempotent(pg):
    pg.migrate()
    with pg.connection() as conn:
        n = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert n == len(list(pg.MIGRATIONS_DIR.glob("*.sql")))


def test_trades_generated_columns(pg):
    with pg.connection() as conn:
        conn.execute(
            "INSERT INTO trades(lawd_cd, deal_ymd, deal_amount, exclu_use_ar, cdeal_type) VALUES "
            "('11110', '202601', 84000, 84.0, 'O'), ('11110', '202601', 50000, 0, '')")
        rows = conn.execute("SELECT price_per_m2, is_cancelled FROM trades ORDER BY id").fetchall()
    assert rows[0]["price_per_m2"] == Decimal("1000.0")
    assert rows[0]["is_cancelled"] is True
    assert rows[1]["price_per_m2"] is None
    assert rows[1]["is_cancelled"] is False


def test_connection_is_autocommit_dict_rows(pg):
    with pg.connection() as conn:
        conn.execute("INSERT INTO api_usage(day, calls) VALUES ('2026-10-03', 5)")
    with pg.connection() as conn:
        row = conn.execute("SELECT calls FROM api_usage").fetchone()
    assert row == {"calls": 5}

"""하루 API 호출 수 기록과 남은 한도(KST 날짜 기준)."""
import settings


def _today():
    return settings.now_kst().date()


def calls_today(conn):
    row = conn.execute("SELECT calls FROM api_usage WHERE day = %s", (_today(),)).fetchone()
    return row["calls"] if row else 0


def count_call(conn):
    conn.execute(
        "INSERT INTO api_usage(day, calls) VALUES (%s, 1) "
        "ON CONFLICT (day) DO UPDATE SET calls = api_usage.calls + 1",
        (_today(),),
    )


def quota_left(conn):
    return settings.DAILY_LIMIT - calls_today(conn)

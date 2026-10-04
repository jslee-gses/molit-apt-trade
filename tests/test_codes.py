from datetime import datetime

import settings
from collector import codes


def test_load_codes_has_seoul_jongno():
    df = codes.load_codes()
    assert list(df.columns) == ["시도", "LAWD_CD", "시군구"]
    assert "11110" in set(df["LAWD_CD"])
    assert codes.names()["11110"] == "서울특별시 종로구"


def test_month_range_crosses_year():
    assert codes.month_range("202511", "202602") == ["202511", "202512", "202601", "202602"]


def test_months_ago(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 2, 10, tzinfo=settings.KST))
    assert codes.months_ago(0) == "202602"
    assert codes.months_ago(2) == "202512"
    assert codes.months_ago(14) == "202412"

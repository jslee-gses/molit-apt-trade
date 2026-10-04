"""시군구 코드 목록(lawd_codes.csv)과 계약월 계산."""
from functools import lru_cache

import pandas as pd

import settings

CODES_PATH = settings.BASE_DIR / "lawd_codes.csv"


@lru_cache(maxsize=1)
def load_codes():
    return pd.read_csv(CODES_PATH, dtype=str, encoding="utf-8-sig")


@lru_cache(maxsize=1)
def names():
    """시군구코드 → '시도 시군구'."""
    return {r.LAWD_CD: f"{r.시도} {r.시군구}" for r in load_codes().itertuples()}


def month_range(start_ymd, end_ymd):
    y, m = int(start_ymd[:4]), int(start_ymd[4:])
    out = []
    while f"{y}{m:02d}" <= end_ymd:
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def months_ago(n):
    now = settings.now_kst()
    y, m = now.year, now.month - n
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}"

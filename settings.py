"""환경변수·시각 등 공통 설정.

값은 환경변수 → 프로젝트 루트 .env 순서로 찾는다(Railway는 Variables, 로컬은 .env).
"""
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).parent
KST = ZoneInfo("Asia/Seoul")


def _read_dotenv(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, value = line.partition("=")
            name = name.strip()
            if sep and name and not name.startswith("#"):
                values[name] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _read_dotenv(BASE_DIR / ".env")


def env(name, default=None):
    return os.environ.get(name) or _DOTENV.get(name) or default


def require(name):
    value = env(name)
    if not value:
        raise RuntimeError(f"{name}가 설정되지 않았습니다.")
    return value


def flag(name, default=True):
    value = env(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off")


def service_key():
    key = require("MOLIT_SERVICE_KEY")
    # Encoding 키를 넣었으면 풀어서 requests가 한 번만 인코딩하게 한다
    return unquote(key) if "%" in key else key


def now_kst():
    return datetime.now(KST)


def now_ts():
    """DB에 넣는 시각: KST, 시간대 정보 없음, 초 단위."""
    return now_kst().replace(tzinfo=None, microsecond=0)


def now_str():
    return now_ts().strftime("%Y-%m-%d %H:%M:%S")


START_YMD = env("START_YMD", "200601")                       # 수집 시작 계약월(공개 시작 2006-01)
DAILY_LIMIT = int(env("DAILY_LIMIT", "8000"))                # 하루 호출 상한(개발계정 한도보다 낮게)
REQUEST_INTERVAL = float(env("REQUEST_INTERVAL", "1.5"))     # 호출 간격(초)
REFRESH_MONTHS = int(env("REFRESH_MONTHS", "3"))             # 매일 다시 받을 최근 개월 수(신고기한 30일, 해제 반영)
VWORLD_KEY = env("VWORLD_KEY", "")                           # 브이월드 배경지도(WMTS) 키. 없으면 OpenStreetMap(개발용)
REFRESH_AT = env("REFRESH_AT", "00:00")                      # 매일 수집을 시작하는 시각(KST, 하루 한도가 자정에 다시 참)
RECHECK_DAYS = int(env("RECHECK_DAYS", "7"))                 # 최근 1년(재수집 구간 이전) 건수 재확인 주기
RECHECK_MONTHS = int(env("RECHECK_MONTHS", "12"))            # 주기 재확인할 지난 개월 수
OLD_RECHECK_DAYS = int(env("OLD_RECHECK_DAYS", "180"))       # 그보다 오래된 달의 재확인 주기
STORAGE_STOP_PCT = float(env("STORAGE_STOP_PCT", "90"))      # DB 사용률이 이 이상이면 과거 자료 수집 중단
DB_LIMIT_MB = int(env("DB_LIMIT_MB", "5000"))                # 용량 사용률 기준(Railway Hobby 볼륨 5GB)
BATCH_MINUTES = int(env("BATCH_MINUTES", "1"))               # 수집 배치 주기(분)
BATCH_JOBS = int(env("BATCH_JOBS", "20"))                    # 배치 한 번에 처리할 작업 수

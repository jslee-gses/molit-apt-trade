# 계획 1: Postgres 기반 이전 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** SQLite 단일 파일(`collector.py`)로 된 수집 앱을 Postgres 기반의 모듈 구조로 옮기고, 공유 비밀번호 로그인과 SQLite → Postgres 이전 스크립트를 갖춰 Railway에 그대로 배포할 수 있게 한다.

**Architecture:** `settings.py`(환경변수·시각) → `db.py`(psycopg 3 풀, SQL 마이그레이션) → `collector/`(API·저장·작업 선택·품질 보고) → `web/`(Flask 블루프린트) → `app.py`(앱 팩토리) + `scheduler.py`(APScheduler). 기존 수집 규칙(우선순위, 재확인 주기, 한도, 백오프)은 그대로 옮기고 저장소만 바꾼다. 화면과 `/api/*` 응답 형식(camelCase 필드)은 유지한다.

**Tech Stack:** Python 3.12(Railway) / 로컬 3.14, Flask 3.1, psycopg 3.2(+pool), Postgres 17(로컬)·Railway Postgres, APScheduler 3.11, pandas 2.3, pytest 8

**Spec:** `docs/superpowers/specs/2026-10-03-analysis-dashboard-design.md` (§3 아키텍처, §4.1–4.2 데이터 모델, §8 오류 처리, §10 이전 절차, §11 환경변수)

**이 계획 다음:** 계획 2(`2026-10-03-plan2-geo.md`, 단지 좌표·경계·지역 판정), 계획 3(`2026-10-03-plan3-analytics-ui.md`, 집계·API·화면). 계획 1만으로도 배포 가능한 상태가 된다.

## Global Constraints

- 데이터는 공공데이터포털 Open API로만 받는다. rt.molit.go.kr 크롤링 금지.
- 인증키·비밀번호(`MOLIT_SERVICE_KEY`, `APP_PASSWORD`, `SECRET_KEY`, DB 연결 문자열)는 저장소에 올리지 않는다. 로컬은 `.env`, Railway는 Variables.
- 코드는 Python 3.12에서 돌아야 한다(Railway `.python-version` = 3.12). 3.13 이상 전용 문법·표준 라이브러리 금지.
- 시각은 KST 기준. DB의 TIMESTAMP 컬럼에는 시간대 정보 없는 KST 시각(`settings.now_ts()`)을 넣는다.
- 모든 모듈은 현재 시각을 `settings.now_kst()` / `settings.now_ts()`로만 얻는다(테스트에서 시각을 고정하기 위해 `from settings import now_kst` 금지, `import settings` 후 호출).
- DB 커넥션 풀은 `autocommit=True`, `row_factory=dict_row`. 여러 문장을 묶어야 하면 `with conn.transaction():`.
- 거래 테이블 컬럼은 API 필드명을 snake_case로 바꾼 이름. 화면·`/api/trades`·CSV에는 camelCase(API 필드명)로 되돌려 내보낸다.
- 하루 호출 상한 `DAILY_LIMIT` 기본 8000, 호출 간격 `REQUEST_INTERVAL` 기본 1.5초, 매일 수집 시작 `REFRESH_AT` 기본 06:00(KST), 최근 `REFRESH_MONTHS`=3개월 매일 재수집, 지난 12개월은 7일마다, 그 이전은 180일마다 건수 재확인 — 기존 값 그대로.
- 용량 기준 `DB_LIMIT_MB` 기본 5000, 과거 자료 수집 중단 `STORAGE_STOP_PCT` 기본 90.
- 테스트 DB는 `.env`의 `TEST_DATABASE_URL`(localhost만 허용). 테스트는 운영 DB에 절대 연결하지 않는다.
- 커밋 메시지는 한국어, 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **금액·면적·층 값의 형식 변형**: `dealAmount`에 쉼표·공백(`"  84,000"`), 층에 음수(`"-1"`, 지하), 숫자가 아닌 값이 와도 행이 버려지지 않고 숫자는 정확히, 이상한 값은 NULL로 저장돼야 한다 → Task 2 `test_to_rows_*`.
2. **잘못된 계약일**: `dealMonth=13`처럼 날짜가 안 되는 값은 `deal_date`가 NULL이 되고 누락 점검 ④(계약월 불일치)에 잡혀야 한다 → Task 2 `test_to_rows_invalid_date_is_none`, Task 3 `test_save_job_quality_counts`.
3. **작업 도중 한도 소진**: 여러 페이지 작업 중간에 한도가 다 차면 그 작업은 저장되지 않고(부분 저장 없음) 다음 날 06:00까지 멈춰야 한다 → Task 4 `test_run_batch_pauses_when_quota_runs_out`.
4. **로그인 후 이동 주소 악용**: `next=//evil.example`이나 `next=https://evil.example`로 외부 사이트로 보내지 말아야 한다 → Task 7 `test_login_rejects_external_next`.
5. **운영 SQLite의 빈 값·옛 형식**: 품질 컬럼이 NULL이거나 시도 횟수가 NULL인 작업, 빈 문자열 층 값이 있어도 이전이 실패하지 않고 건수 검증까지 끝나야 한다 → Task 8 `test_migrate_handles_nulls`.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `settings.py` (새로) | `.env`·환경변수 읽기, KST 시각, 수집 설정 상수 |
| `db.py` (새로) | psycopg 커넥션 풀, `migrations/*.sql` 순서 적용 |
| `migrations/001_core.sql` (새로) | `trades`, `jobs`, `api_usage`, `changes` |
| `collector/__init__.py` (새로) | 빈 패키지 |
| `collector/codes.py` (새로) | `lawd_codes.csv` 읽기, 계약월 계산 |
| `collector/api.py` (새로) | 국토부 API 호출·XML 파싱·행 변환, 필드명 변환표 |
| `collector/usage.py` (새로) | 일일 호출 수 기록·남은 한도 |
| `collector/quality.py` (새로) | 작업별 품질 집계, 진행 상황·용량·누락 점검 보고 |
| `collector/store.py` (새로) | 작업 생성, 작업 저장(교체), 오류 기록, 저장 후처리 훅 |
| `collector/jobs.py` (새로) | 다음 작업 선택, 건수 재확인, 수집 배치, 상태 |
| `scheduler.py` (새로) | APScheduler 작업 등록 |
| `web/__init__.py` (새로) | 빈 패키지 |
| `web/common.py` (새로) | 시군구 이름, 조회 조건, 행 변환, 템플릿 필터 |
| `web/pages.py` (새로) | `/`, `/status`, `/download.csv` |
| `web/api.py` (새로) | `/api/status`, `/api/quality`, `/api/trades` |
| `web/auth.py` (새로) | `/login`, `/logout`, 로그인 보호, 시도 제한 |
| `app.py` (교체) | 앱 팩토리, JSON 직렬화, 스케줄러 시작 |
| `collector.py` (삭제) | 위 모듈로 옮긴 뒤 삭제 |
| `templates/base.html`, `templates/status.html` (수정) | 로그아웃, 용량 표시 |
| `templates/login.html` (새로) | 로그인 화면 |
| `scripts/migrate_sqlite.py` (새로) | SQLite → Postgres 1회 이전·검증 |
| `requirements.txt` (수정), `requirements-dev.txt`, `pytest.ini` (새로) | 의존성·테스트 설정 |
| `tests/conftest.py`, `tests/helpers.py`, `tests/test_*.py` (새로) | 테스트 |
| `docs/runbooks/postgres-migration.md` (새로) | Railway 이전 절차 |
| `README.md` (수정) | 구조·환경변수·로컬 실행 |

---

### Task 1: 개발 환경, 설정 모듈, DB 풀과 마이그레이션

**Files:**
- Create: `settings.py`, `db.py`, `migrations/001_core.sql`, `requirements-dev.txt`, `pytest.ini`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_settings.py`, `tests/test_db.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: 없음
- Produces:
  - `settings.BASE_DIR: Path`, `settings.KST`
  - `settings.env(name, default=None) -> str | None`, `settings.require(name) -> str`, `settings.flag(name, default=True) -> bool`, `settings.service_key() -> str`
  - `settings.now_kst() -> datetime(tz=KST)`, `settings.now_ts() -> datetime(naive, 초 단위)`, `settings.now_str() -> "YYYY-MM-DD HH:MM:SS"`
  - 상수: `START_YMD, DAILY_LIMIT, REQUEST_INTERVAL, REFRESH_MONTHS, REFRESH_AT, RECHECK_DAYS, RECHECK_MONTHS, OLD_RECHECK_DAYS, STORAGE_STOP_PCT, DB_LIMIT_MB, BATCH_MINUTES, BATCH_JOBS`
  - `db.pool()`, `db.close_pool()`, `db.connection()`(context manager → psycopg Connection, autocommit, dict_row), `db.migrate()`, `db.MIGRATIONS_DIR`
  - 테스트 픽스처 `pg`(빈 스키마 + 마이그레이션, `db` 모듈을 돌려줌)

- [ ] **Step 1: 로컬 Postgres 준비 (사람이 직접 — 실행자는 이 단계를 사용자에게 요청하고 완료를 확인받는다)**

PowerShell에서 (관리자 승인 창이 뜬다. 설치 중 superuser 비밀번호를 정한다):
```powershell
winget install -e --id PostgreSQL.PostgreSQL.17 --interactive
& "C:\Program Files\PostgreSQL\17\bin\createdb.exe" -U postgres molit_test
& "C:\Program Files\PostgreSQL\17\bin\createdb.exe" -U postgres molit_dev
```
프로젝트 루트 `.env`에 다음 줄을 추가한다(`<pw>`는 설치 때 정한 비밀번호, 이미 있는 `MOLIT_SERVICE_KEY` 줄은 그대로):
```
DATABASE_URL=postgresql://postgres:<pw>@localhost:5432/molit_dev
TEST_DATABASE_URL=postgresql://postgres:<pw>@localhost:5432/molit_test
APP_PASSWORD=<로컬에서 쓸 비밀번호>
SECRET_KEY=<python -c "import secrets;print(secrets.token_hex(32))" 결과>
COLLECT_ENABLED=false
```

- [ ] **Step 2: 의존성 파일과 가상환경**

`requirements.txt` 전체를 다음으로 바꾼다:
```
flask==3.1.*
gunicorn==23.*
requests==2.32.*
pandas==2.3.*
apscheduler==3.11.*
psycopg[binary,pool]==3.2.*
```
`requirements-dev.txt`:
```
-r requirements.txt
pytest==8.*
```
`pytest.ini`:
```ini
[pytest]
testpaths = tests
pythonpath = .
addopts = -q
```
가상환경을 만들고 설치:
```bash
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
```
Expected: 설치 성공(`Successfully installed ... psycopg ...`).

- [ ] **Step 3: 설정 모듈 테스트 작성**

`tests/__init__.py`는 빈 파일. `tests/test_settings.py`:
```python
import settings


def test_env_prefers_environment(monkeypatch):
    monkeypatch.setenv("SOME_TEST_VAR", "from-env")
    assert settings.env("SOME_TEST_VAR", "default") == "from-env"


def test_env_default_when_missing(monkeypatch):
    monkeypatch.delenv("NOPE_TEST_VAR", raising=False)
    assert settings.env("NOPE_TEST_VAR", "default") == "default"


def test_require_raises_when_missing(monkeypatch):
    monkeypatch.delenv("NOPE_TEST_VAR", raising=False)
    try:
        settings.require("NOPE_TEST_VAR")
    except RuntimeError as e:
        assert "NOPE_TEST_VAR" in str(e)
    else:
        raise AssertionError("RuntimeError가 나야 한다")


def test_flag(monkeypatch):
    monkeypatch.setenv("FLAG_TEST", "false")
    assert settings.flag("FLAG_TEST", True) is False
    monkeypatch.setenv("FLAG_TEST", "1")
    assert settings.flag("FLAG_TEST", False) is True
    monkeypatch.delenv("FLAG_TEST")
    assert settings.flag("FLAG_TEST", True) is True


def test_read_dotenv(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# 주석\nA=1\nB="two"\nC=\'three\'\nbroken line\n', encoding="utf-8")
    assert settings._read_dotenv(p) == {"A": "1", "B": "two", "C": "three"}


def test_service_key_unquotes_encoding_key(monkeypatch):
    monkeypatch.setenv("MOLIT_SERVICE_KEY", "abc%2Bdef%3D%3D")
    assert settings.service_key() == "abc+def=="


def test_now_ts_is_naive_kst(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 6, 0, 1, 999, tzinfo=settings.KST))
    assert settings.now_ts() == datetime(2026, 10, 3, 6, 0, 1)
    assert settings.now_str() == "2026-10-03 06:00:01"
```

- [ ] **Step 4: 테스트 공통 설정 작성**

`tests/conftest.py`:
```python
"""테스트 공통 설정: 로컬 Postgres 테스트 DB를 테스트마다 비우고 스키마를 새로 만든다."""
import os
from urllib.parse import urlparse

import psycopg
import pytest

import settings

TEST_URL = settings.env("TEST_DATABASE_URL")
if not TEST_URL:
    pytest.exit(".env에 TEST_DATABASE_URL(로컬 테스트 DB)을 설정하세요.", returncode=2)
if urlparse(TEST_URL).hostname not in ("localhost", "127.0.0.1"):
    pytest.exit("TEST_DATABASE_URL은 로컬 DB만 허용합니다.", returncode=2)

# 앱 코드가 운영 DB나 실제 키를 보지 않도록 테스트 값으로 덮어쓴다
os.environ["DATABASE_URL"] = TEST_URL
os.environ["COLLECT_ENABLED"] = "false"
os.environ["APP_PASSWORD"] = "test-password"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["MOLIT_SERVICE_KEY"] = "test-key"


@pytest.fixture
def pg():
    """빈 스키마에 마이그레이션을 적용한 DB. db 모듈을 돌려준다."""
    import db
    db.close_pool()
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    db.migrate()
    yield db
    db.close_pool()
```

- [ ] **Step 5: DB 테스트 작성**

`tests/test_db.py`:
```python
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
```

- [ ] **Step 6: 테스트가 실패하는지 확인**

Run: `.venv/Scripts/python -m pytest tests/test_settings.py tests/test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'settings'` (conftest 수집 단계 오류)

- [ ] **Step 7: 설정 모듈 구현**

`settings.py`:
```python
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
REFRESH_AT = env("REFRESH_AT", "06:00")                      # 매일 수집을 시작하는 시각(KST)
RECHECK_DAYS = int(env("RECHECK_DAYS", "7"))                 # 최근 1년(재수집 구간 이전) 건수 재확인 주기
RECHECK_MONTHS = int(env("RECHECK_MONTHS", "12"))            # 주기 재확인할 지난 개월 수
OLD_RECHECK_DAYS = int(env("OLD_RECHECK_DAYS", "180"))       # 그보다 오래된 달의 재확인 주기
STORAGE_STOP_PCT = float(env("STORAGE_STOP_PCT", "90"))      # DB 사용률이 이 이상이면 과거 자료 수집 중단
DB_LIMIT_MB = int(env("DB_LIMIT_MB", "5000"))                # 용량 사용률 기준(Railway Hobby 볼륨 5GB)
BATCH_MINUTES = int(env("BATCH_MINUTES", "1"))               # 수집 배치 주기(분)
BATCH_JOBS = int(env("BATCH_JOBS", "20"))                    # 배치 한 번에 처리할 작업 수
```

- [ ] **Step 8: 마이그레이션 SQL 작성**

`migrations/001_core.sql`:
```sql
-- 원본 거래: 수집 단위(lawd_cd x deal_ymd)마다 통째로 교체 저장한다
CREATE TABLE trades (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lawd_cd TEXT NOT NULL,
    deal_ymd TEXT NOT NULL,
    sgg_cd TEXT, umd_cd TEXT, land_cd TEXT, bonbun TEXT, bubun TEXT,
    road_nm TEXT, road_nm_sgg_cd TEXT, road_nm_cd TEXT, road_nm_seq TEXT, road_nmb_cd TEXT,
    road_nm_bonbun TEXT, road_nm_bubun TEXT,
    umd_nm TEXT, apt_nm TEXT, jibun TEXT,
    exclu_use_ar NUMERIC,
    deal_year TEXT, deal_month TEXT, deal_day TEXT,
    deal_amount INTEGER,
    floor SMALLINT,
    build_year SMALLINT,
    apt_seq TEXT, cdeal_type TEXT, cdeal_day TEXT, dealing_gbn TEXT, estate_agent_sgg_nm TEXT,
    rgst_date TEXT, apt_dong TEXT, sler_gbn TEXT, buyer_gbn TEXT, land_leasehold_gbn TEXT,
    deal_date DATE,
    collected_at TIMESTAMP,
    price_per_m2 NUMERIC GENERATED ALWAYS AS (
        CASE WHEN exclu_use_ar > 0 THEN round(deal_amount / exclu_use_ar, 1) END) STORED,
    is_cancelled BOOLEAN GENERATED ALWAYS AS (COALESCE(btrim(cdeal_type), '') <> '') STORED
);
CREATE INDEX ix_trades_job ON trades (lawd_cd, deal_ymd);
CREATE INDEX ix_trades_apt ON trades (apt_seq, deal_date);
CREATE INDEX ix_trades_date ON trades (deal_date);

CREATE TABLE jobs (
    lawd_cd TEXT NOT NULL,
    deal_ymd TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending / done / incomplete / error
    total_count INTEGER,
    stored_count INTEGER,
    fetched_at TIMESTAMP,
    checked_at TIMESTAMP,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try_at TIMESTAMP,
    error TEXT,
    q_blank JSONB,
    q_dup INTEGER,
    q_ymd_bad INTEGER,
    q_sgg_bad INTEGER,
    q_cancelled INTEGER,
    PRIMARY KEY (lawd_cd, deal_ymd)
);
CREATE INDEX ix_jobs_status ON jobs (status, deal_ymd);

CREATE TABLE api_usage (day DATE PRIMARY KEY, calls INTEGER NOT NULL);

-- 재수집 시 건수 변동 기록(늦은 신고·해제 추적)
CREATE TABLE changes (
    at TIMESTAMP NOT NULL,
    lawd_cd TEXT NOT NULL,
    deal_ymd TEXT NOT NULL,
    before INTEGER,
    after INTEGER
);
CREATE INDEX ix_changes_at ON changes (at DESC);
```

- [ ] **Step 9: DB 모듈 구현**

`db.py`:
```python
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
```

- [ ] **Step 10: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_settings.py tests/test_db.py -v`
Expected: PASS (11 passed)

- [ ] **Step 11: Commit**

```bash
git add settings.py db.py migrations/001_core.sql requirements.txt requirements-dev.txt pytest.ini tests/__init__.py tests/conftest.py tests/test_settings.py tests/test_db.py
git commit -m "Postgres 기반: 설정 모듈, 커넥션 풀, 핵심 스키마 마이그레이션

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 국토부 API 호출·파싱 모듈

**Files:**
- Create: `collector/__init__.py`, `collector/codes.py`, `collector/api.py`, `tests/test_api.py`, `tests/test_codes.py`

**Interfaces:**
- Consumes: `settings.REQUEST_INTERVAL`, `settings.now_kst()`, `settings.BASE_DIR`
- Produces:
  - `collector.codes.load_codes() -> pandas.DataFrame`(열: 시도, LAWD_CD, 시군구; 캐시됨), `codes.names() -> dict[str, str]`(코드 → "시도 시군구"), `codes.month_range(start_ymd, end_ymd) -> list[str]`, `codes.months_ago(n) -> "YYYYMM"`
  - `collector.api.FIELDS: list[str]`(API 필드명 32개), `api.snake(name) -> str`, `api.COLUMNS: list[str]`(snake 필드명), `api.CAMEL: dict[str, str]`(snake → API 이름, `deal_date`→`dealDate` 포함)
  - `api.QuotaExceeded`, `api.ApiError`
  - `api.to_int(v) -> int | None`, `api.to_float(v) -> float | None`
  - `api.parse_response(content: bytes) -> (list[dict], int)`
  - `api.fetch_page(session, key, lawd_cd, deal_ymd, page_no, num_rows=1000, on_call=None) -> (items, total)`
  - `api.fetch_job(session, key, lawd_cd, deal_ymd, on_call=None) -> (items, total)`
  - `api.to_rows(items, lawd_cd, deal_ymd, collected_at) -> list[dict]` — snake 키, `deal_amount:int|None`, `exclu_use_ar:float|None`, `floor:int|None`, `build_year:int|None`, `deal_date:date|None`, `lawd_cd`, `deal_ymd`, `collected_at`

- [ ] **Step 1: 테스트 작성**

`tests/test_codes.py`:
```python
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
```

`tests/test_api.py`:
```python
from datetime import date

import pytest

import settings
from collector import api

OK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>
<body><items>
<item><aptNm>테스트아파트</aptNm><aptSeq>11110-1</aptSeq><dealAmount>  84,000</dealAmount>
<dealYear>2026</dealYear><dealMonth>1</dealMonth><dealDay>5</dealDay><excluUseAr>84.9751</excluUseAr>
<floor>12</floor><buildYear>2005</buildYear><sggCd>11110</sggCd><umdCd>10100</umdCd><umdNm>청운동</umdNm>
<cdealType> </cdealType></item>
<item><aptNm>둘째</aptNm><dealAmount>abc</dealAmount><dealYear>2026</dealYear><dealMonth>13</dealMonth>
<dealDay>1</dealDay><floor>-1</floor><excluUseAr>x</excluUseAr></item>
</items><numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>"""

GATEWAY_QUOTA_XML = """<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>
<returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>
<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""

GATEWAY_KEY_XML = """<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>
<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""

RESULT_ERROR_XML = """<response><header><resultCode>03</resultCode><resultMsg>NO DATA</resultMsg></header></response>"""


def test_snake_and_camel_maps():
    assert api.snake("excluUseAr") == "exclu_use_ar"
    assert api.snake("roadNmbCd") == "road_nmb_cd"
    assert api.snake("sggCd") == "sgg_cd"
    assert len(api.COLUMNS) == len(api.FIELDS) == 32
    assert api.CAMEL["apt_seq"] == "aptSeq"
    assert api.CAMEL["deal_date"] == "dealDate"


def test_parse_response_ok():
    items, total = api.parse_response(OK_XML.encode())
    assert total == 2
    assert items[0]["aptNm"] == "테스트아파트"
    assert items[0]["cdealType"] == ""


def test_parse_response_gateway_quota():
    with pytest.raises(api.QuotaExceeded):
        api.parse_response(GATEWAY_QUOTA_XML.encode())


def test_parse_response_gateway_other_error():
    with pytest.raises(api.ApiError, match="게이트웨이 오류 30"):
        api.parse_response(GATEWAY_KEY_XML.encode())


def test_parse_response_result_error():
    with pytest.raises(api.ApiError, match="API 오류 03"):
        api.parse_response(RESULT_ERROR_XML.encode())


def test_parse_response_bad_xml():
    with pytest.raises(api.ApiError, match="XML 파싱 실패"):
        api.parse_response(b"<not xml")


def test_to_rows_types_and_defaults():
    items, _ = api.parse_response(OK_XML.encode())
    rows = api.to_rows(items, "11110", "202601", "COLLECTED")
    r = rows[0]
    assert r["deal_amount"] == 84000
    assert r["exclu_use_ar"] == pytest.approx(84.9751)
    assert r["floor"] == 12
    assert r["build_year"] == 2005
    assert r["deal_date"] == date(2026, 1, 5)
    assert r["road_nm"] == ""
    assert (r["lawd_cd"], r["deal_ymd"], r["collected_at"]) == ("11110", "202601", "COLLECTED")


def test_to_rows_bad_values_become_none():
    items, _ = api.parse_response(OK_XML.encode())
    r = api.to_rows(items, "11110", "202601", None)[1]
    assert r["deal_amount"] is None
    assert r["exclu_use_ar"] is None
    assert r["floor"] == -1
    assert r["build_year"] is None


def test_to_rows_invalid_date_is_none():
    items, _ = api.parse_response(OK_XML.encode())
    assert api.to_rows(items, "11110", "202601", None)[1]["deal_date"] is None


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    """pageNo에 따라 정해진 XML을 돌려주는 가짜 세션."""

    def __init__(self, pages, status_code=200):
        self.pages = pages
        self.status_code = status_code
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append(params)
        return FakeResponse(self.pages[params["pageNo"]], self.status_code)


def page_xml(n_items, total):
    items = "".join(f"<item><aptNm>A{i}</aptNm></item>" for i in range(n_items))
    return (f"<response><header><resultCode>000</resultCode></header><body><items>{items}</items>"
            f"<totalCount>{total}</totalCount></body></response>").encode()


def test_fetch_job_pages_until_total(monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    session = FakeSession({1: page_xml(2, 3), 2: page_xml(1, 3)})
    calls = []
    items, total = api.fetch_job(session, "KEY", "11110", "202601", on_call=lambda: calls.append(1))
    assert total == 3 and len(items) == 3
    assert len(calls) == 2
    assert session.calls[0]["serviceKey"] == "KEY" and session.calls[1]["pageNo"] == 2


def test_fetch_page_http_error(monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    with pytest.raises(api.ApiError, match="HTTP 500"):
        api.fetch_page(FakeSession({1: b""}, status_code=500), "KEY", "11110", "202601", 1)


def test_fetch_page_network_error_hides_url(monkeypatch):
    import requests
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)

    class Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError("https://apis.data.go.kr/?serviceKey=SECRET")

    with pytest.raises(api.ApiError) as e:
        api.fetch_page(Boom(), "KEY", "11110", "202601", 1)
    assert "SECRET" not in str(e.value)


def test_fetch_page_on_call_can_stop(monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    session = FakeSession({1: page_xml(1, 1)})

    def stop():
        raise api.QuotaExceeded("한도")

    with pytest.raises(api.QuotaExceeded):
        api.fetch_page(session, "KEY", "11110", "202601", 1, on_call=stop)
    assert session.calls == []
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_api.py tests/test_codes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'collector.api'` (현재 루트의 `collector.py`가 모듈로 잡히면 `'collector' is not a package` 오류. 둘 다 실패로 본다)

- [ ] **Step 3: 구현**

`collector/__init__.py`는 빈 파일. 루트의 `collector.py`가 패키지와 이름이 겹치므로 **이 단계에서 `collector.py`를 `legacy_collector.py`로 이름을 바꿔 둔다**(Task 6에서 삭제). `app.py`는 Task 6에서 교체하므로 그 전까지 앱 실행은 하지 않는다.
```bash
git mv collector.py legacy_collector.py
```

`collector/codes.py`:
```python
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
```

`collector/api.py`:
```python
"""공공데이터포털 '아파트 매매 실거래가 상세자료' API 호출과 응답 파싱.

DB는 모른다. 호출 수 기록·한도 확인은 호출하는 쪽이 on_call 콜백으로 맡긴다.
"""
import re
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests

import settings

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/xml",
}
NUM_ROWS = 1000

# API 응답 필드 (기술문서 순서)
FIELDS = [
    "sggCd", "umdCd", "landCd", "bonbun", "bubun", "roadNm", "roadNmSggCd", "roadNmCd",
    "roadNmSeq", "roadNmbCd", "roadNmBonbun", "roadNmBubun", "umdNm", "aptNm", "jibun",
    "excluUseAr", "dealYear", "dealMonth", "dealDay", "dealAmount", "floor", "buildYear",
    "aptSeq", "cdealType", "cdealDay", "dealingGbn", "estateAgentSggNm", "rgstDate",
    "aptDong", "slerGbn", "buyerGbn", "landLeaseholdGbn",
]


def snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


COLUMNS = [snake(f) for f in FIELDS]                       # trades 테이블의 원본 필드 컬럼
CAMEL = {snake(f): f for f in FIELDS} | {"deal_date": "dealDate"}


class QuotaExceeded(Exception):
    """오늘 호출 한도 소진. 다음 날 수집 시작 시각까지 쉰다."""


class ApiError(Exception):
    """재시도할 수 있는 API·네트워크 오류."""


def to_int(value):
    text = str(value if value is not None else "").replace(",", "").strip()
    try:
        return int(text)
    except ValueError:
        return None


def to_float(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_response(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ApiError("XML 파싱 실패") from None

    # 게이트웨이 오류(키 미등록·한도 초과 등)는 형식이 다르다
    reason = root.findtext(".//returnReasonCode")
    if reason:
        if reason.strip() == "22":
            raise QuotaExceeded("공공데이터포털 일일 한도 초과(22)")
        raise ApiError(f"게이트웨이 오류 {reason.strip()}: {root.findtext('.//returnAuthMsg')}")
    code = (root.findtext(".//resultCode") or "").strip()
    if code == "22":
        raise QuotaExceeded("공공데이터포털 일일 한도 초과(22)")
    if code not in ("000", "00"):
        raise ApiError(f"API 오류 {code}: {root.findtext('.//resultMsg')}")

    items = [{c.tag: (c.text or "").strip() for c in item} for item in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or 0)


def fetch_page(session, key, lawd_cd, deal_ymd, page_no, num_rows=NUM_ROWS, on_call=None):
    """한 페이지를 받는다. on_call은 호출 직전에 불려 한도 확인·호출 수 기록을 맡는다."""
    if on_call:
        on_call()
    params = {"serviceKey": key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd,
              "pageNo": page_no, "numOfRows": num_rows}
    time.sleep(settings.REQUEST_INTERVAL)
    try:
        resp = session.get(API_URL, params=params, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        # 예외 메시지에 serviceKey가 든 URL이 섞일 수 있어 종류만 남긴다
        raise ApiError(f"네트워크 오류: {type(e).__name__}") from None
    if resp.status_code != 200:
        raise ApiError(f"HTTP {resp.status_code}")
    return parse_response(resp.content)


def fetch_job(session, key, lawd_cd, deal_ymd, on_call=None):
    """작업 하나(시군구 x 계약월)의 전체 페이지 -> (items, total_count)."""
    items, total = fetch_page(session, key, lawd_cd, deal_ymd, 1, on_call=on_call)
    page = 1
    while len(items) < total:
        page += 1
        more, total = fetch_page(session, key, lawd_cd, deal_ymd, page, on_call=on_call)
        if not more:
            break
        items += more
    return items, total


def to_rows(items, lawd_cd, deal_ymd, collected_at):
    """API 항목 -> trades 행(snake_case 키, 숫자·날짜 변환)."""
    rows = []
    for it in items:
        r = {snake(f): it.get(f, "") for f in FIELDS}
        r["deal_amount"] = to_int(r["deal_amount"])
        r["exclu_use_ar"] = to_float(r["exclu_use_ar"])
        r["floor"] = to_int(r["floor"])
        r["build_year"] = to_int(r["build_year"])
        try:
            r["deal_date"] = date(int(r["deal_year"]), int(r["deal_month"]), int(r["deal_day"]))
        except (TypeError, ValueError):
            r["deal_date"] = None
        r.update(lawd_cd=lawd_cd, deal_ymd=deal_ymd, collected_at=collected_at)
        rows.append(r)
    return rows
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_api.py tests/test_codes.py -v`
Expected: PASS (17 passed)

- [ ] **Step 5: Commit**

```bash
git add collector/ legacy_collector.py tests/test_api.py tests/test_codes.py
git commit -m "수집기 API 호출·파싱을 collector 패키지로 분리

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 호출 수 기록, 작업별 품질 집계, 작업 저장

**Files:**
- Create: `collector/usage.py`, `collector/quality.py`, `collector/store.py`, `tests/helpers.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: Task 1 `db`, `settings`; Task 2 `api.to_rows`, `api.COLUMNS`
- Produces:
  - `collector.usage.calls_today(conn) -> int`, `usage.count_call(conn)`, `usage.quota_left(conn) -> int`
  - `collector.quality.BLANK_FIELDS: list[str]`(snake), `quality.job_quality(rows, lawd_cd, deal_ymd) -> dict(q_blank: dict, q_dup, q_ymd_bad, q_sgg_bad, q_cancelled)`, `quality.write_quality(conn, lawd_cd, deal_ymd, q)`
  - `collector.store.TRADE_COLS: list[str]`, `store.AFTER_SAVE: list[callable(conn, lawd_cd, deal_ymd, rows)]`(같은 트랜잭션 안에서 실행. 계획 2·3이 등록)
  - `store.ensure_jobs(conn, codes: list[str], months: list[str])`
  - `store.save_job(conn, lawd_cd, deal_ymd, items, total) -> (status: "done"|"incomplete", n: int)`
  - `store.mark_error(conn, lawd_cd, deal_ymd, msg)`
  - `tests.helpers.item(**overrides) -> dict`(API 항목), `helpers.add_job(conn, lawd_cd, deal_ymd, **cols)`

- [ ] **Step 1: 테스트 도우미 작성**

`tests/helpers.py`:
```python
"""테스트용 API 항목·작업 생성 도우미."""


def item(**overrides):
    """국토부 API 응답 항목 하나(camelCase). 필요한 값만 덮어쓴다."""
    base = dict(
        sggCd="11110", umdCd="10100", umdNm="청운동", jibun="1", aptNm="테스트아파트", aptSeq="11110-1",
        roadNm="자하문로", roadNmSggCd="11110", roadNmCd="4100135", roadNmbCd="0",
        roadNmBonbun="00001", roadNmBubun="00000",
        dealYear="2026", dealMonth="1", dealDay="5", dealAmount="84,000", excluUseAr="84.97",
        floor="12", buildYear="2005", cdealType="",
    )
    base.update(overrides)
    return base


def add_job(conn, lawd_cd, deal_ymd, **cols):
    conn.execute("INSERT INTO jobs(lawd_cd, deal_ymd) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                 (lawd_cd, deal_ymd))
    if cols:
        sets = ", ".join(f"{k} = %({k})s" for k in cols)
        conn.execute(f"UPDATE jobs SET {sets} WHERE lawd_cd = %(lawd_cd)s AND deal_ymd = %(deal_ymd)s",
                     {**cols, "lawd_cd": lawd_cd, "deal_ymd": deal_ymd})
```

- [ ] **Step 2: 테스트 작성**

`tests/test_store.py`:
```python
from datetime import datetime, timedelta

import pytest

import settings
from collector import store, usage
from tests.helpers import add_job, item

NOW = datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST)


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: NOW)


@pytest.fixture(autouse=True)
def no_hooks(monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [])


def test_usage_counts_per_day(pg):
    with pg.connection() as conn:
        assert usage.calls_today(conn) == 0
        usage.count_call(conn)
        usage.count_call(conn)
        assert usage.calls_today(conn) == 2
        assert usage.quota_left(conn) == settings.DAILY_LIMIT - 2


def test_ensure_jobs_idempotent(pg):
    with pg.connection() as conn:
        store.ensure_jobs(conn, ["11110", "11140"], ["202601", "202602"])
        store.ensure_jobs(conn, ["11110", "11140"], ["202601", "202602"])
        n = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'pending'").fetchone()["n"]
    assert n == 4


def test_save_job_inserts_and_marks_done(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        status, n = store.save_job(conn, "11110", "202601", [item(), item(aptSeq="11110-2")], 2)
        job = conn.execute("SELECT * FROM jobs").fetchone()
        rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
    assert (status, n) == ("done", 2)
    assert job["status"] == "done" and job["total_count"] == 2 and job["stored_count"] == 2
    assert job["attempts"] == 1 and job["error"] is None and job["next_try_at"] is None
    assert job["fetched_at"] == datetime(2026, 10, 3, 7, 0)
    assert rows[0]["deal_amount"] == 84000 and rows[0]["apt_seq"] == "11110-1"
    assert rows[0]["collected_at"] == datetime(2026, 10, 3, 7, 0)


def test_save_job_replaces_and_records_change(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        store.save_job(conn, "11110", "202601", [item(), item()], 2)
        store.save_job(conn, "11110", "202601", [item()], 1)
        n = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        change = conn.execute('SELECT lawd_cd, deal_ymd, "before", "after" FROM changes').fetchone()
    assert n == 1
    assert change == {"lawd_cd": "11110", "deal_ymd": "202601", "before": 2, "after": 1}


def test_save_job_incomplete_schedules_retry(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        status, _ = store.save_job(conn, "11110", "202601", [item()], 3)
        job = conn.execute("SELECT * FROM jobs").fetchone()
    assert status == "incomplete"
    assert job["error"] == "저장 1건 / 전체 3건"
    assert job["next_try_at"] == datetime(2026, 10, 3, 8, 0)


def test_save_job_quality_counts(pg):
    items = [
        item(),
        item(),                                    # 완전 중복
        item(dealMonth="13", aptNm=""),            # 날짜 불량 + 단지명 빈 값
        item(sggCd="99999", cdealType="O"),        # 코드 불일치 + 해제
    ]
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        store.save_job(conn, "11110", "202601", items, 4)
        job = conn.execute("SELECT * FROM jobs").fetchone()
    assert job["q_dup"] == 1
    assert job["q_ymd_bad"] == 1
    assert job["q_sgg_bad"] == 1
    assert job["q_cancelled"] == 1
    assert job["q_blank"] == {"apt_nm": 1, "deal_date": 1}


def test_after_save_hook_runs_in_same_transaction(pg, monkeypatch):
    seen = []

    def hook(conn, lawd_cd, deal_ymd, rows):
        seen.append((lawd_cd, deal_ymd, len(rows)))
        raise RuntimeError("후처리 실패")

    monkeypatch.setattr(store, "AFTER_SAVE", [hook])
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        with pytest.raises(RuntimeError):
            store.save_job(conn, "11110", "202601", [item()], 1)
        n = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        status = conn.execute("SELECT status FROM jobs").fetchone()["status"]
    assert seen == [("11110", "202601", 1)]
    assert n == 0 and status == "pending"


def test_mark_error_backoff_keeps_done(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601", attempts=2)
        add_job(conn, "11140", "202601", status="done", attempts=0)
        store.mark_error(conn, "11110", "202601", "HTTP 500")
        store.mark_error(conn, "11140", "202601", "HTTP 500")
        a, b = conn.execute("SELECT * FROM jobs ORDER BY lawd_cd").fetchall()
    assert a["status"] == "error" and a["attempts"] == 3 and a["error"] == "HTTP 500"
    assert a["next_try_at"] == datetime(2026, 10, 3, 7, 0) + timedelta(minutes=8)
    assert b["status"] == "done" and b["attempts"] == 1
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'collector.store'`

- [ ] **Step 4: 구현**

`collector/usage.py`:
```python
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
```

`collector/quality.py` (이 Task에서는 작업별 집계만. 보고 함수는 Task 5에서 추가):
```python
"""누락 점검: 작업별 품질 집계와 진행 상황·누락 점검 보고."""
from psycopg.types.json import Jsonb

from collector import api

# 누락 점검 대상: 위치·가격 분석에 꼭 필요한 필드
KEY_FIELDS = ["apt_nm", "umd_nm", "jibun", "road_nm", "exclu_use_ar", "floor", "build_year", "apt_seq"]
BLANK_FIELDS = KEY_FIELDS + ["deal_amount", "deal_date"]


def job_quality(rows, lawd_cd, deal_ymd):
    """작업 하나의 누락 점검 집계: 필드별 빈 값, 완전 중복, 계약월·코드 불일치, 해제 건수."""
    blank = {f: sum(1 for r in rows if r.get(f) is None or str(r.get(f)).strip() == "")
             for f in BLANK_FIELDS}
    keys = [tuple(r.get(c) for c in api.COLUMNS) for r in rows]
    return dict(
        q_blank={f: n for f, n in blank.items() if n},
        q_dup=len(keys) - len(set(keys)),
        q_ymd_bad=sum(1 for r in rows if not r.get("deal_date")
                      or r["deal_date"].strftime("%Y%m") != deal_ymd),
        q_sgg_bad=sum(1 for r in rows if (r.get("sgg_cd") or "") != lawd_cd),
        q_cancelled=sum(1 for r in rows if (r.get("cdeal_type") or "").strip()),
    )


def write_quality(conn, lawd_cd, deal_ymd, q):
    conn.execute(
        "UPDATE jobs SET q_blank = %(q_blank)s, q_dup = %(q_dup)s, q_ymd_bad = %(q_ymd_bad)s, "
        "q_sgg_bad = %(q_sgg_bad)s, q_cancelled = %(q_cancelled)s "
        "WHERE lawd_cd = %(lawd_cd)s AND deal_ymd = %(deal_ymd)s",
        {**q, "q_blank": Jsonb(q["q_blank"]), "lawd_cd": lawd_cd, "deal_ymd": deal_ymd},
    )
```

`collector/store.py`:
```python
"""수집 결과 저장: 작업 생성, 작업 단위 교체 저장, 오류 기록."""
from datetime import timedelta

import settings
from collector import api, quality

TRADE_COLS = ["lawd_cd", "deal_ymd", *api.COLUMNS, "deal_date", "collected_at"]

# 작업 하나를 저장한 같은 트랜잭션 안에서 불리는 후처리(단지 등록·집계 대기열 등).
# 함수 형태: hook(conn, lawd_cd, deal_ymd, rows). 예외를 내면 저장 전체가 취소된다.
AFTER_SAVE = []


def ensure_jobs(conn, codes, months):
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO jobs(lawd_cd, deal_ymd) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            [(c, ym) for ym in months for c in codes],
        )


def save_job(conn, lawd_cd, deal_ymd, items, total):
    """작업 하나를 통째로 교체 저장한다. 삭제·삽입·작업 상태 갱신·후처리가 한 트랜잭션이다."""
    stored_at = settings.now_ts()
    rows = api.to_rows(items, lawd_cd, deal_ymd, stored_at)
    q = quality.job_quality(rows, lawd_cd, deal_ymd)
    # 누락 점검 1: API가 알려준 전체 건수와 실제 저장 건수 비교
    status = "done" if len(rows) == total else "incomplete"
    with conn.transaction():
        prev = conn.execute(
            "SELECT stored_count FROM jobs WHERE lawd_cd = %s AND deal_ymd = %s FOR UPDATE",
            (lawd_cd, deal_ymd),
        ).fetchone()
        conn.execute("DELETE FROM trades WHERE lawd_cd = %s AND deal_ymd = %s", (lawd_cd, deal_ymd))
        with conn.cursor() as cur, cur.copy(f"COPY trades ({', '.join(TRADE_COLS)}) FROM STDIN") as copy:
            for r in rows:
                copy.write_row([r[c] for c in TRADE_COLS])
        conn.execute(
            "UPDATE jobs SET status = %(status)s, total_count = %(total)s, stored_count = %(n)s, "
            "fetched_at = %(at)s, checked_at = %(at)s, attempts = attempts + 1, "
            "error = %(error)s, next_try_at = %(next)s "
            "WHERE lawd_cd = %(lawd_cd)s AND deal_ymd = %(deal_ymd)s",
            dict(status=status, total=total, n=len(rows), at=stored_at,
                 error=None if status == "done" else f"저장 {len(rows)}건 / 전체 {total}건",
                 next=None if status == "done" else stored_at + timedelta(hours=1),
                 lawd_cd=lawd_cd, deal_ymd=deal_ymd),
        )
        quality.write_quality(conn, lawd_cd, deal_ymd, q)
        if prev and prev["stored_count"] is not None and prev["stored_count"] != len(rows):
            conn.execute(
                'INSERT INTO changes(at, lawd_cd, deal_ymd, "before", "after") VALUES (%s, %s, %s, %s, %s)',
                (stored_at, lawd_cd, deal_ymd, prev["stored_count"], len(rows)),
            )
        for hook in AFTER_SAVE:
            hook(conn, lawd_cd, deal_ymd, rows)
    return status, len(rows)


def mark_error(conn, lawd_cd, deal_ymd, msg):
    with conn.transaction():
        row = conn.execute(
            "SELECT attempts FROM jobs WHERE lawd_cd = %s AND deal_ymd = %s FOR UPDATE",
            (lawd_cd, deal_ymd),
        ).fetchone()
        attempts = (row["attempts"] or 0) + 1
        wait = min(2 ** attempts, 24 * 60)  # 분 단위 지수 백오프, 최대 하루
        conn.execute(
            "UPDATE jobs SET status = CASE WHEN status = 'done' THEN 'done' ELSE 'error' END, "
            "attempts = %s, error = %s, next_try_at = %s WHERE lawd_cd = %s AND deal_ymd = %s",
            (attempts, msg, settings.now_ts() + timedelta(minutes=wait), lawd_cd, deal_ymd),
        )
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_store.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
git add collector/usage.py collector/quality.py collector/store.py tests/helpers.py tests/test_store.py
git commit -m "작업 저장·오류 기록·호출 수 기록을 Postgres로 이식

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 작업 선택과 수집 배치

**Files:**
- Create: `collector/jobs.py`, `tests/test_jobs.py`
- Modify: `collector/quality.py` (용량 함수 추가)

**Interfaces:**
- Consumes: Task 2 `api.fetch_page`, `api.fetch_job`, `api.QuotaExceeded`, `api.ApiError`, `codes.*`; Task 3 `store.*`, `usage.*`
- Produces:
  - `collector.quality.db_size(conn) -> int`(바이트), `quality.storage_pct() -> float`
  - `collector.jobs.state: dict`(running, current, last_error, paused_until, backfill_stopped)
  - `jobs.ensure_jobs()`, `jobs.daily_start(day_offset=0) -> datetime(KST)`, `jobs.last_refresh_time() -> datetime(KST)`
  - `jobs.next_jobs(conn, limit, allow_backfill=True) -> list[dict(lawd_cd, deal_ymd, mode: "fetch"|"check", pri)]`
  - `jobs.check_job(session, key, conn, lawd_cd, deal_ymd, on_call=None)`
  - `jobs.run_batch(max_jobs=20)`
  - `jobs.reset_state()`(테스트용)

- [ ] **Step 1: 테스트 작성**

`tests/test_jobs.py`:
```python
from datetime import datetime

import pytest

import settings
from collector import api, codes, jobs, store
from tests.helpers import add_job, item

AT_7AM = datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST)


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: AT_7AM)
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    jobs.reset_state()
    yield
    jobs.reset_state()


def ts(s):
    return datetime.fromisoformat(s)


def test_next_jobs_priority_order(pg):
    # 기준: 2026-10-03 07:00, 최근 3개월 = 202608~, 재확인 구간 = 202508~202607
    with pg.connection() as conn:
        add_job(conn, "11110", "202610")                                                    # 1 최근 미수집
        add_job(conn, "11110", "202609", status="done", fetched_at=ts("2026-10-02 06:10"),
                checked_at=ts("2026-10-02 06:10"))                                          # 2 오늘 재수집
        add_job(conn, "11110", "202001", status="error", next_try_at=ts("2026-10-03 06:59"))  # 3 재시도
        add_job(conn, "11110", "200601")                                                    # 4 과거 미수집
        add_job(conn, "11110", "202508", status="done", fetched_at=ts("2026-09-01 06:00"),
                checked_at=ts("2026-09-01 06:00"))                                          # 5 1년 내 재확인
        add_job(conn, "11110", "201001", status="done", fetched_at=ts("2026-03-01 06:00"),
                checked_at=ts("2026-03-01 06:00"))                                          # 6 오래된 달 재확인
        add_job(conn, "11140", "202609", status="done", fetched_at=ts("2026-10-03 06:30"),
                checked_at=ts("2026-10-03 06:30"))                                          # 오늘 이미 받음 → 제외
        add_job(conn, "11140", "202001", status="error", next_try_at=ts("2026-10-03 08:00"))  # 아직 대기 → 제외
        picked = jobs.next_jobs(conn, 10)
        no_backfill = jobs.next_jobs(conn, 10, allow_backfill=False)
    assert [(j["deal_ymd"], j["mode"], j["pri"]) for j in picked] == [
        ("202610", "fetch", 1), ("202609", "fetch", 2), ("202001", "fetch", 3),
        ("200601", "fetch", 4), ("202508", "check", 5), ("201001", "check", 6),
    ]
    assert "200601" not in [j["deal_ymd"] for j in no_backfill]


def test_daily_start_and_last_refresh(monkeypatch):
    assert jobs.daily_start() == datetime(2026, 10, 3, 6, 0, tzinfo=settings.KST)
    assert jobs.last_refresh_time() == datetime(2026, 10, 3, 6, 0, tzinfo=settings.KST)
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 5, 0, tzinfo=settings.KST))
    assert jobs.last_refresh_time() == datetime(2026, 10, 2, 6, 0, tzinfo=settings.KST)


def fake_fetch_page(pages_by_job):
    """(lawd_cd, deal_ymd) -> 항목 목록. 한 페이지에 모두 돌려준다."""
    def fetch_page(session, key, lawd_cd, deal_ymd, page_no, num_rows=1000, on_call=None):
        if on_call:
            on_call()
        items = pages_by_job.get((lawd_cd, deal_ymd), [])
        return (items[:num_rows], len(items))
    return fetch_page


def test_check_job_marks_changed_month_pending(pg, monkeypatch):
    monkeypatch.setattr(api, "fetch_page", fake_fetch_page({("11110", "202001"): [item(), item()]}))
    with pg.connection() as conn:
        add_job(conn, "11110", "202001", status="done", stored_count=1, checked_at=ts("2026-01-01 00:00"))
        add_job(conn, "11140", "202001", status="done", stored_count=0, checked_at=ts("2026-01-01 00:00"))
        jobs.check_job(None, "KEY", conn, "11110", "202001")
        jobs.check_job(None, "KEY", conn, "11140", "202001")
        a, b = conn.execute("SELECT * FROM jobs ORDER BY lawd_cd").fetchall()
    assert a["status"] == "pending" and a["error"] == "재확인 시 건수 변동 1→2"
    assert b["status"] == "done" and b["checked_at"] == datetime(2026, 10, 3, 7, 0)


def test_run_batch_waits_before_daily_start(pg, monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 5, 0, tzinfo=settings.KST))
    jobs.run_batch(max_jobs=3)
    with pg.connection() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"] == 0


def test_run_batch_collects_recent_first(pg, monkeypatch):
    monkeypatch.setattr(settings, "START_YMD", "202609")
    monkeypatch.setattr(api, "fetch_page", fake_fetch_page({("11110", "202609"): [item(dealMonth="9")]}))
    jobs.run_batch(max_jobs=3)
    with pg.connection() as conn:
        done = conn.execute("SELECT lawd_cd, deal_ymd FROM jobs WHERE status = 'done' "
                            "ORDER BY deal_ymd, lawd_cd").fetchall()
        n_jobs = conn.execute("SELECT COUNT(*) AS n FROM jobs").fetchone()["n"]
        trades = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        calls = conn.execute("SELECT calls FROM api_usage").fetchone()["calls"]
    n_codes = len(codes.load_codes())
    assert n_jobs == 2 * n_codes                     # 202609, 202610 x 전 시군구
    assert len(done) == 3 and done[0]["deal_ymd"] == "202609"
    assert trades == 1 and calls == 3
    assert jobs.state["running"] is False


def test_run_batch_pauses_when_quota_runs_out(pg, monkeypatch):
    monkeypatch.setattr(settings, "START_YMD", "202610")
    monkeypatch.setattr(settings, "DAILY_LIMIT", 2)
    # 첫 작업은 2페이지(호출 2번)로 끝나고, 두 번째 작업의 첫 호출에서 한도에 걸린다
    many = [item(aptSeq=f"s{i}") for i in range(3)]

    def fetch_page(session, key, lawd_cd, deal_ymd, page_no, num_rows=1000, on_call=None):
        if on_call:
            on_call()
        items = many if lawd_cd == "11110" else [item()]
        chunk = items[(page_no - 1) * 2: page_no * 2]
        return chunk, len(items)

    monkeypatch.setattr(api, "fetch_page", fetch_page)
    jobs.run_batch(max_jobs=5)
    with pg.connection() as conn:
        done = conn.execute("SELECT lawd_cd FROM jobs WHERE status = 'done'").fetchall()
        trades = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
    assert [d["lawd_cd"] for d in done] == ["11110"]
    assert trades == 3                                   # 두 번째 작업은 부분 저장되지 않음
    assert jobs.state["paused_until"] == "2026-10-04 06:00:00"


def test_run_batch_records_api_error(pg, monkeypatch):
    monkeypatch.setattr(settings, "START_YMD", "202610")

    def fetch_page(*a, on_call=None, **k):
        if on_call:
            on_call()
        raise api.ApiError("HTTP 500")

    monkeypatch.setattr(api, "fetch_page", fetch_page)
    jobs.run_batch(max_jobs=1)
    with pg.connection() as conn:
        job = conn.execute("SELECT * FROM jobs WHERE status = 'error'").fetchone()
    assert job["error"] == "HTTP 500" and job["attempts"] == 1
    assert "HTTP 500" in jobs.state["last_error"]


def test_storage_pct_uses_db_size(pg, monkeypatch):
    from collector import quality
    monkeypatch.setattr(settings, "DB_LIMIT_MB", 1)
    assert quality.storage_pct() > 0
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_jobs.py -v`
Expected: FAIL — `ImportError: cannot import name 'jobs' from 'collector'`

- [ ] **Step 3: 용량 함수 추가**

`collector/quality.py` 맨 위 import에 `import db`, `import settings`를 추가하고, 파일 끝에 덧붙인다:
```python
def db_size(conn):
    return conn.execute("SELECT pg_database_size(current_database()) AS n").fetchone()["n"]


def storage_pct():
    with db.connection() as conn:
        return 100 * db_size(conn) / (settings.DB_LIMIT_MB * 1024 * 1024)
```
(import 블록은 `from psycopg.types.json import Jsonb` 다음 줄에 빈 줄을 두고 `import db`, `import settings`, `from collector import api` 순서.)

- [ ] **Step 4: 작업 선택·배치 구현**

`collector/jobs.py`:
```python
"""무엇을 언제 받을지 고르고, 스케줄러가 부르는 수집 배치를 돌린다."""
import logging
import threading
from datetime import timedelta

import requests

import db
import settings
from collector import api, codes, quality, store, usage

log = logging.getLogger(__name__)

_lock = threading.Lock()
state = {}
_ensured = {}


def reset_state():
    state.clear()
    state.update(running=False, current=None, last_error=None, paused_until=None, backfill_stopped=None)
    _ensured.clear()
    _ensured.update(month=None, quality=False)


reset_state()


def ensure_jobs():
    """시작월~이번 달 x 전 시군구 작업이 없으면 만든다(달이 바뀌면 자동 추가)."""
    this_month = settings.now_kst().strftime("%Y%m")
    if _ensured["month"] == this_month:
        return
    months = codes.month_range(settings.START_YMD, this_month)
    with db.connection() as conn:
        store.ensure_jobs(conn, codes.load_codes()["LAWD_CD"].tolist(), months)
    _ensured["month"] = this_month


def daily_start(day_offset=0):
    """그날의 수집 시작 시각(REFRESH_AT, KST)."""
    hour, minute = map(int, settings.REFRESH_AT.split(":"))
    at = settings.now_kst().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at + timedelta(days=day_offset)


def last_refresh_time():
    """가장 최근에 지난 매일 갱신 시각. 이 시각 전에 받은 최근 달은 다시 받는다."""
    at = daily_start()
    return at if settings.now_kst() >= at else at - timedelta(days=1)


def _naive(dt):
    return dt.replace(tzinfo=None, microsecond=0)


def next_jobs(conn, limit, allow_backfill=True):
    """우선순위
    ① 최근 N개월 미수집(새 달 포함)  ② 최근 N개월 매일 재수집  ③ 오류·불일치 재시도
    ④ 과거 자료 미수집(오래된 달부터, 용량 여유가 있을 때만)
    ⑤ 지난 1년 건수 재확인(RECHECK_DAYS 주기)  ⑥ 그보다 오래된 달 건수 재확인(OLD_RECHECK_DAYS 주기)
    """
    now = settings.now_kst()
    params = dict(
        now=_naive(now),
        recent=codes.months_ago(settings.REFRESH_MONTHS - 1),
        check_from=codes.months_ago(settings.REFRESH_MONTHS - 1 + settings.RECHECK_MONTHS),
        refresh=_naive(last_refresh_time()),
        recheck=_naive(now - timedelta(days=settings.RECHECK_DAYS)),
        old_recheck=_naive(now - timedelta(days=settings.OLD_RECHECK_DAYS)),
        backfill=allow_backfill,
        limit=limit,
    )
    sql = """
    SELECT lawd_cd, deal_ymd, 'fetch' AS mode, 1 AS pri FROM jobs
     WHERE status = 'pending' AND deal_ymd >= %(recent)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 2 FROM jobs
     WHERE status = 'done' AND deal_ymd >= %(recent)s AND fetched_at < %(refresh)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 3 FROM jobs
     WHERE status IN ('error', 'incomplete') AND (next_try_at IS NULL OR next_try_at <= %(now)s)
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 4 FROM jobs
     WHERE status = 'pending' AND deal_ymd < %(recent)s AND %(backfill)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'check', 5 FROM jobs
     WHERE status = 'done' AND deal_ymd < %(recent)s AND deal_ymd >= %(check_from)s
       AND checked_at <= %(recheck)s
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'check', 6 FROM jobs
     WHERE status = 'done' AND deal_ymd < %(check_from)s AND checked_at <= %(old_recheck)s
    ORDER BY pri, deal_ymd, lawd_cd LIMIT %(limit)s
    """
    return conn.execute(sql, params).fetchall()


def check_job(session, key, conn, lawd_cd, deal_ymd, on_call=None):
    """누락 점검: 지난 달은 1건만 요청해 전체 건수만 비교, 달라졌으면 다시 받도록 표시."""
    _, total = api.fetch_page(session, key, lawd_cd, deal_ymd, 1, num_rows=1, on_call=on_call)
    row = conn.execute("SELECT stored_count FROM jobs WHERE lawd_cd = %s AND deal_ymd = %s",
                       (lawd_cd, deal_ymd)).fetchone()
    if row["stored_count"] != total:
        conn.execute("UPDATE jobs SET status = 'pending', error = %s WHERE lawd_cd = %s AND deal_ymd = %s",
                     (f"재확인 시 건수 변동 {row['stored_count']}→{total}", lawd_cd, deal_ymd))
    else:
        conn.execute("UPDATE jobs SET checked_at = %s WHERE lawd_cd = %s AND deal_ymd = %s",
                     (settings.now_ts(), lawd_cd, deal_ymd))


def run_batch(max_jobs=20):
    """스케줄러가 1분마다 호출. 매일 REFRESH_AT부터 하루 한도까지 수집하고, 0시~REFRESH_AT에는 쉰다."""
    if not _lock.acquire(blocking=False):
        return
    state["running"] = True
    try:
        if state["paused_until"] and settings.now_str() < state["paused_until"]:
            return
        state["paused_until"] = None
        if settings.now_kst() < daily_start():
            return  # 오늘 수집 시작 전
        key = settings.service_key()
        ensure_jobs()
        if not _ensured["quality"]:
            with db.connection() as conn:
                quality.backfill_quality(conn)
            _ensured["quality"] = True

        pct = quality.storage_pct()
        allow_backfill = pct < settings.STORAGE_STOP_PCT
        state["backfill_stopped"] = None if allow_backfill else (
            f"DB 사용률 {pct:.1f}% ≥ {settings.STORAGE_STOP_PCT:g}% → 과거 자료 수집 중단(최근 자료는 계속)")

        with db.connection() as conn, requests.Session() as session:
            def on_call():
                if usage.quota_left(conn) <= 0:
                    raise api.QuotaExceeded("오늘 호출 상한 도달")
                usage.count_call(conn)

            for job in next_jobs(conn, max_jobs, allow_backfill):
                lawd_cd, deal_ymd, mode = job["lawd_cd"], job["deal_ymd"], job["mode"]
                state["current"] = f"{deal_ymd} {lawd_cd} ({mode})"
                try:
                    if mode == "check":
                        check_job(session, key, conn, lawd_cd, deal_ymd, on_call=on_call)
                    else:
                        items, total = api.fetch_job(session, key, lawd_cd, deal_ymd, on_call=on_call)
                        status, n = store.save_job(conn, lawd_cd, deal_ymd, items, total)
                        log.info("%s %s: %d건 (%s)", deal_ymd, lawd_cd, n, status)
                except api.ApiError as e:
                    log.warning("%s %s 실패: %s", deal_ymd, lawd_cd, e)
                    store.mark_error(conn, lawd_cd, deal_ymd, str(e))
                    state["last_error"] = f"{settings.now_str()} {deal_ymd} {lawd_cd}: {e}"
    except api.QuotaExceeded as e:
        # 다음 날 수집 시작 시각까지 쉰다
        resume = daily_start(1).strftime("%Y-%m-%d %H:%M:%S")
        state["paused_until"] = resume
        log.info("%s → %s까지 대기", e, resume)
    except Exception as e:  # noqa: BLE001
        state["last_error"] = f"{settings.now_str()} {type(e).__name__}: {e}"
        log.exception("수집 배치 실패")
    finally:
        state["running"] = False
        state["current"] = None
        _lock.release()
```

`run_batch`는 `quality.backfill_quality(conn)`를 부른다. 이 함수는 Task 5에서 만들므로, 이 Task에서는 `collector/quality.py` 끝에 다음을 먼저 추가한다(Task 5에서 테스트를 붙인다):
```python
def backfill_quality(conn):
    """품질 집계 열이 비어 있는 완료 작업을 채운다(이전 버전에서 받은 자료)."""
    todo = conn.execute(
        "SELECT lawd_cd, deal_ymd FROM jobs WHERE status = 'done' AND q_blank IS NULL").fetchall()
    for job in todo:
        rows = conn.execute("SELECT * FROM trades WHERE lawd_cd = %s AND deal_ymd = %s",
                            (job["lawd_cd"], job["deal_ymd"])).fetchall()
        write_quality(conn, job["lawd_cd"], job["deal_ymd"],
                      job_quality(rows, job["lawd_cd"], job["deal_ymd"]))
    return len(todo)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_jobs.py -v`
Expected: PASS (8 passed)

- [ ] **Step 6: Commit**

```bash
git add collector/jobs.py collector/quality.py tests/test_jobs.py
git commit -m "작업 선택·수집 배치를 Postgres로 이식

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 진행 상황·용량·누락 점검 보고

**Files:**
- Modify: `collector/quality.py`, `collector/jobs.py`
- Create: `tests/test_quality.py`

**Interfaces:**
- Consumes: Task 3·4
- Produces:
  - `quality.storage(conn, trades) -> dict(total_bytes, limit_bytes, stop_pct, pct, bytes_per_trade)`
  - `quality.progress() -> dict(by_status, by_month, by_year, trades, last_fetched, calls_today, daily_limit, total_jobs, eta_days, refresh_at, storage)`
  - `quality.quality_report() -> dict(total, problems, zero_codes, sgg_mismatch, blanks, ymd_mismatch, duplicates, cancelled, changes)` — `blanks[].field`는 API 필드명(camelCase)
  - `jobs.progress() -> dict` = `quality.progress()` + `jobs.state`

- [ ] **Step 1: 테스트 작성**

`tests/test_quality.py`:
```python
from datetime import datetime

import pytest

import settings
from collector import jobs, quality, store
from tests.helpers import add_job, item


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    jobs.reset_state()


def seed(conn):
    add_job(conn, "11110", "202509")
    add_job(conn, "11110", "202610")
    add_job(conn, "11140", "202610")
    add_job(conn, "11170", "202609", status="done", stored_count=0)
    add_job(conn, "11170", "202610", status="done", stored_count=0)
    store.save_job(conn, "11110", "202509", [item(dealYear="2025", dealMonth="9")], 1)
    store.save_job(conn, "11110", "202610", [item(dealMonth="10", aptNm=""), item(dealMonth="10")], 2)
    store.save_job(conn, "11140", "202610", [item(sggCd="11140", dealMonth="10")], 5)  # incomplete


def test_progress(pg):
    with pg.connection() as conn:
        seed(conn)
    p = quality.progress()
    assert p["by_status"] == {"done": 4, "incomplete": 1}
    assert p["trades"] == 4
    assert p["total_jobs"] == 5
    assert [y["year"] for y in p["by_year"]] == ["2025", "2026"]
    assert p["by_year"][1]["jobs"] == 4 and p["by_year"][1]["bad"] == 1
    assert p["last_fetched"] == datetime(2026, 10, 3, 7, 0)
    assert p["storage"]["total_bytes"] > 0 and p["storage"]["bytes_per_trade"] > 0
    assert p["eta_days"] == 0


def test_jobs_progress_includes_state(pg):
    with pg.connection() as conn:
        seed(conn)
    jobs.state["last_error"] = "boom"
    p = jobs.progress()
    assert p["last_error"] == "boom" and p["trades"] == 4


def test_quality_report(pg):
    with pg.connection() as conn:
        seed(conn)
    q = quality.quality_report()
    assert [r["lawd_cd"] for r in q["problems"]] == ["11140"]
    assert q["problems"][0]["name"] == "서울특별시 중구"
    assert [r["lawd_cd"] for r in q["zero_codes"]] == ["11170"]
    assert q["sgg_mismatch"] == []
    blanks = {b["field"]: b["missing"] for b in q["blanks"]}
    assert blanks["aptNm"] == 1 and blanks["dealAmount"] == 0
    assert q["total"] == 3                        # done 작업의 저장 건수 합
    assert q["changes"] == []


def test_backfill_quality_fills_missing(pg):
    with pg.connection() as conn:
        seed(conn)
        conn.execute("UPDATE jobs SET q_blank = NULL, q_dup = NULL")
        n = quality.backfill_quality(conn)
        job = conn.execute("SELECT q_blank FROM jobs WHERE lawd_cd = '11110' AND deal_ymd = '202610'").fetchone()
    assert n == 4
    assert job["q_blank"] == {"apt_nm": 1}
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_quality.py -v`
Expected: FAIL — `AttributeError: module 'collector.quality' has no attribute 'progress'`

- [ ] **Step 3: 구현**

`collector/quality.py`의 import 블록에 `from collector import api, codes, usage`(기존 `from collector import api`를 교체)를 쓰고, 파일 끝에 덧붙인다:
```python
def storage(conn, trades):
    """Postgres DB 크기와 기준 용량(DB_LIMIT_MB) 대비 사용률."""
    total = db_size(conn)
    limit = settings.DB_LIMIT_MB * 1024 * 1024
    return dict(total_bytes=total, limit_bytes=limit, stop_pct=settings.STORAGE_STOP_PCT,
                pct=round(100 * total / limit, 1),
                bytes_per_trade=round(total / trades) if trades else None)


def progress():
    """진행 상황. 거래 건수는 jobs의 저장 건수 합으로 계산한다(대용량에서 COUNT(*) 피함)."""
    with db.connection() as conn:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")}
        by_month = conn.execute("""
            SELECT deal_ymd, COUNT(*) AS jobs,
                   COUNT(*) FILTER (WHERE status = 'done') AS done,
                   COUNT(*) FILTER (WHERE status IN ('error', 'incomplete')) AS bad,
                   COALESCE(SUM(stored_count), 0) AS trades, MAX(fetched_at) AS last_fetched
              FROM jobs GROUP BY deal_ymd ORDER BY deal_ymd""").fetchall()
        last = conn.execute("SELECT MAX(fetched_at) AS t FROM jobs").fetchone()["t"]
        used = usage.calls_today(conn)
        trades = sum(m["trades"] for m in by_month)
        st = storage(conn, trades)
    by_year = {}
    for m in by_month:
        y = by_year.setdefault(m["deal_ymd"][:4], dict(year=m["deal_ymd"][:4], jobs=0, done=0, bad=0,
                                                         trades=0, last_fetched=None))
        for k in ("jobs", "done", "bad", "trades"):
            y[k] += m[k] or 0
        y["last_fetched"] = max(filter(None, [y["last_fetched"], m["last_fetched"]]), default=None)
    total_jobs = sum(by_status.values())
    pending = by_status.get("pending", 0)
    per_day = max(settings.DAILY_LIMIT - settings.REFRESH_MONTHS * len(codes.load_codes()), 1)
    return dict(by_status=by_status, by_month=by_month, by_year=list(by_year.values()), trades=trades,
                last_fetched=last, calls_today=used, daily_limit=settings.DAILY_LIMIT,
                total_jobs=total_jobs, eta_days=-(-pending // per_day) if pending else 0,
                refresh_at=settings.REFRESH_AT, storage=st)


def quality_report():
    """누락 점검 결과 모음. 모두 jobs의 작업별 집계에서 계산한다."""
    names = codes.names()
    with db.connection() as conn:
        # 1) 아직 못 받았거나 건수가 안 맞는 작업
        problems = conn.execute("""
            SELECT deal_ymd, lawd_cd, status, total_count, stored_count, attempts, error, next_try_at
              FROM jobs WHERE status IN ('error', 'incomplete')
             ORDER BY deal_ymd, lawd_cd LIMIT 500""").fetchall()
        # 2) 받은 달이 모두 0건인 시군구 → 코드 변경(행정구역 개편) 의심
        zero_codes = conn.execute("""
            SELECT lawd_cd, COUNT(*) AS months, MAX(deal_ymd) AS last_ymd FROM jobs
             WHERE status = 'done' GROUP BY lawd_cd
            HAVING SUM(stored_count) = 0 AND COUNT(*) >= 2""").fetchall()
        # 3) 응답의 sggCd가 요청 코드와 다른 작업(코드 체계 변화 감지)
        sgg_mismatch = conn.execute("""
            SELECT lawd_cd, deal_ymd, q_sgg_bad AS n FROM jobs WHERE q_sgg_bad > 0
             ORDER BY deal_ymd, lawd_cd LIMIT 500""").fetchall()
        agg = conn.execute("""
            SELECT COALESCE(SUM(stored_count), 0) AS total, COALESCE(SUM(q_ymd_bad), 0) AS ymd_bad,
                   COALESCE(SUM(q_dup), 0) AS dup, COALESCE(SUM(q_cancelled), 0) AS cancelled
              FROM jobs WHERE status = 'done'""").fetchone()
        # 4) 필드별 빈 값 합계
        blank_rows = conn.execute("""
            SELECT b.key AS field, SUM(b.value::int) AS n
              FROM jobs, jsonb_each_text(q_blank) AS b
             WHERE q_blank IS NOT NULL GROUP BY b.key""").fetchall()
        # 5) 재수집 시 건수 변동 이력(최근 50건)
        changes = conn.execute('SELECT at, lawd_cd, deal_ymd, "before", "after" FROM changes '
                               "ORDER BY at DESC LIMIT 50").fetchall()

    blank_sum = dict.fromkeys(BLANK_FIELDS, 0)
    for r in blank_rows:
        blank_sum[r["field"]] = r["n"]
    total = agg["total"]
    blanks = [dict(field=api.CAMEL.get(f, f), missing=n, pct=round(100 * n / total, 2) if total else 0)
              for f, n in blank_sum.items()]
    for r in problems + zero_codes + sgg_mismatch + changes:
        r["name"] = names.get(r["lawd_cd"], "?")
    return dict(total=total, problems=problems, zero_codes=zero_codes, sgg_mismatch=sgg_mismatch,
                blanks=blanks, ymd_mismatch=agg["ymd_bad"], duplicates=agg["dup"],
                cancelled=agg["cancelled"], changes=changes)
```

`collector/jobs.py` 끝에 덧붙인다:
```python
def progress():
    """화면용 진행 상황: 집계 + 배치 상태(실행 중, 최근 오류, 대기 등)."""
    return {**quality.progress(), **state}
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체. test_quality 4 passed 포함)

- [ ] **Step 5: Commit**

```bash
git add collector/quality.py collector/jobs.py tests/test_quality.py
git commit -m "진행 상황·누락 점검 보고를 Postgres로 이식

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 웹 앱 이식 (앱 팩토리, 화면, API, 스케줄러)

**Files:**
- Create: `web/__init__.py`, `web/common.py`, `web/pages.py`, `web/api.py`, `scheduler.py`, `tests/test_web.py`
- Replace: `app.py`
- Modify: `templates/base.html`, `templates/status.html`, `tests/conftest.py`
- Delete: `legacy_collector.py`

**Interfaces:**
- Consumes: Task 1–5 전부
- Produces:
  - `app.create_app() -> Flask`, 모듈 변수 `app.app`
  - `app.JSONProvider`(datetime → "YYYY-MM-DD HH:MM:SS", date → ISO, Decimal → float)
  - `web.common.CODES, SIDO, NAMES`, `common.filters(args, for_jobs=False) -> (where_sql, params)`, `common.api_row(row) -> dict`(camelCase + sido, sigungu), `common.LABELS: list[(api_field, 한글)]`, `common.register_filters(app)`
  - 블루프린트 `web.pages.bp`(`pages.index`, `pages.status`, `pages.download`), `web.api.bp`(`api.status`, `api.quality`, `api.trades`)
  - `scheduler.start() -> BackgroundScheduler`, `scheduler.get() -> BackgroundScheduler | None`
  - 테스트 픽스처 `app`, `client`(로그인된 세션)

- [ ] **Step 1: 테스트 픽스처 추가**

`tests/conftest.py` 끝에 덧붙인다:
```python
@pytest.fixture
def app(pg):
    import app as app_module
    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    """로그인된 테스트 클라이언트."""
    c = app.test_client()
    with c.session_transaction() as s:
        s["auth"] = True
    return c
```

- [ ] **Step 2: 테스트 작성**

`tests/test_web.py`:
```python
from datetime import datetime

import pytest

import settings
from collector import store
from tests.helpers import add_job, item


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])


@pytest.fixture
def seeded(pg):
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        add_job(conn, "11140", "202601")
        store.save_job(conn, "11110", "202601", [item(aptNm="종로아파트"), item(aptNm="해제단지", cdealType="O")], 2)
        store.save_job(conn, "11140", "202601", [item(sggCd="11140", aptNm="중구아파트")], 1)


def test_index_lists_trades(client, seeded):
    html = client.get("/").get_data(as_text=True)
    assert "종로아파트" in html and "중구아파트" in html
    assert "조건에 맞는 거래 3건" in html


def test_index_filters(client, seeded):
    html = client.get("/?lawd_cd=11140").get_data(as_text=True)
    assert "중구아파트" in html and "종로아파트" not in html
    html = client.get("/?q=종로").get_data(as_text=True)
    assert "조건에 맞는 거래 1건" in html
    html = client.get("/?sido=서울특별시&exclude_cancelled=1").get_data(as_text=True)
    assert "해제단지" not in html and "조건에 맞는 거래 2건" in html


def test_status_page(client, seeded):
    resp = client.get("/status")
    assert resp.status_code == 200
    assert "누락 점검" in resp.get_data(as_text=True)


def test_download_requires_filter(client, seeded):
    resp = client.get("/download.csv")
    assert resp.status_code == 400


def test_download_csv(client, seeded):
    resp = client.get("/download.csv?year=2026")
    body = resp.get_data()
    assert resp.status_code == 200
    assert body.startswith("\ufeff".encode("utf-8"))
    text = body.decode("utf-8-sig")
    header = text.splitlines()[0].split(",")
    assert header[:3] == ["계약일", "시도", "시군구"]
    assert "종로아파트" in text and "2026-01-05" in text


def test_api_trades_camel_case(client, seeded):
    data = client.get("/api/trades?lawd_cd=11110").get_json()
    assert len(data) == 2
    row = data[0]
    assert row["dealDate"] == "2026-01-05"
    assert row["dealAmount"] == 84000
    assert row["excluUseAr"] == 84.97
    assert row["sido"] == "서울특별시" and row["sigungu"] == "종로구"
    assert "deal_amount" not in row and "id" not in row


def test_api_status_formats_datetime(client, seeded):
    data = client.get("/api/status").get_json()
    assert data["last_fetched"] == "2026-10-03 07:00:00"
    assert data["by_status"]["done"] == 2


def test_api_quality(client, seeded):
    data = client.get("/api/quality").get_json()
    assert data["cancelled"] == 1
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_web.py -v`
Expected: FAIL — `AttributeError: module 'app' has no attribute 'create_app'` (또는 기존 app.py가 `import collector as c`로 실패)

- [ ] **Step 4: 공용 모듈 구현**

`web/__init__.py`는 빈 파일. `web/common.py`:
```python
"""화면·API 공용: 시군구 이름, 거래 조회 조건, 거래 행 변환, 템플릿 필터."""
from collector import api, codes

CODES = codes.load_codes()
SIDO = list(dict.fromkeys(CODES["시도"]))
NAMES = {r.LAWD_CD: (r.시도, r.시군구) for r in CODES.itertuples()}

# CSV·화면용 한글 열 이름 (API 필드명 기준. 기존 /download.csv 형식 유지)
LABELS = [
    ("dealDate", "계약일"), ("sido", "시도"), ("sigungu", "시군구"), ("lawd_cd", "시군구코드"),
    ("umdNm", "법정동"), ("jibun", "지번"), ("roadNm", "도로명"), ("roadNmBonbun", "건물본번"),
    ("roadNmBubun", "건물부번"), ("aptNm", "단지명"), ("aptDong", "동"), ("aptSeq", "단지일련번호"),
    ("excluUseAr", "전용면적"), ("floor", "층"), ("buildYear", "건축년도"), ("dealAmount", "거래금액(만원)"),
    ("dealingGbn", "거래유형"), ("estateAgentSggNm", "중개사소재지"), ("slerGbn", "매도자"),
    ("buyerGbn", "매수자"), ("cdealType", "해제여부"), ("cdealDay", "해제사유발생일"),
    ("rgstDate", "등기일자"), ("landLeaseholdGbn", "토지임대부"), ("sggCd", "법정동시군구코드"),
    ("umdCd", "법정동읍면동코드"), ("bonbun", "본번"), ("bubun", "부번"), ("deal_ymd", "계약년월"),
    ("collected_at", "수집시각"),
]

_HIDDEN = {"id", "price_per_m2", "is_cancelled"}


def filters(args, for_jobs=False):
    """요청 인자 → WHERE 절과 파라미터. for_jobs=True면 jobs 테이블에도 있는 조건(지역·기간)만."""
    where, params = [], []
    if args.get("sido"):
        where.append("lawd_cd = ANY(%s)")
        params.append(CODES.loc[CODES["시도"] == args["sido"], "LAWD_CD"].tolist())
    if args.get("lawd_cd"):
        where.append("lawd_cd = %s")
        params.append(args["lawd_cd"])
    if args.get("year"):
        where.append("deal_ymd BETWEEN %s AND %s")
        params += [f"{args['year']}01", f"{args['year']}12"]
    if args.get("ymd"):
        where.append("deal_ymd = %s")
        params.append(args["ymd"].replace("-", ""))
    if not for_jobs:
        if args.get("q"):
            where.append("(apt_nm ILIKE %s OR umd_nm ILIKE %s OR road_nm ILIKE %s)")
            params += [f"%{args['q']}%"] * 3
        if args.get("exclude_cancelled"):
            where.append("NOT is_cancelled")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def api_row(row):
    """DB 행(snake_case) → API 필드명(camelCase) + 시도·시군구 이름."""
    d = {api.CAMEL.get(k, k): v for k, v in row.items() if k not in _HIDDEN}
    d["sido"], d["sigungu"] = NAMES.get(row["lawd_cd"], ("", ""))
    return d


def register_filters(app):
    @app.template_filter("comma")
    def comma(v):
        return f"{v:,}" if isinstance(v, (int, float)) else (v or "")

    @app.template_filter("mb")
    def mb(v):
        return f"{(v or 0) / 1024 / 1024:,.1f}MB"
```

- [ ] **Step 5: 화면·API 블루프린트 구현**

`web/pages.py`:
```python
"""화면: 거래 목록, 수집 현황, CSV 다운로드."""
import csv
import io

from flask import Blueprint, Response, render_template, request

import db
import settings
from collector import jobs, quality
from web.common import CODES, LABELS, SIDO, api_row, filters

bp = Blueprint("pages", __name__)
PAGE_SIZE = 50


def count_trades(conn, args):
    """지역·기간 조건만 있으면 jobs의 저장 건수 합으로 바로 계산하고, 검색어가 있을 때만 행을 센다."""
    if args.get("q"):
        where, params = filters(args)
        return conn.execute(f"SELECT COUNT(*) AS n FROM trades{where}", params).fetchone()["n"]
    where, params = filters(args, for_jobs=True)
    col = "stored_count - COALESCE(q_cancelled, 0)" if args.get("exclude_cancelled") else "stored_count"
    return conn.execute(f"SELECT COALESCE(SUM({col}), 0) AS n FROM jobs{where}", params).fetchone()["n"]


@bp.route("/")
def index():
    args = request.args
    where, params = filters(args)
    page = max(int(args.get("page", 1) or 1), 1)
    with db.connection() as conn:
        total = count_trades(conn, args)
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY deal_date DESC NULLS LAST, id DESC LIMIT %s OFFSET %s",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
        all_months = [r["deal_ymd"] for r in conn.execute(
            "SELECT DISTINCT deal_ymd FROM jobs ORDER BY deal_ymd DESC")]
    year = args.get("year", "")
    sido = args.get("sido", "")
    sigungu = CODES[CODES["시도"] == sido] if sido else CODES
    return render_template(
        "index.html", rows=[api_row(r) for r in rows], total=total, page=page,
        pages=max((total - 1) // PAGE_SIZE + 1, 1), args=args, sido_list=SIDO,
        sigungu_list=sigungu.to_dict("records"), months=[m for m in all_months if m.startswith(year)],
        p=jobs.progress(), years=sorted({m[:4] for m in all_months}, reverse=True),
    )


@bp.route("/status")
def status():
    return render_template(
        "status.html", p=jobs.progress(), q=quality.quality_report(), start_ymd=settings.START_YMD,
        refresh_months=settings.REFRESH_MONTHS, refresh_at=settings.REFRESH_AT,
        recheck_days=settings.RECHECK_DAYS, old_recheck_days=settings.OLD_RECHECK_DAYS,
        interval=settings.REQUEST_INTERVAL,
    )


@bp.route("/download.csv")
def download():
    args = request.args
    if not any(args.get(k) for k in ("sido", "lawd_cd", "year", "ymd")):
        # 전체(2006년~) 한 번에 내려받으면 수 GB가 되어 서버에 부담이 크다
        return Response("시도·시군구·연도·계약월 중 하나 이상을 선택한 뒤 내려받으세요.",
                        status=400, mimetype="text/plain; charset=utf-8")
    where, params = filters(args)

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        # utf-8-sig: 엑셀에서 한글이 깨지지 않도록 BOM을 맨 앞에 붙인다
        yield "\ufeff".encode("utf-8")
        writer.writerow([label for _, label in LABELS])
        # 서버 측 커서(이름 있는 커서)는 트랜잭션 안에서만 동작한다
        with db.connection() as conn, conn.transaction(), conn.cursor(name="download") as cur:
            cur.execute(f"SELECT * FROM trades{where} ORDER BY deal_date, lawd_cd", params)
            while batch := cur.fetchmany(5000):
                for row in batch:
                    d = api_row(row)
                    writer.writerow(["" if d.get(k) is None else d.get(k) for k, _ in LABELS])
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate()
        if buf.tell():
            yield buf.getvalue().encode("utf-8")

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=apt_trades.csv"})
```

`web/api.py`:
```python
"""JSON API (기존 형식 유지: 거래 필드는 API 필드명)."""
from flask import Blueprint, jsonify, request

import db
from collector import jobs, quality
from web.common import api_row, filters

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/status")
def status():
    return jsonify(jobs.progress())


@bp.route("/quality")
def quality_():
    return jsonify(quality.quality_report())


@bp.route("/trades")
def trades():
    where, params = filters(request.args)
    limit = min(int(request.args.get("limit", 100) or 100), 1000)
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY deal_date DESC NULLS LAST, id DESC LIMIT %s",
            [*params, limit]).fetchall()
    return jsonify([api_row(r) for r in rows])
```
(블루프린트 함수 이름이 모듈 `quality`와 겹치지 않게 `quality_`로 둔다. 엔드포인트 이름은 `api.quality_`.)

- [ ] **Step 6: 스케줄러와 앱 팩토리 구현**

`scheduler.py`:
```python
"""백그라운드 작업 등록(APScheduler). 계획 2·3에서 좌표·집계 작업을 여기에 더한다."""
from apscheduler.schedulers.background import BackgroundScheduler

import settings
from collector import jobs

_scheduler = None


def get():
    return _scheduler


def start():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    s = BackgroundScheduler(timezone="Asia/Seoul")
    s.add_job(jobs.run_batch, "interval", minutes=settings.BATCH_MINUTES,
              kwargs={"max_jobs": settings.BATCH_JOBS}, id="collect", max_instances=1, coalesce=True,
              next_run_time=settings.now_kst())  # 시작 직후 1회
    s.start()
    _scheduler = s
    return s
```

`app.py` 전체를 다음으로 교체:
```python
"""아파트 매매 실거래가를 자동 수집·최신화하고 분석하는 웹앱."""
import logging
from datetime import date, datetime
from decimal import Decimal

from flask import Flask
from flask.json.provider import DefaultJSONProvider

import db
import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _json_default(o):
    if isinstance(o, datetime):
        return o.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return DefaultJSONProvider.default(o)


class JSONProvider(DefaultJSONProvider):
    default = staticmethod(_json_default)
    ensure_ascii = False


def create_app():
    from web import api, pages
    from web.common import register_filters

    flask_app = Flask(__name__)
    flask_app.secret_key = settings.require("SECRET_KEY")
    flask_app.json = JSONProvider(flask_app)
    db.migrate()
    flask_app.register_blueprint(pages.bp)
    flask_app.register_blueprint(api.bp)
    register_filters(flask_app)
    return flask_app


app = create_app()

if settings.flag("COLLECT_ENABLED", True):
    import scheduler
    scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(settings.env("PORT", "8000")))
```

- [ ] **Step 7: 템플릿 수정**

`templates/status.html`에서 다음 두 곳을 바꾼다.

바꿀 내용 1:
```
  <div class="tile"><div class="k">DB 용량 (볼륨 {{ st.limit_bytes|mb }} 중)</div>
```
→
```
  <div class="tile"><div class="k">DB 용량 (기준 {{ st.limit_bytes|mb }})</div>
```
바꿀 내용 2:
```
<p class="meta">DB 본체 {{ st.db_bytes|mb }} · WAL {{ st.wal_bytes|mb }} · 재사용 대기 빈 공간 {{ st.free_bytes|mb }} ·
  거래 1건당 약 {{ st.bytes_per_trade or '-' }}B · 사용률 {{ st.pct }}%</p>
```
→
```
<p class="meta">Postgres DB {{ st.total_bytes|mb }} · 거래 1건당 약 {{ st.bytes_per_trade or '-' }}B (인덱스 포함) · 사용률 {{ st.pct }}%</p>
```
바꿀 내용 3:
```
  <li>볼륨 사용률이 {{ p.storage.stop_pct|int }}%에 닿으면 과거 자료 수집을 멈추고 최근 자료만 갱신합니다.</li>
```
→
```
  <li>DB 사용률이 {{ p.storage.stop_pct|int }}%에 닿으면 과거 자료 수집을 멈추고 최근 자료만 갱신합니다.</li>
```

`templates/base.html`에서 메타 줄 블록을 `p`가 있을 때만 그리도록 감싼다(로그인 화면은 `p`가 없다):
```
  <div class="meta">
    출처: 국토교통부 실거래가 정보(공공데이터포털 Open API) ·
```
→
```
  {% if p is defined %}
  <div class="meta">
    출처: 국토교통부 실거래가 정보(공공데이터포털 Open API) ·
```
그리고 그 `<div class="meta">`를 닫는 `</div>` 바로 다음 줄(`{% block body %}` 앞)에 `{% endif %}`를 넣는다.

- [ ] **Step 8: 옛 수집기 삭제**

```bash
git rm legacy_collector.py
```

- [ ] **Step 9: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체. test_web 8 passed 포함)

- [ ] **Step 10: 로컬 실행 확인**

`.claude/launch.json`을 만든다:
```json
{
  "version": "0.0.1",
  "configurations": [
    {
      "name": "molit-web",
      "runtimeExecutable": ".venv/Scripts/python.exe",
      "runtimeArgs": ["app.py"],
      "port": 8000
    }
  ]
}
```
preview_start `{name: "molit-web"}`로 띄우고 `/`와 `/status`가 오류 없이 열리는지 확인한다(빈 DB라 "아직 수집된 거래가 없습니다"가 보이면 정상). `.claude/`는 `.gitignore`에 있어 커밋되지 않는다.

- [ ] **Step 11: Commit**

```bash
git add app.py scheduler.py web/ templates/base.html templates/status.html tests/conftest.py tests/test_web.py
git commit -m "웹 앱을 앱 팩토리·블루프린트 구조로 바꾸고 Postgres로 이식

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 공유 비밀번호 로그인

**Files:**
- Create: `web/auth.py`, `templates/login.html`, `tests/test_auth.py`
- Modify: `app.py`, `templates/base.html`, `tests/conftest.py`

**Interfaces:**
- Consumes: Task 6 `create_app`
- Produces:
  - 블루프린트 `web.auth.bp`(`auth.login` GET/POST `/login`, `auth.logout` POST `/logout`)
  - `auth.protect(app)`: 로그인 안 된 요청 → 화면은 `/login?next=...`로 302, `/api/*`는 401 JSON `{"error": "로그인이 필요합니다."}`. 예외: `auth.login`, `static`
  - `auth.reset()`(테스트용 시도 기록 초기화)
  - 세션 키 `session["auth"] = True`
  - 실패 시도 제한: IP당 60초에 5회. 초과하면 429

- [ ] **Step 1: 테스트 작성**

`tests/test_auth.py`:
```python
import pytest


@pytest.fixture
def anon(app):
    from web import auth
    auth.reset()
    return app.test_client()


def test_pages_redirect_to_login(anon):
    resp = anon.get("/status")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login?next=%2Fstatus"


def test_api_returns_401(anon):
    resp = anon.get("/api/status")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "로그인이 필요합니다."}


def test_login_page_renders(anon):
    resp = anon.get("/login")
    assert resp.status_code == 200
    assert "비밀번호" in resp.get_data(as_text=True)


def test_login_wrong_password(anon):
    resp = anon.post("/login", data={"password": "nope"})
    assert resp.status_code == 200
    assert "비밀번호가 맞지 않습니다" in resp.get_data(as_text=True)
    assert anon.get("/status").status_code == 302


def test_login_success_redirects_to_next(anon):
    resp = anon.post("/login?next=/status", data={"password": "test-password"})
    assert resp.status_code == 302 and resp.headers["Location"] == "/status"
    assert anon.get("/status").status_code == 200


@pytest.mark.parametrize("bad_next", ["//evil.example", "https://evil.example", "/\\evil.example"])
def test_login_rejects_external_next(anon, bad_next):
    resp = anon.post(f"/login?next={bad_next}", data={"password": "test-password"})
    assert resp.headers["Location"] == "/"


def test_login_rate_limited(anon):
    for _ in range(5):
        anon.post("/login", data={"password": "nope"})
    resp = anon.post("/login", data={"password": "test-password"})
    assert resp.status_code == 429


def test_logout(client):
    assert client.get("/status").status_code == 200
    client.post("/logout")
    assert client.get("/status").status_code == 302
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ImportError: cannot import name 'auth' from 'web'`

- [ ] **Step 3: 구현**

`web/auth.py`:
```python
"""공유 비밀번호 로그인(연구실 범위). 다중 사용자 계정은 범위 밖."""
import hmac
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

import settings

bp = Blueprint("auth", __name__)

MAX_FAILURES = 5
WINDOW_SECONDS = 60
_failures = defaultdict(deque)
_failures_lock = threading.Lock()
_OPEN_ENDPOINTS = {"auth.login", "static"}


def reset():
    with _failures_lock:
        _failures.clear()


def _recent_failures(ip):
    now = time.monotonic()
    with _failures_lock:
        q = _failures[ip]
        while q and now - q[0] > WINDOW_SECONDS:
            q.popleft()
        return len(q)


def _record_failure(ip):
    with _failures_lock:
        _failures[ip].append(time.monotonic())


def _safe_next(target):
    """같은 사이트 안의 경로만 허용한다(//host, scheme://, 역슬래시 우회 차단)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _recent_failures(ip) >= MAX_FAILURES:
            return render_template("login.html", error="시도 횟수가 많습니다. 1분 뒤 다시 시도하세요."), 429
        password = request.form.get("password", "")
        if hmac.compare_digest(password.encode(), settings.require("APP_PASSWORD").encode()):
            session.clear()
            session["auth"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")))
        _record_failure(ip)
        error = "비밀번호가 맞지 않습니다."
    return render_template("login.html", error=error)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def protect(app):
    @app.before_request
    def require_login():
        if request.endpoint in _OPEN_ENDPOINTS or session.get("auth"):
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="로그인이 필요합니다."), 401
        nxt = request.full_path.rstrip("?")
        return redirect(url_for("auth.login", next=nxt))
```

`templates/login.html`:
```html
{% extends "base.html" %}
{% block title %}로그인 · 아파트 실거래가{% endblock %}
{% block body %}
<form method="post" class="bar" style="max-width:420px">
  <input type="password" name="password" placeholder="비밀번호" autofocus required style="flex:1 1 200px">
  <button class="primary">로그인</button>
</form>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
{% endblock %}
```

`templates/base.html`의 `<nav>` 안, 마지막 `<a href="/status" ...>` 줄 다음에 추가:
```html
    {% if session.get('auth') %}<form method="post" action="/logout" style="margin-left:auto"><button>로그아웃</button></form>{% endif %}
```

`app.py`의 `create_app()`을 다음처럼 바꾼다(import에 `from datetime import date, datetime, timedelta`, `from werkzeug.middleware.proxy_fix import ProxyFix` 추가):
```python
def create_app():
    from web import api, auth, pages
    from web.common import register_filters

    flask_app = Flask(__name__)
    flask_app.secret_key = settings.require("SECRET_KEY")
    flask_app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(settings.env("RAILWAY_ENVIRONMENT")),  # Railway(HTTPS)에서만
    )
    # Railway 프록시 뒤: 실제 클라이언트 IP·https를 반영(시도 제한·보안 쿠키용)
    flask_app.wsgi_app = ProxyFix(flask_app.wsgi_app, x_for=1, x_proto=1)
    flask_app.json = JSONProvider(flask_app)
    db.migrate()
    flask_app.register_blueprint(auth.bp)
    flask_app.register_blueprint(pages.bp)
    flask_app.register_blueprint(api.bp)
    auth.protect(flask_app)
    register_filters(flask_app)
    return flask_app
```

`tests/conftest.py`의 `app` 픽스처에서 `create_app()` 호출 전에 시도 기록을 비운다:
```python
@pytest.fixture
def app(pg):
    import app as app_module
    from web import auth
    auth.reset()
    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    return flask_app
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체. test_auth 10 passed 포함)

- [ ] **Step 5: Commit**

```bash
git add web/auth.py templates/login.html templates/base.html app.py tests/conftest.py tests/test_auth.py
git commit -m "공유 비밀번호 로그인과 시도 제한 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: SQLite → Postgres 이전 스크립트

**Files:**
- Create: `scripts/migrate_sqlite.py`, `tests/test_migrate_sqlite.py`

**Interfaces:**
- Consumes: `db.migrate`, `db.connection`, `api.FIELDS/COLUMNS/snake/to_int/to_float`, `store.TRADE_COLS`
- Produces: `scripts.migrate_sqlite.main(argv: list[str]) -> int`(0 성공, 1 실패). 옵션 `--sqlite PATH`(기본 `$DATA_DIR/trades.db`, `DATA_DIR` 기본 `data`), `--replace`(대상 테이블을 비우고 다시 복사)

- [ ] **Step 1: 테스트 작성**

`tests/test_migrate_sqlite.py`:
```python
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


def test_migrate_reports_count_mismatch(pg, tmp_path):
    src = tmp_path / "trades.db"
    make_sqlite(src, stored_count=3)
    assert migrate_sqlite.main(["--sqlite", str(src)]) == 1
```

`scripts/__init__.py`(빈 파일)도 만든다(테스트에서 `from scripts import migrate_sqlite`).

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_migrate_sqlite.py -v`
Expected: FAIL — `ImportError: cannot import name 'migrate_sqlite' from 'scripts'`

- [ ] **Step 3: 구현**

`scripts/migrate_sqlite.py`:
```python
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
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체. test_migrate_sqlite 4 passed 포함)

- [ ] **Step 5: Commit**

```bash
git add scripts/ tests/test_migrate_sqlite.py
git commit -m "SQLite → Postgres 이전 스크립트 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: 문서와 배포 절차

**Files:**
- Modify: `README.md`
- Create: `docs/runbooks/postgres-migration.md`

**Interfaces:**
- Consumes: 앞 Task 전부
- Produces: 사람이 따라 하는 배포 절차서. 실행자는 Railway 작업을 직접 하지 않는다(사용자 확인이 필요한 외부 작업).

- [ ] **Step 1: README 갱신**

`README.md`에서 다음을 바꾼다.

첫 설명 줄 `... 웹앱 (Flask, Railway 배포).` → `... 웹앱 (Flask + Postgres, Railway 배포).`

`## 수집 방식` 목록의 `- 볼륨 사용률이 90%(`STORAGE_STOP_PCT`)에 닿으면 ...` 줄 → `- DB 사용률(Postgres 크기 / `DB_LIMIT_MB`)이 90%(`STORAGE_STOP_PCT`)에 닿으면 과거 자료 수집을 멈추고 최근 자료만 갱신`

`## 엔드포인트` 표 위에 다음 절을 넣는다:
```markdown
## 구조
| 경로 | 역할 |
|---|---|
| `settings.py` | 환경변수(`.env`)·KST 시각·수집 설정 |
| `db.py`, `migrations/` | Postgres 커넥션 풀, SQL 마이그레이션(시작할 때 자동 적용) |
| `collector/` | 국토부 API 호출(`api`), 저장(`store`), 작업 선택·배치(`jobs`), 누락 점검(`quality`) |
| `web/` | 화면(`pages`), JSON API(`api`), 로그인(`auth`) |
| `scheduler.py` | 백그라운드 수집 작업 |
| `scripts/migrate_sqlite.py` | 옛 SQLite → Postgres 1회 이전 |

모든 화면과 API는 로그인이 필요합니다(공유 비밀번호 `APP_PASSWORD`).
```

`## 환경변수` 표를 다음으로 교체:
```markdown
| 이름 | 기본값 | 설명 |
|---|---|---|
| `MOLIT_SERVICE_KEY` | (필수) | 공공데이터포털 인증키 |
| `DATABASE_URL` | (필수) | Postgres 연결 문자열. Railway는 `${{Postgres.DATABASE_URL}}` |
| `APP_PASSWORD` | (필수) | 공유 로그인 비밀번호 |
| `SECRET_KEY` | (필수) | 세션 서명 키 (`python -c "import secrets;print(secrets.token_hex(32))"`) |
| `COLLECT_ENABLED` | `true` | `false`면 수집 스케줄러를 켜지 않음 |
| `START_YMD` | `200601` | 수집 시작 계약월 |
| `DAILY_LIMIT` | `8000` | 하루 호출 상한 |
| `REQUEST_INTERVAL` | `1.5` | 호출 간격(초) |
| `REFRESH_AT` | `06:00` | 매일 수집을 시작하는 시각(KST). 최근 3개월 재수집도 이때 |
| `DB_LIMIT_MB` | `5000` | 용량 사용률 기준(Railway Hobby 볼륨 5GB) |
| `STORAGE_STOP_PCT` | `90` | 이 사용률 이상이면 과거 자료 수집 중단 |
| `DATA_DIR` | `data` | 옛 SQLite 위치(이전 스크립트 기본 경로) |

인증키·비밀번호는 로컬은 `.env`, Railway는 Variables에 둡니다(저장소에 올리지 않음).
```

`## 로컬 실행` 절을 다음으로 교체:
````markdown
## 로컬 실행
Postgres 17을 설치하고(`winget install -e --id PostgreSQL.PostgreSQL.17 --interactive`) `molit_dev`, `molit_test` DB를 만든 뒤 `.env`에 다음을 넣습니다.
```
MOLIT_SERVICE_KEY=발급받은키
DATABASE_URL=postgresql://postgres:<pw>@localhost:5432/molit_dev
TEST_DATABASE_URL=postgresql://postgres:<pw>@localhost:5432/molit_test
APP_PASSWORD=로컬비밀번호
SECRET_KEY=임의의긴문자열
COLLECT_ENABLED=false
```
```bash
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
.venv/Scripts/python app.py        # http://localhost:8000
```
````

- [ ] **Step 2: 배포 절차서 작성**

`docs/runbooks/postgres-migration.md`:
````markdown
# Railway: SQLite → Postgres 이전 절차

명세 §10을 따른다. 모든 단계는 사람이 Railway 대시보드·CLI에서 직접 한다.

## 0. 준비
- Railway 플랜이 Hobby 이상인지 확인(볼륨 5GB, Postgres 서비스 사용).
- 되돌릴 지점을 남긴다: `git tag pre-postgres <현재 운영 중인 main 커밋>` 후 `git push origin pre-postgres`.

## 1. Postgres 추가
1. 프로젝트 → New → Database → PostgreSQL.
2. web 서비스 Variables:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (내부 네트워크 주소)
   - `APP_PASSWORD` = 연구실 공유 비밀번호
   - `SECRET_KEY` = `python -c "import secrets;print(secrets.token_hex(32))"` 결과
   - `COLLECT_ENABLED` = `false`
   - `DB_LIMIT_MB` = `5000`
   - 예전 변수 `COLLECTOR_DISABLED`, `VOLUME_LIMIT_MB`가 있으면 삭제
3. 기존 볼륨(`/data`)은 **그대로 둔다**(SQLite 원본).

## 2. 새 코드 배포
`feature/analysis-dashboard` 브랜치를 main에 합치거나, web 서비스의 배포 브랜치를 이 브랜치로 바꿔 배포한다.
배포 로그에서 마이그레이션 오류가 없는지, `/login`이 열리는지 확인한다.

## 3. 데이터 이전
```bash
railway ssh --service web
python scripts/migrate_sqlite.py --sqlite /data/trades.db
```
마지막 줄이 `검증 통과`인지 확인한다. 실패하면 출력된 불일치 작업을 기록하고 `--replace`로 다시 실행한다.

## 4. 수집 재개
- `/status`에서 거래 건수·작업 수가 이전과 같은지 확인한다.
- Variables에서 `COLLECT_ENABLED`를 지운다(기본 `true`). 재배포 후 다음 06:00(KST)부터 수집되는지 `/status`로 확인한다.

## 5. 정리 (1주일 동안 안정적으로 돈 뒤)
- SQLite 원본을 내려받아 보관한다(예: `railway ssh --service web` 안에서 `gzip -c /data/trades.db > /data/trades.db.gz` 후 Railway 볼륨 백업 또는 파일 다운로드 기능 사용).
- web 서비스에서 볼륨을 분리·삭제한다.

## 롤백 (5단계 전까지)
web 서비스를 `pre-postgres` 태그 커밋으로 다시 배포하고, Variables에 `COLLECTOR_DISABLED`·`VOLUME_LIMIT_MB`를 원래 값으로 되돌린다. SQLite 볼륨이 그대로라 이전 상태로 동작한다.
````

- [ ] **Step 3: 전체 테스트**

Run: `.venv/Scripts/python -m pytest -v`
Expected: PASS (전체)

- [ ] **Step 4: Commit**

```bash
git add README.md docs/runbooks/postgres-migration.md
git commit -m "Postgres 구조·환경변수 문서와 Railway 이전 절차서 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

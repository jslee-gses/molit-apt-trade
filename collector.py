"""공공데이터포털 '아파트 매매 실거래가 상세자료' API 수집기.

수집 단위(job) = 시군구코드(LAWD_CD) x 계약년월(DEAL_YMD).
오래된 계약월부터 순서대로 받고, 하루 호출 한도를 넘지 않게 천천히 호출한다.
결과는 SQLite(DATA_DIR/trades.db)에 job 단위로 통째로 교체 저장한다.
"""
import json
import logging
import os
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

log = logging.getLogger(__name__)

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/xml",
}
KST = ZoneInfo("Asia/Seoul")
BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
DB_PATH = DATA_DIR / "trades.db"
CODES_PATH = BASE_DIR / "lawd_codes.csv"

START_YMD = os.environ.get("START_YMD", "200601")              # 수집 시작 계약월(공개 시작 2006-01)
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "8000"))       # 하루 호출 상한(개발계정 한도보다 낮게)
REQUEST_INTERVAL = float(os.environ.get("REQUEST_INTERVAL", "1.5"))  # 호출 간격(초)
REFRESH_MONTHS = int(os.environ.get("REFRESH_MONTHS", "3"))    # 매일 다시 받을 최근 개월 수(신고기한 30일, 해제 반영)
REFRESH_AT = os.environ.get("REFRESH_AT", "06:00")             # 최근 N개월을 매일 다시 받는 시각(KST)
RECHECK_DAYS = int(os.environ.get("RECHECK_DAYS", "7"))        # 최근 1년(재수집 구간 이전) 건수 재확인 주기
RECHECK_MONTHS = int(os.environ.get("RECHECK_MONTHS", "12"))   # 주기 재확인할 지난 개월 수
OLD_RECHECK_DAYS = int(os.environ.get("OLD_RECHECK_DAYS", "180"))  # 그보다 오래된 달의 재확인 주기
STORAGE_STOP_PCT = float(os.environ.get("STORAGE_STOP_PCT", "90"))  # 볼륨 사용률이 이 이상이면 과거 자료 수집 중단
NUM_ROWS = 1000
VOLUME_LIMIT_MB = int(os.environ.get("VOLUME_LIMIT_MB", "500"))  # Railway 볼륨 용량(사용률 표시용)

# API 응답 필드 (기술문서 순서)
FIELDS = [
    "sggCd", "umdCd", "landCd", "bonbun", "bubun", "roadNm", "roadNmSggCd", "roadNmCd",
    "roadNmSeq", "roadNmbCd", "roadNmBonbun", "roadNmBubun", "umdNm", "aptNm", "jibun",
    "excluUseAr", "dealYear", "dealMonth", "dealDay", "dealAmount", "floor", "buildYear",
    "aptSeq", "cdealType", "cdealDay", "dealingGbn", "estateAgentSggNm", "rgstDate",
    "aptDong", "slerGbn", "buyerGbn", "landLeaseholdGbn",
]
# 누락 점검 대상: 위치(지오코딩)·가격 분석에 꼭 필요한 필드
KEY_FIELDS = ["aptNm", "umdNm", "jibun", "roadNm", "excluUseAr", "floor", "buildYear", "aptSeq"]
BLANK_FIELDS = KEY_FIELDS + ["dealAmount", "dealDate"]
QUALITY_COLS = ["q_blank", "q_dup", "q_ymd_bad", "q_sgg_bad", "q_cancelled"]

_lock = threading.Lock()
state = {"running": False, "current": None, "last_error": None, "paused_until": None,
         "backfill_stopped": None}
_ensured = {"month": None}


class QuotaExceeded(Exception):
    pass


class ApiError(Exception):
    pass


# ---------------------------------------------------------------- 공통

def now_kst():
    return datetime.now(KST)


def now_str():
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


def load_service_key():
    key = os.environ.get("MOLIT_SERVICE_KEY")
    env_file = BASE_DIR / ".env"
    if not key and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "MOLIT_SERVICE_KEY":
                key = value.strip().strip('"').strip("'")
    if not key:
        raise RuntimeError("MOLIT_SERVICE_KEY가 설정되지 않았습니다.")
    # Encoding 키를 넣었으면 풀어서 requests가 한 번만 인코딩하게 한다
    return unquote(key) if "%" in key else key


def connect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    cols = ", ".join(f'"{f}" TEXT' for f in FIELDS if f not in ("dealAmount", "excluUseAr"))
    with closing(connect()) as conn, conn:
        conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS trades (
            lawd_cd TEXT NOT NULL, deal_ymd TEXT NOT NULL,
            {cols}, dealAmount INTEGER, excluUseAr REAL,
            dealDate TEXT, collected_at TEXT
        );
        DROP INDEX IF EXISTS ix_trades_job;
        CREATE INDEX IF NOT EXISTS ix_trades_ymd ON trades(deal_ymd, lawd_cd);
        CREATE INDEX IF NOT EXISTS ix_trades_date ON trades(dealDate);
        CREATE TABLE IF NOT EXISTS jobs (
            lawd_cd TEXT NOT NULL, deal_ymd TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending / done / incomplete / error
            total_count INTEGER, stored_count INTEGER,
            fetched_at TEXT, checked_at TEXT, attempts INTEGER DEFAULT 0,
            next_try_at TEXT, error TEXT,
            PRIMARY KEY (lawd_cd, deal_ymd)
        );
        CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status, deal_ymd);
        CREATE TABLE IF NOT EXISTS api_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS changes (       -- 재수집 시 건수 변동 기록(늦은 신고·해제 추적)
            at TEXT, lawd_cd TEXT, deal_ymd TEXT, before INTEGER, after INTEGER
        );
        """)
        # 누락 점검을 작업 단위로 미리 집계해 둔다(화면을 열 때 전체 행을 훑지 않도록)
        have = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
        for col in QUALITY_COLS:
            if col not in have:
                conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {'TEXT' if col == 'q_blank' else 'INTEGER'}")


def load_codes():
    return pd.read_csv(CODES_PATH, dtype=str, encoding="utf-8-sig")


def month_range(start_ymd, end_ymd):
    y, m = int(start_ymd[:4]), int(start_ymd[4:])
    out = []
    while f"{y}{m:02d}" <= end_ymd:
        out.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def ensure_jobs():
    """시작월~이번 달 x 전 시군구 job이 없으면 만든다(달이 바뀌면 자동 추가)."""
    this_month = now_kst().strftime("%Y%m")
    if _ensured["month"] == this_month:
        return
    months = month_range(START_YMD, this_month)
    codes = load_codes()["LAWD_CD"].tolist()
    with closing(connect()) as conn, conn:
        conn.executemany(
            "INSERT OR IGNORE INTO jobs(lawd_cd, deal_ymd) VALUES (?, ?)",
            [(c, ym) for ym in months for c in codes],
        )
    _ensured["month"] = this_month


# ---------------------------------------------------------------- 호출 한도

def today():
    return now_kst().strftime("%Y-%m-%d")


def calls_today(conn):
    row = conn.execute("SELECT calls FROM api_usage WHERE day=?", (today(),)).fetchone()
    return row["calls"] if row else 0


def count_call(conn):
    conn.execute(
        "INSERT INTO api_usage(day, calls) VALUES (?, 1) "
        "ON CONFLICT(day) DO UPDATE SET calls = calls + 1",
        (today(),),
    )
    conn.commit()


def quota_left(conn):
    return DAILY_LIMIT - calls_today(conn)


# ---------------------------------------------------------------- API

def fetch_page(session, key, conn, lawd_cd, deal_ymd, page_no, num_rows=NUM_ROWS):
    if quota_left(conn) <= 0:
        raise QuotaExceeded("오늘 호출 상한 도달")
    params = {"serviceKey": key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd,
              "pageNo": page_no, "numOfRows": num_rows}
    time.sleep(REQUEST_INTERVAL)
    count_call(conn)
    try:
        resp = session.get(API_URL, params=params, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        # 예외 메시지에 serviceKey가 든 URL이 섞일 수 있어 종류만 남긴다
        raise ApiError(f"네트워크 오류: {type(e).__name__}") from None
    if resp.status_code != 200:
        raise ApiError(f"HTTP {resp.status_code}")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError:
        raise ApiError("XML 파싱 실패") from None

    # 게이트웨이 오류(키 미등록·한도 초과 등)는 형식이 다르다
    reason = root.findtext(".//returnReasonCode")
    if reason:
        if reason.strip() == "22":
            raise QuotaExceeded("공공데이터포털 일일 한도 초과(22)")
        raise ApiError(f"게이트웨이 오류 {reason}: {root.findtext('.//returnAuthMsg')}")
    code = (root.findtext(".//resultCode") or "").strip()
    if code == "22":
        raise QuotaExceeded("공공데이터포털 일일 한도 초과(22)")
    if code not in ("000", "00"):
        raise ApiError(f"API 오류 {code}: {root.findtext('.//resultMsg')}")

    items = [{c.tag: (c.text or "").strip() for c in item} for item in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or 0)


def fetch_job(session, key, conn, lawd_cd, deal_ymd):
    """job 하나의 전체 페이지 수집 -> (items, total_count)."""
    items, total = fetch_page(session, key, conn, lawd_cd, deal_ymd, 1)
    page = 1
    while len(items) < total:
        page += 1
        more, total = fetch_page(session, key, conn, lawd_cd, deal_ymd, page)
        if not more:
            break
        items += more
    return items, total


def to_rows(items, lawd_cd, deal_ymd, collected_at):
    rows = []
    for it in items:
        r = {f: it.get(f, "") for f in FIELDS}
        amount = r["dealAmount"].replace(",", "").strip()
        r["dealAmount"] = int(amount) if amount.isdigit() else None
        try:
            r["excluUseAr"] = float(r["excluUseAr"]) if r["excluUseAr"] else None
        except ValueError:
            r["excluUseAr"] = None
        try:
            r["dealDate"] = f"{int(r['dealYear']):04d}-{int(r['dealMonth']):02d}-{int(r['dealDay']):02d}"
        except ValueError:
            r["dealDate"] = None
        r.update(lawd_cd=lawd_cd, deal_ymd=deal_ymd, collected_at=collected_at)
        rows.append(r)
    return rows


def job_quality(rows, lawd_cd, deal_ymd):
    """작업 하나의 누락 점검 집계: 필드별 빈 값, 완전 중복, 계약월·코드 불일치, 해제 건수."""
    blank = {f: sum(1 for r in rows if r.get(f) is None or str(r.get(f)).strip() == "")
             for f in BLANK_FIELDS}
    keys = [tuple(r.get(f) for f in FIELDS) for r in rows]
    return dict(
        q_blank=json.dumps({f: n for f, n in blank.items() if n}),
        q_dup=len(keys) - len(set(keys)),
        q_ymd_bad=sum(1 for r in rows if not r.get("dealDate")
                      or r["dealDate"][:7].replace("-", "") != deal_ymd),
        q_sgg_bad=sum(1 for r in rows if (r.get("sggCd") or "") != lawd_cd),
        q_cancelled=sum(1 for r in rows if (r.get("cdealType") or "").strip()),
    )


def backfill_quality():
    """품질 집계 열이 비어 있는 기존 작업을 한 번 채운다(이전 버전에서 받은 자료)."""
    with closing(connect()) as conn:
        todo = conn.execute(
            "SELECT lawd_cd, deal_ymd FROM jobs WHERE status='done' AND q_blank IS NULL").fetchall()
        for job in todo:
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM trades WHERE deal_ymd=? AND lawd_cd=?", (job["deal_ymd"], job["lawd_cd"]))]
            q = job_quality(rows, job["lawd_cd"], job["deal_ymd"])
            conn.execute(f"UPDATE jobs SET {', '.join(f'{k}=:{k}' for k in q)} "
                         "WHERE lawd_cd=:lawd_cd AND deal_ymd=:deal_ymd",
                         {**q, "lawd_cd": job["lawd_cd"], "deal_ymd": job["deal_ymd"]})
        conn.commit()
    if todo:
        log.info("기존 작업 %d개의 품질 집계를 채움", len(todo))


def save_job(conn, lawd_cd, deal_ymd, items, total):
    stored_at = now_str()
    rows = to_rows(items, lawd_cd, deal_ymd, stored_at)
    prev = conn.execute(
        "SELECT stored_count FROM jobs WHERE lawd_cd=? AND deal_ymd=?", (lawd_cd, deal_ymd)
    ).fetchone()
    cols = ["lawd_cd", "deal_ymd", *FIELDS, "dealDate", "collected_at"]
    col_sql = ", ".join(f'"{c}"' for c in cols)
    q = job_quality(rows, lawd_cd, deal_ymd)
    with conn:
        conn.execute("DELETE FROM trades WHERE deal_ymd=? AND lawd_cd=?", (deal_ymd, lawd_cd))
        conn.executemany(
            f"INSERT INTO trades({col_sql}) VALUES ({', '.join('?' * len(cols))})",
            [[r[c] for c in cols] for r in rows],
        )
        # 누락 점검 1: API가 알려준 전체 건수와 실제 저장 건수 비교
        status = "done" if len(rows) == total else "incomplete"
        conn.execute(
            "UPDATE jobs SET status=?, total_count=?, stored_count=?, fetched_at=?, checked_at=?, "
            "attempts=attempts+1, error=?, next_try_at=? WHERE lawd_cd=? AND deal_ymd=?",
            (status, total, len(rows), stored_at, stored_at,
             None if status == "done" else f"저장 {len(rows)}건 / 전체 {total}건",
             None if status == "done" else (now_kst() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S"),
             lawd_cd, deal_ymd),
        )
        conn.execute(f"UPDATE jobs SET {', '.join(f'{k}=:{k}' for k in q)} "
                     "WHERE lawd_cd=:lawd_cd AND deal_ymd=:deal_ymd",
                     {**q, "lawd_cd": lawd_cd, "deal_ymd": deal_ymd})
        if prev and prev["stored_count"] is not None and prev["stored_count"] != len(rows):
            conn.execute("INSERT INTO changes VALUES (?, ?, ?, ?, ?)",
                         (stored_at, lawd_cd, deal_ymd, prev["stored_count"], len(rows)))
    return status, len(rows)


def mark_error(conn, lawd_cd, deal_ymd, msg):
    with conn:
        row = conn.execute("SELECT attempts FROM jobs WHERE lawd_cd=? AND deal_ymd=?",
                           (lawd_cd, deal_ymd)).fetchone()
        attempts = (row["attempts"] or 0) + 1
        wait = min(2 ** attempts, 24 * 60)  # 분 단위 지수 백오프, 최대 하루
        conn.execute(
            "UPDATE jobs SET status=CASE WHEN status='done' THEN 'done' ELSE 'error' END, "
            "attempts=?, error=?, next_try_at=? WHERE lawd_cd=? AND deal_ymd=?",
            (attempts, msg, (now_kst() + timedelta(minutes=wait)).strftime("%Y-%m-%d %H:%M:%S"),
             lawd_cd, deal_ymd),
        )


# ---------------------------------------------------------------- 무엇을 받을지

def daily_start(day_offset=0):
    """그날의 수집 시작 시각(REFRESH_AT, KST)."""
    hour, minute = map(int, REFRESH_AT.split(":"))
    at = now_kst().replace(hour=hour, minute=minute, second=0, microsecond=0)
    return at + timedelta(days=day_offset)


def last_refresh_time():
    """가장 최근에 지난 매일 갱신 시각. 이 시각 전에 받은 최근 달은 다시 받는다."""
    at = daily_start()
    return at if now_kst() >= at else at - timedelta(days=1)


def months_ago(n):
    y, m = now_kst().year, now_kst().month - n
    while m < 1:
        y, m = y - 1, m + 12
    return f"{y}{m:02d}"


def next_jobs(conn, limit, allow_backfill=True):
    """우선순위
    ① 최근 N개월 미수집(새 달 포함)  ② 최근 N개월 매일 재수집  ③ 오류·불일치 재시도
    ④ 과거 자료 미수집(오래된 달부터, 용량 여유가 있을 때만)
    ⑤ 지난 1년 건수 재확인(RECHECK_DAYS 주기)  ⑥ 그보다 오래된 달 건수 재확인(OLD_RECHECK_DAYS 주기)
    """
    fmt = "%Y-%m-%d %H:%M:%S"
    params = dict(
        now=now_str(),
        recent=months_ago(REFRESH_MONTHS - 1),
        check_from=months_ago(REFRESH_MONTHS - 1 + RECHECK_MONTHS),
        refresh=last_refresh_time().strftime(fmt),
        recheck=(now_kst() - timedelta(days=RECHECK_DAYS)).strftime(fmt),
        old_recheck=(now_kst() - timedelta(days=OLD_RECHECK_DAYS)).strftime(fmt),
        backfill=1 if allow_backfill else 0,
        limit=limit,
    )
    sql = """
    SELECT lawd_cd, deal_ymd, 'fetch' AS mode, 1 AS pri FROM jobs
     WHERE status='pending' AND deal_ymd >= :recent
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 2 FROM jobs
     WHERE status='done' AND deal_ymd >= :recent AND fetched_at < :refresh
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 3 FROM jobs
     WHERE status IN ('error','incomplete') AND (next_try_at IS NULL OR next_try_at <= :now)
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 4 FROM jobs
     WHERE status='pending' AND deal_ymd < :recent AND :backfill = 1
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'check', 5 FROM jobs
     WHERE status='done' AND deal_ymd < :recent AND deal_ymd >= :check_from AND checked_at <= :recheck
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'check', 6 FROM jobs
     WHERE status='done' AND deal_ymd < :check_from AND checked_at <= :old_recheck
    ORDER BY pri, deal_ymd, lawd_cd LIMIT :limit
    """
    return conn.execute(sql, params).fetchall()


def check_job(session, key, conn, lawd_cd, deal_ymd):
    """누락 점검: 지난 달은 1건만 요청해 전체 건수만 비교, 달라졌으면 다시 받도록 표시."""
    _, total = fetch_page(session, key, conn, lawd_cd, deal_ymd, 1, num_rows=1)
    row = conn.execute("SELECT stored_count FROM jobs WHERE lawd_cd=? AND deal_ymd=?",
                       (lawd_cd, deal_ymd)).fetchone()
    with conn:
        if row["stored_count"] != total:
            conn.execute("UPDATE jobs SET status='pending', error=? WHERE lawd_cd=? AND deal_ymd=?",
                         (f"재확인 시 건수 변동 {row['stored_count']}→{total}", lawd_cd, deal_ymd))
        else:
            conn.execute("UPDATE jobs SET checked_at=? WHERE lawd_cd=? AND deal_ymd=?",
                         (now_str(), lawd_cd, deal_ymd))


def storage_pct():
    total = sum(p.stat().st_size for p in DATA_DIR.glob("trades.db*") if p.is_file())
    return 100 * total / (VOLUME_LIMIT_MB * 1024 * 1024)


def run_batch(max_jobs=20):
    """스케줄러가 1분마다 호출. 매일 REFRESH_AT부터 하루 한도까지 수집하고, 0시~REFRESH_AT에는 쉰다."""
    if not _lock.acquire(blocking=False):
        return
    state["running"] = True
    try:
        if state["paused_until"] and now_str() < state["paused_until"]:
            return
        state["paused_until"] = None
        if now_kst() < daily_start():
            return  # 오늘 수집 시작 전
        key = load_service_key()
        ensure_jobs()
        if not _ensured.get("quality"):
            backfill_quality()
            _ensured["quality"] = True

        pct = storage_pct()
        allow_backfill = pct < STORAGE_STOP_PCT
        state["backfill_stopped"] = None if allow_backfill else (
            f"볼륨 사용률 {pct:.1f}% ≥ {STORAGE_STOP_PCT:g}% → 과거 자료 수집 중단(최근 자료는 계속)")

        with closing(connect()) as conn, requests.Session() as session:
            for job in next_jobs(conn, max_jobs, allow_backfill):
                if quota_left(conn) <= 0:
                    raise QuotaExceeded("오늘 호출 상한 도달")
                lawd_cd, deal_ymd, mode = job["lawd_cd"], job["deal_ymd"], job["mode"]
                state["current"] = f"{deal_ymd} {lawd_cd} ({mode})"
                try:
                    if mode == "check":
                        check_job(session, key, conn, lawd_cd, deal_ymd)
                    else:
                        items, total = fetch_job(session, key, conn, lawd_cd, deal_ymd)
                        status, n = save_job(conn, lawd_cd, deal_ymd, items, total)
                        log.info("%s %s: %d건 (%s)", deal_ymd, lawd_cd, n, status)
                except ApiError as e:
                    log.warning("%s %s 실패: %s", deal_ymd, lawd_cd, e)
                    mark_error(conn, lawd_cd, deal_ymd, str(e))
                    state["last_error"] = f"{now_str()} {deal_ymd} {lawd_cd}: {e}"
    except QuotaExceeded as e:
        # 다음 날 수집 시작 시각까지 쉰다
        resume = daily_start(1).strftime("%Y-%m-%d %H:%M:%S")
        state["paused_until"] = resume
        log.info("%s → %s까지 대기", e, resume)
    except Exception as e:  # noqa: BLE001
        state["last_error"] = f"{now_str()} {type(e).__name__}: {e}"
        log.exception("수집 배치 실패")
    finally:
        state["running"] = False
        state["current"] = None
        _lock.release()


# ---------------------------------------------------------------- 진행 상황·누락 점검 보고

def progress():
    """진행 상황. 거래 건수는 jobs의 저장 건수 합으로 계산한다(대용량에서 COUNT(*) 피함)."""
    with closing(connect()) as conn:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
        by_month = [dict(r) for r in conn.execute("""
            SELECT deal_ymd, COUNT(*) jobs, SUM(status='done') done,
                   SUM(status IN ('error','incomplete')) bad, SUM(COALESCE(stored_count,0)) trades,
                   MAX(fetched_at) last_fetched
              FROM jobs GROUP BY deal_ymd ORDER BY deal_ymd""")]
        last = conn.execute("SELECT MAX(fetched_at) FROM jobs").fetchone()[0]
        used = calls_today(conn)
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        free_bytes = conn.execute("PRAGMA freelist_count").fetchone()[0] * page_size
    by_year = {}
    for m in by_month:
        y = by_year.setdefault(m["deal_ymd"][:4], dict(year=m["deal_ymd"][:4], jobs=0, done=0, bad=0,
                                                         trades=0, last_fetched=None))
        for k in ("jobs", "done", "bad", "trades"):
            y[k] += m[k] or 0
        y["last_fetched"] = max(filter(None, [y["last_fetched"], m["last_fetched"]]), default=None)
    trades = sum(m["trades"] for m in by_month)
    total_jobs = sum(by_status.values())
    pending = by_status.get("pending", 0)
    per_day = max(DAILY_LIMIT - REFRESH_MONTHS * len(load_codes()), 1)
    return dict(by_status=by_status, by_month=by_month, by_year=list(by_year.values()), trades=trades,
                last_fetched=last, calls_today=used, daily_limit=DAILY_LIMIT, total_jobs=total_jobs,
                eta_days=-(-pending // per_day) if pending else 0, refresh_at=REFRESH_AT,
                storage=storage(trades, free_bytes), **state)


def storage(trades, free_bytes):
    """DB 파일 크기(본체+WAL)와 볼륨 한도 대비 사용률."""
    files = {p.name: p.stat().st_size for p in DATA_DIR.glob("trades.db*") if p.is_file()}
    total = sum(files.values())
    db = files.get("trades.db", 0)
    return dict(db_bytes=db, wal_bytes=files.get("trades.db-wal", 0), total_bytes=total,
                free_bytes=free_bytes, bytes_per_trade=round(db / trades) if trades else None,
                limit_bytes=VOLUME_LIMIT_MB * 1024 * 1024, stop_pct=STORAGE_STOP_PCT,
                pct=round(100 * total / (VOLUME_LIMIT_MB * 1024 * 1024), 1))


def quality_report():
    """누락 점검 결과 모음. 모두 jobs의 작업별 집계에서 계산한다."""
    codes = load_codes().set_index("LAWD_CD")
    with closing(connect()) as conn:
        # 1) 아직 못 받았거나 건수가 안 맞는 job
        problems = [dict(r) for r in conn.execute("""
            SELECT deal_ymd, lawd_cd, status, total_count, stored_count, attempts, error, next_try_at
              FROM jobs WHERE status IN ('error','incomplete') ORDER BY deal_ymd, lawd_cd LIMIT 500""")]

        # 2) 받은 달이 모두 0건인 시군구 → 코드 변경(행정구역 개편) 의심
        zero_codes = [dict(r) for r in conn.execute("""
            SELECT lawd_cd, COUNT(*) months, MAX(deal_ymd) last_ymd FROM jobs
             WHERE status='done' GROUP BY lawd_cd
            HAVING SUM(stored_count) = 0 AND COUNT(*) >= 2""")]

        # 3) 응답의 sggCd가 요청 코드와 다른 작업(코드 체계 변화 감지)
        sgg_mismatch = [dict(r) for r in conn.execute("""
            SELECT lawd_cd, deal_ymd, q_sgg_bad n FROM jobs WHERE q_sgg_bad > 0
             ORDER BY deal_ymd, lawd_cd LIMIT 500""")]

        agg = conn.execute("""
            SELECT COALESCE(SUM(stored_count),0) total, COALESCE(SUM(q_ymd_bad),0) ymd_bad,
                   COALESCE(SUM(q_dup),0) dup, COALESCE(SUM(q_cancelled),0) cancelled
              FROM jobs WHERE status='done'""").fetchone()

        # 4) 필드별 빈 값 합계
        blank_sum = dict.fromkeys(BLANK_FIELDS, 0)
        for (q,) in conn.execute("SELECT q_blank FROM jobs WHERE q_blank IS NOT NULL AND q_blank != '{}'"):
            for f, n in json.loads(q).items():
                blank_sum[f] = blank_sum.get(f, 0) + n

        # 5) 재수집 시 건수 변동 이력(최근 50건)
        changes = [dict(r) for r in conn.execute(
            "SELECT * FROM changes ORDER BY at DESC LIMIT 50")]

    total = agg["total"]
    blanks = [dict(field=f, missing=n, pct=round(100 * n / total, 2) if total else 0)
              for f, n in blank_sum.items()]
    name = lambda c: f"{codes.loc[c, '시도']} {codes.loc[c, '시군구']}" if c in codes.index else "?"  # noqa: E731
    for r in problems + zero_codes + sgg_mismatch + changes:
        r["name"] = name(r["lawd_cd"])
    return dict(total=total, problems=problems, zero_codes=zero_codes, sgg_mismatch=sgg_mismatch,
                blanks=blanks, ymd_mismatch=agg["ymd_bad"], duplicates=agg["dup"],
                cancelled=agg["cancelled"], changes=changes)

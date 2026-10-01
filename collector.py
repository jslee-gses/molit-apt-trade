"""공공데이터포털 '아파트 매매 실거래가 상세자료' API 수집기.

수집 단위(job) = 시군구코드(LAWD_CD) x 계약년월(DEAL_YMD).
오래된 계약월부터 순서대로 받고, 하루 호출 한도를 넘지 않게 천천히 호출한다.
결과는 SQLite(DATA_DIR/trades.db)에 job 단위로 통째로 교체 저장한다.
"""
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

START_YMD = os.environ.get("START_YMD", "202601")              # 수집 시작 계약월
DAILY_LIMIT = int(os.environ.get("DAILY_LIMIT", "8000"))       # 하루 호출 상한(개발계정 한도보다 낮게)
REQUEST_INTERVAL = float(os.environ.get("REQUEST_INTERVAL", "1.5"))  # 호출 간격(초)
REFRESH_MONTHS = int(os.environ.get("REFRESH_MONTHS", "3"))    # 매일 다시 받을 최근 개월 수(신고기한 30일, 해제 반영)
REFRESH_HOURS = int(os.environ.get("REFRESH_HOURS", "24"))
RECHECK_DAYS = int(os.environ.get("RECHECK_DAYS", "7"))        # 지난 달 건수 재확인 주기
NUM_ROWS = 1000

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

_lock = threading.Lock()
state = {"running": False, "current": None, "last_error": None, "paused_until": None}


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
        CREATE INDEX IF NOT EXISTS ix_trades_job ON trades(lawd_cd, deal_ymd);
        CREATE INDEX IF NOT EXISTS ix_trades_date ON trades(dealDate);
        CREATE TABLE IF NOT EXISTS jobs (
            lawd_cd TEXT NOT NULL, deal_ymd TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',   -- pending / done / incomplete / error
            total_count INTEGER, stored_count INTEGER,
            fetched_at TEXT, checked_at TEXT, attempts INTEGER DEFAULT 0,
            next_try_at TEXT, error TEXT,
            PRIMARY KEY (lawd_cd, deal_ymd)
        );
        CREATE TABLE IF NOT EXISTS api_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS changes (       -- 재수집 시 건수 변동 기록(늦은 신고·해제 추적)
            at TEXT, lawd_cd TEXT, deal_ymd TEXT, before INTEGER, after INTEGER
        );
        """)


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
    months = month_range(START_YMD, now_kst().strftime("%Y%m"))
    codes = load_codes()["LAWD_CD"].tolist()
    with closing(connect()) as conn, conn:
        conn.executemany(
            "INSERT OR IGNORE INTO jobs(lawd_cd, deal_ymd) VALUES (?, ?)",
            [(c, ym) for ym in months for c in codes],
        )


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


def save_job(conn, lawd_cd, deal_ymd, items, total):
    stored_at = now_str()
    rows = to_rows(items, lawd_cd, deal_ymd, stored_at)
    prev = conn.execute(
        "SELECT stored_count FROM jobs WHERE lawd_cd=? AND deal_ymd=?", (lawd_cd, deal_ymd)
    ).fetchone()
    cols = ["lawd_cd", "deal_ymd", *FIELDS, "dealDate", "collected_at"]
    col_sql = ", ".join(f'"{c}"' for c in cols)
    with conn:
        conn.execute("DELETE FROM trades WHERE lawd_cd=? AND deal_ymd=?", (lawd_cd, deal_ymd))
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

def next_jobs(conn, limit):
    """우선순위: ①미수집(오래된 달부터) ②오류·불일치 재시도 ③최근 N개월 재수집 ④지난 달 건수 재확인."""
    now = now_str()
    y, m = now_kst().year, now_kst().month - (REFRESH_MONTHS - 1)
    while m < 1:
        y, m = y - 1, m + 12
    recent_from = f"{y}{m:02d}"
    refresh_before = (now_kst() - timedelta(hours=REFRESH_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    recheck_before = (now_kst() - timedelta(days=RECHECK_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    sql = """
    SELECT lawd_cd, deal_ymd, 'fetch' AS mode, 1 AS pri FROM jobs WHERE status='pending'
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 2 FROM jobs
     WHERE status IN ('error','incomplete') AND (next_try_at IS NULL OR next_try_at <= :now)
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'fetch', 3 FROM jobs
     WHERE status='done' AND deal_ymd >= :recent AND fetched_at <= :refresh
    UNION ALL
    SELECT lawd_cd, deal_ymd, 'check', 4 FROM jobs
     WHERE status='done' AND deal_ymd < :recent AND checked_at <= :recheck
    ORDER BY pri, deal_ymd, lawd_cd LIMIT :limit
    """
    return conn.execute(sql, dict(now=now, recent=recent_from, refresh=refresh_before,
                                  recheck=recheck_before, limit=limit)).fetchall()


def check_job(session, key, conn, lawd_cd, deal_ymd):
    """누락 점검 2: 지난 달은 1건만 요청해 전체 건수만 비교, 달라졌으면 다시 받도록 표시."""
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


def run_batch(max_jobs=20):
    """스케줄러가 주기적으로 호출. 한 번에 max_jobs개 job만 처리하고 빠진다."""
    if not _lock.acquire(blocking=False):
        return
    state["running"] = True
    try:
        if state["paused_until"] and now_str() < state["paused_until"]:
            return
        state["paused_until"] = None
        key = load_service_key()
        ensure_jobs()
        with closing(connect()) as conn, requests.Session() as session:
            for job in next_jobs(conn, max_jobs):
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
        # 다음 날 0시(KST)까지 쉰다
        tomorrow = (now_kst() + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00")
        state["paused_until"] = tomorrow
        log.info("%s → %s까지 대기", e, tomorrow)
    except Exception as e:  # noqa: BLE001
        state["last_error"] = f"{now_str()} {type(e).__name__}: {e}"
        log.exception("수집 배치 실패")
    finally:
        state["running"] = False
        state["current"] = None
        _lock.release()


# ---------------------------------------------------------------- 진행 상황·누락 점검 보고

def progress():
    with closing(connect()) as conn:
        by_status = {r["status"]: r["n"] for r in conn.execute(
            "SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
        by_month = conn.execute("""
            SELECT deal_ymd, COUNT(*) jobs, SUM(status='done') done,
                   SUM(status IN ('error','incomplete')) bad, SUM(COALESCE(stored_count,0)) trades,
                   MAX(fetched_at) last_fetched
              FROM jobs GROUP BY deal_ymd ORDER BY deal_ymd""").fetchall()
        trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        last = conn.execute("SELECT MAX(fetched_at) FROM jobs").fetchone()[0]
        used = calls_today(conn)
    return dict(by_status=by_status, by_month=[dict(r) for r in by_month], trades=trades,
                last_fetched=last, calls_today=used, daily_limit=DAILY_LIMIT,
                total_jobs=sum(by_status.values()), **state)


def quality_report():
    """누락 점검 결과 모음."""
    codes = load_codes().set_index("LAWD_CD")
    with closing(connect()) as conn:
        # 1) 아직 못 받았거나 건수가 안 맞는 job
        problems = [dict(r) for r in conn.execute("""
            SELECT deal_ymd, lawd_cd, status, total_count, stored_count, attempts, error, next_try_at
              FROM jobs WHERE status IN ('error','incomplete') ORDER BY deal_ymd, lawd_cd""")]

        # 2) 받은 달이 모두 0건인 시군구 → 코드 변경(행정구역 개편) 의심
        zero_codes = [dict(r) for r in conn.execute("""
            SELECT lawd_cd, COUNT(*) months, MAX(deal_ymd) last_ymd FROM jobs
             WHERE status='done' GROUP BY lawd_cd
            HAVING SUM(stored_count) = 0 AND COUNT(*) >= 2""")]

        # 3) 응답의 sggCd가 요청 코드와 다른 경우(코드 체계 변화 감지)
        sgg_mismatch = [dict(r) for r in conn.execute("""
            SELECT lawd_cd, sggCd, COUNT(*) n FROM trades
             WHERE sggCd != lawd_cd GROUP BY lawd_cd, sggCd""")]

        # 4) 필드별 빈 값 비율(지오코딩·분석용 핵심 필드)
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        blanks = []
        for f in KEY_FIELDS + ["dealAmount", "dealDate"]:
            n = conn.execute(f'SELECT COUNT(*) FROM trades WHERE "{f}" IS NULL OR TRIM("{f}")=\'\'').fetchone()[0]
            blanks.append(dict(field=f, missing=n, pct=round(100 * n / total, 2) if total else 0))

        # 5) 계약월과 dealDate가 어긋나는 행
        ymd_mismatch = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE dealDate IS NULL OR "
            "REPLACE(SUBSTR(dealDate,1,7),'-','') != deal_ymd").fetchone()[0]

        # 6) 완전히 같은 행(중복 의심) — 같은 날 같은 동·층·가격 거래는 실제로도 있을 수 있음
        dup = conn.execute(f"""
            SELECT COALESCE(SUM(c - 1), 0) FROM (
              SELECT COUNT(*) c FROM trades
               GROUP BY {', '.join(f'"{f}"' for f in FIELDS)} HAVING c > 1)""").fetchone()[0]

        # 7) 해제(취소)된 거래 수
        cancelled = conn.execute("SELECT COUNT(*) FROM trades WHERE TRIM(cdealType) != ''").fetchone()[0]

        # 8) 재수집 시 건수 변동 이력(최근 50건)
        changes = [dict(r) for r in conn.execute(
            "SELECT * FROM changes ORDER BY at DESC LIMIT 50")]

    name = lambda c: f"{codes.loc[c, '시도']} {codes.loc[c, '시군구']}" if c in codes.index else "?"  # noqa: E731
    for r in problems + zero_codes + sgg_mismatch + changes:
        r["name"] = name(r["lawd_cd"])
    return dict(total=total, problems=problems, zero_codes=zero_codes, sgg_mismatch=sgg_mismatch,
                blanks=blanks, ymd_mismatch=ymd_mismatch, duplicates=dup, cancelled=cancelled,
                changes=changes)

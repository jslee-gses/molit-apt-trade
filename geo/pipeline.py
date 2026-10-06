"""스케줄러가 10분마다 부르는 지리 처리.

경계 버전 동기화 → (처음 한 번) 기존 거래로 단지 만들기 → 대응표 변경 확인 → 지역 판정 → AFTER_RUN(집계 등)
"""
import logging
import threading
from datetime import timedelta

import db
import settings
from geo import assign, code_map, complexes, versions

log = logging.getLogger(__name__)
_lock = threading.Lock()
RETRY_AFTER = timedelta(hours=1)
_failed = None   # (버전, 실패 시각): 같은 버전 전환을 한동안 다시 시도하지 않는다
state = {"last_attempt": None, "last_run": None, "sync_error": None, "bootstrap_error": None, "code_map_reset": None, "last_result": None, "last_error": None}
AFTER_RUN = []   # fn(conn). 계획 3: 집계 대기열 처리


def _sync(conn):
    """경계 전환 실패가 나머지 처리를 막지 않게 한다. 실패한 버전은 RETRY_AFTER 동안 건너뛴다."""
    global _failed
    now = settings.now_kst()
    skip = {_failed[0]} if _failed and now - _failed[1] < RETRY_AFTER else set()
    try:
        switched = versions.sync(conn, skip=skip)
    except Exception as e:  # noqa: BLE001
        found = versions.available()
        _failed = (found[-1] if found else None, now)
        state["sync_error"] = f"{settings.now_str()} {type(e).__name__}: {e}"
        log.exception("경계 버전 전환 실패")
        return None
    state["sync_error"] = None if not skip else state["sync_error"]
    return switched


def _bootstrap(conn):
    """단지 최초 생성 실패(시간 초과 등)가 나머지 처리를 막지 않게 한다. 표식이 없으면 다음 실행에서 다시 시도한다."""
    try:
        added = complexes.bootstrap(conn)
    except Exception as e:  # noqa: BLE001
        state["bootstrap_error"] = f"{settings.now_str()} {type(e).__name__}: {e}"
        log.exception("단지 최초 생성 실패")
        return 0
    state["bootstrap_error"] = None
    return added


def check_code_map(conn):
    """대응표(code_map.csv)가 지난 판정 때와 다르면 코드로 판정한 단지를 다시 판정하게 한다. → 재판정을 걸었으면 True"""
    flag = f"code_map:{code_map.digest()}"
    if conn.execute("SELECT 1 FROM app_flags WHERE name = %s", (flag,)).fetchone():
        return False
    with conn.transaction():
        complexes.lock_complexes(conn)
        conn.execute("UPDATE complexes SET boundary_version = NULL WHERE region_match IN ('code', 'none')")
        conn.execute("DELETE FROM app_flags WHERE name LIKE 'code_map:%'")
        conn.execute("INSERT INTO app_flags (name, set_at) VALUES (%s, %s)", (flag, settings.now_ts()))
    return True


def run():
    if not _lock.acquire(blocking=False):
        return
    state["last_attempt"] = settings.now_str()
    try:
        with db.connection() as conn:
            switched = _sync(conn)
            added = _bootstrap(conn)
            state["code_map_reset"] = check_code_map(conn)
            version = versions.active(conn)
            assigned = assign.assign_pending(conn, version) if version else 0
            for step in AFTER_RUN:
                step(conn)
        state.update(last_run=settings.now_str(), last_error=None, last_result=dict(
            switched=switched, added=added, assigned=assigned))
    except Exception as e:  # noqa: BLE001
        state["last_error"] = f"{settings.now_str()} {type(e).__name__}: {e}"
        log.exception("지리 처리 실패")
    finally:
        _lock.release()

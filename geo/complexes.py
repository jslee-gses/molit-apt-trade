"""단지(aptSeq) 목록: 거래에서 등록·갱신하고, 좌표 상태를 관리한다."""
import math
from datetime import date

import settings
from geo import hooks

FLAG = "complexes_bootstrapped"

COMPLEXES_LOCK = 7_203_001_001   # complexes를 쓰는 모든 경로가 공유하는 어드바이저리 락 키(고정값)


def lock_complexes(conn):
    """complexes 쓰기를 한 번에 하나씩만 하도록 직렬화한다(수집·지리 스레드의 행 락 교착 방지).
    반드시 트랜잭션 안에서, complexes 행을 건드리기 전에 가장 먼저 부른다(커밋·롤백 때 풀린다)."""
    conn.execute("SELECT pg_advisory_xact_lock(%s)", (COMPLEXES_LOCK,))

UPSERT = """
INSERT INTO complexes AS c (apt_seq, apt_nm, jibun, road_nm, build_year,
                            api_sgg_cd, api_umd_cd, api_umd_nm, last_deal_date)
VALUES (%(apt_seq)s, %(apt_nm)s, %(jibun)s, %(road_nm)s, %(build_year)s,
        %(sgg_cd)s, %(umd_cd)s, %(umd_nm)s, %(deal_date)s)
ON CONFLICT (apt_seq) DO UPDATE SET
    apt_nm = EXCLUDED.apt_nm, jibun = EXCLUDED.jibun, road_nm = EXCLUDED.road_nm,
    build_year = EXCLUDED.build_year, api_sgg_cd = EXCLUDED.api_sgg_cd,
    api_umd_cd = EXCLUDED.api_umd_cd, api_umd_nm = EXCLUDED.api_umd_nm,
    last_deal_date = EXCLUDED.last_deal_date,
    geocode_status = CASE WHEN c.geocode_status = 'failed' THEN 'pending' ELSE c.geocode_status END
WHERE c.last_deal_date IS NULL OR EXCLUDED.last_deal_date >= c.last_deal_date
"""


def register(conn, lawd_cd, deal_ymd, rows):
    """작업 저장 직후(store.AFTER_SAVE): 거래에 나온 단지를 등록하고, 더 최근 거래면 정보를 갱신한다.
    좌표를 못 찾았던 단지(failed)는 새 거래가 오면 다시 찾아본다(pending)."""
    latest = {}
    for r in rows:
        seq = (r.get("apt_seq") or "").strip()
        if not seq:
            continue
        cur = latest.get(seq)
        if cur is None or (r["deal_date"] or date.min) >= (cur["deal_date"] or date.min):
            latest[seq] = {**r, "apt_seq": seq}
    if latest:
        lock_complexes(conn)
        with conn.cursor() as cur:
            cur.executemany(UPSERT, list(latest.values()))


def bootstrap(conn):
    """기존 trades 전체에서 단지를 한 번에 만든다(이전 직후 1회). 완료 표식이 있으면 하지 않는다.
    register가 먼저 넣은 행은 그대로 둔다."""
    if conn.execute("SELECT 1 FROM app_flags WHERE name = %s", (FLAG,)).fetchone():
        return 0
    with conn.transaction():
        lock_complexes(conn)
        return _bootstrap(conn)


def _bootstrap(conn):
    n = conn.execute("""
        INSERT INTO complexes (apt_seq, apt_nm, jibun, road_nm, build_year,
                               api_sgg_cd, api_umd_cd, api_umd_nm, last_deal_date)
        SELECT DISTINCT ON (btrim(apt_seq)) btrim(apt_seq), apt_nm, jibun, road_nm, build_year,
               sgg_cd, umd_cd, umd_nm, deal_date
          FROM trades WHERE btrim(COALESCE(apt_seq, '')) <> ''
         ORDER BY btrim(apt_seq), deal_date DESC NULLS LAST, id DESC
        ON CONFLICT DO NOTHING""").rowcount
    conn.execute("INSERT INTO app_flags (name, set_at) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                 (FLAG, settings.now_ts()))
    return n


KOREA_BBOX = (124.0, 33.0, 132.0, 39.0)   # 경도 최소, 위도 최소, 경도 최대, 위도 최대


def set_manual(conn, apt_seq, lon, lat):
    """수동 좌표. 자동 처리가 덮어쓰지 않으며, 지역은 다음 판정에서 다시 정한다."""
    try:
        lon, lat = float(lon), float(lat)
    except (TypeError, ValueError):
        raise ValueError("경도·위도는 숫자여야 합니다.") from None
    x0, y0, x1, y1 = KOREA_BBOX
    if not (math.isfinite(lon) and math.isfinite(lat) and x0 <= lon <= x1 and y0 <= lat <= y1):
        raise ValueError("한국 범위의 경도(124~132)·위도(33~39)가 아닙니다. 순서가 바뀌지 않았는지 확인하세요.")
    with conn.transaction():
        lock_complexes(conn)
        if not conn.execute("SELECT 1 FROM complexes WHERE apt_seq = %s FOR UPDATE", (apt_seq,)).fetchone():
            raise LookupError(apt_seq)
        for hook in hooks.ON_REGION_CHANGE:   # 바뀌기 전 지역
            hook(conn, [apt_seq])
        conn.execute("""
            UPDATE complexes SET lon = %s, lat = %s, geocode_status = 'manual', geocode_source = 'manual',
                   geocoded_at = %s, region_sgg_cd = NULL, region_umd_cd = NULL, region_match = NULL,
                   boundary_version = NULL, sgg_mismatch = false
             WHERE apt_seq = %s""", (lon, lat, settings.now_ts(), apt_seq))


def summary(conn):
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE geocode_status IN ('ok', 'manual')) AS located,
               COUNT(*) FILTER (WHERE geocode_status = 'ok' AND geocode_source = 'parcel') AS parcel,
               COUNT(*) FILTER (WHERE geocode_status = 'ok' AND geocode_source = 'parcel_near') AS parcel_near,
               COUNT(*) FILTER (WHERE geocode_status = 'ok' AND geocode_source = 'rebuild') AS rebuild,
               COUNT(*) FILTER (WHERE geocode_status = 'ok' AND geocode_source = 'user') AS user_geocoded,
               COUNT(*) FILTER (WHERE geocode_status = 'manual') AS manual,
               COUNT(*) FILTER (WHERE geocode_status = 'pending') AS pending,
               COUNT(*) FILTER (WHERE geocode_status = 'failed') AS failed,
               COUNT(*) FILTER (WHERE region_match IN ('within', 'nearest')) AS by_boundary,
               COUNT(*) FILTER (WHERE region_match = 'nearest') AS nearest,
               COUNT(*) FILTER (WHERE region_match = 'code') AS by_code,
               COUNT(*) FILTER (WHERE region_match = 'none' OR region_match IS NULL) AS unassigned,
               COUNT(*) FILTER (WHERE sgg_mismatch) AS mismatch
          FROM complexes""").fetchone()
    by_status = {r["geocode_status"]: r["n"] for r in conn.execute(
        "SELECT geocode_status, COUNT(*) AS n FROM complexes GROUP BY 1")}
    version = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchone()
    total = row["total"]
    return dict(row, by_status=by_status,
                located_pct=round(100 * row["located"] / total, 1) if total else 0.0,
                boundary_version=version["version"] if version else None)


def failed(conn, limit=100):
    """좌표를 못 찾은 단지: 최근 거래 순으로 limit개를 고른 뒤 거래 많은 순으로 보여 준다."""
    return conn.execute("""
        SELECT c.apt_seq, c.apt_nm, c.api_sgg_cd, c.api_umd_nm, c.jibun, c.road_nm, c.last_deal_date,
               t.n AS n_trades
          FROM (SELECT * FROM complexes WHERE geocode_status = 'failed'
                 ORDER BY last_deal_date DESC NULLS LAST, apt_seq LIMIT %s) c
          CROSS JOIN LATERAL (SELECT COUNT(*) AS n FROM trades WHERE apt_seq = c.apt_seq) t
         ORDER BY t.n DESC, c.apt_seq""", (limit,)).fetchall()


MISSING_COLUMNS = [("apt_seq", "단지 코드"), ("apt_nm", "단지명"), ("status", "상태"), ("sgg_name", "시군구"),
                   ("api_umd_nm", "법정동"), ("jibun", "지번"), ("address", "지번 주소"), ("road_nm", "도로명"),
                   ("build_year", "건축년도"), ("n_trades", "거래 수"), ("last_deal_date", "최근 계약일")]


def missing(conn):
    """좌표가 없는 단지 전부(못 찾음·대기). 시군구 이름은 사용 중 경계 판의 지역 이름(없으면 코드).
    거래 많은 순으로, 지번 주소는 시군구 + 법정동 + 지번."""
    return conn.execute("""
        SELECT c.apt_seq, c.apt_nm,
               CASE c.geocode_status WHEN 'failed' THEN '못 찾음' ELSE '대기(좌표 도구 실행 전)' END AS status,
               COALESCE(r.full_name, c.api_sgg_cd) AS sgg_name, c.api_umd_nm, c.jibun,
               concat_ws(' ', COALESCE(r.full_name, c.api_sgg_cd), c.api_umd_nm, NULLIF(c.jibun, '')) AS address,
               c.road_nm, c.build_year, t.n AS n_trades, c.last_deal_date
          FROM complexes c
          LEFT JOIN regions r ON r.region_cd = c.api_sgg_cd AND r.level = 'sgg'
               AND r.boundary_version = (SELECT version FROM boundary_versions WHERE is_active)
          CROSS JOIN LATERAL (SELECT COUNT(*) AS n FROM trades WHERE apt_seq = c.apt_seq) t
         WHERE c.geocode_status IN ('pending', 'failed')
         ORDER BY t.n DESC, c.apt_seq""").fetchall()


UNASSIGNED_COLUMNS = [("apt_seq", "단지 코드"), ("apt_nm", "단지명"), ("reason", "원인(추정)"), ("coords", "좌표"),
                      ("sgg_name", "시군구"), ("api_sgg_cd", "신고 시군구 코드"), ("api_umd_cd", "신고 법정동 코드"),
                      ("api_umd_nm", "법정동"), ("jibun", "지번"), ("n_trades", "거래 수"), ("last_deal_date", "최근 계약일")]


def unassigned(conn):
    """읍면동을 정하지 못한 단지 전부(판정 대기 포함)와 원인 추정. 거래 많은 순."""
    return conn.execute("""
        SELECT c.apt_seq, c.apt_nm,
               CASE WHEN c.region_match IS NULL THEN '판정 대기(다음 지리 처리에서 판정)'
                    WHEN c.geocode_status IN ('ok', 'manual') AND c.lon IS NOT NULL
                         THEN '좌표가 읍면동 경계에서 200m 넘게 떨어짐 + 법정동 코드로도 못 정함'
                    WHEN COALESCE(c.api_umd_cd, '') = '' THEN '좌표 없음 + 신고 법정동 코드 없음'
                    ELSE '좌표 없음 + 법정동 코드가 현재 경계에 없음(개편 코드 대응표 확인)' END AS reason,
               CASE WHEN c.geocode_status IN ('ok', 'manual') AND c.lon IS NOT NULL
                    THEN '있음(' || COALESCE(c.geocode_source, c.geocode_status) || ')' ELSE '없음' END AS coords,
               COALESCE(r.full_name, c.api_sgg_cd) AS sgg_name, c.api_sgg_cd, c.api_umd_cd, c.api_umd_nm, c.jibun,
               t.n AS n_trades, c.last_deal_date
          FROM complexes c
          LEFT JOIN regions r ON r.region_cd = c.api_sgg_cd AND r.level = 'sgg'
               AND r.boundary_version = (SELECT version FROM boundary_versions WHERE is_active)
          CROSS JOIN LATERAL (SELECT COUNT(*) AS n FROM trades WHERE apt_seq = c.apt_seq) t
         WHERE c.region_match = 'none' OR c.region_match IS NULL
         ORDER BY t.n DESC, c.apt_seq""").fetchall()

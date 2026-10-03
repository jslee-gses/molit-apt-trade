"""단지(aptSeq) 목록: 거래에서 등록·갱신하고, 좌표 상태를 관리한다."""
import math
from datetime import date

import settings
from geo import hooks

FLAG = "complexes_bootstrapped"

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
        with conn.cursor() as cur:
            cur.executemany(UPSERT, list(latest.values()))


def bootstrap(conn):
    """기존 trades 전체에서 단지를 한 번에 만든다(이전 직후 1회). 완료 표식이 있으면 하지 않는다.
    register가 먼저 넣은 행은 그대로 둔다."""
    if conn.execute("SELECT 1 FROM app_flags WHERE name = %s", (FLAG,)).fetchone():
        return 0
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
               COUNT(*) FILTER (WHERE region_match = 'within') AS within,
               COUNT(*) FILTER (WHERE region_match = 'nearest') AS nearest,
               COUNT(*) FILTER (WHERE region_match = 'none') AS outside,
               COUNT(*) FILTER (WHERE sgg_mismatch) AS mismatch
          FROM complexes""").fetchone()
    by_status = {r["geocode_status"]: r["n"] for r in conn.execute(
        "SELECT geocode_status, COUNT(*) AS n FROM complexes GROUP BY 1")}
    points = conn.execute("SELECT COUNT(*) AS n, MAX(source_month) AS month FROM address_points").fetchone()
    version = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchone()
    total = row["total"]
    return dict(row, by_status=by_status,
                located_pct=round(100 * row["located"] / total, 1) if total else 0.0,
                address_points=points["n"], address_month=points["month"],
                boundary_version=version["version"] if version else None)


def failed(conn, limit=100):
    """좌표를 못 찾은 단지(거래 많은 순)."""
    return conn.execute("""
        SELECT c.apt_seq, c.apt_nm, c.api_sgg_cd, c.api_umd_nm, c.jibun, c.road_nm, c.last_deal_date,
               COUNT(t.id) AS n_trades
          FROM complexes c LEFT JOIN trades t ON t.apt_seq = c.apt_seq
         WHERE c.geocode_status = 'failed'
         GROUP BY c.apt_seq
         ORDER BY n_trades DESC, c.apt_seq LIMIT %s""", (limit,)).fetchall()

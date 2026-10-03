"""pending 단지에 위치정보요약DB 좌표를 붙인다(도로명주소 매칭, 외부 API 호출 없음)."""
import settings

# 단지의 거래들이 가진 도로명주소 키 중 address_points에 있는 것, 그중 거래가 가장 많은 키의 좌표
LOCATE = """
WITH keys AS (
    SELECT t.apt_seq,
           road_key(t.road_nm_sgg_cd, t.road_nm_cd, t.road_nmb_cd, t.road_nm_bonbun, t.road_nm_bubun) AS k,
           COUNT(*) AS n
      FROM trades t JOIN complexes c ON c.apt_seq = t.apt_seq
     WHERE c.geocode_status = 'pending' AND c.apt_seq IN (SELECT apt_seq FROM pending_now)
     GROUP BY 1, 2
), best AS (
    SELECT DISTINCT ON (keys.apt_seq) keys.apt_seq, p.lon, p.lat
      FROM keys JOIN address_points p ON p.road_key = keys.k
     ORDER BY keys.apt_seq, keys.n DESC, keys.k
)
UPDATE complexes c
   SET lon = best.lon, lat = best.lat, geocode_status = 'ok', geocode_source = 'road',
       geocoded_at = %(now)s, region_sgg_cd = NULL, region_umd_cd = NULL, region_match = NULL,
       boundary_version = NULL, sgg_mismatch = false
  FROM best
 WHERE c.apt_seq = best.apt_seq AND c.geocode_status = 'pending'
"""

# 처리 도중 수집기가 새로 등록한 pending 단지를 시도 없이 failed로 만들지 않도록, 시작 시점의 목록만 다룬다
SNAPSHOT = """
CREATE TEMP TABLE pending_now ON COMMIT DROP AS
SELECT apt_seq FROM complexes WHERE geocode_status = 'pending'
"""
MARK_FAILED = """
UPDATE complexes SET geocode_status = 'failed'
 WHERE geocode_status = 'pending' AND apt_seq IN (SELECT apt_seq FROM pending_now)
"""


def snapshot_pending(conn):
    """트랜잭션 안에서 호출: 지금 pending인 단지 목록을 고정한다."""
    conn.execute(SNAPSHOT)


def finish_snapshot(conn):
    """snapshot_pending 이후: 고정된 목록에만 좌표를 붙이고 못 찾은 것을 failed로 만든다."""
    ok = conn.execute(LOCATE, {"now": settings.now_ts()}).rowcount
    failed = conn.execute(MARK_FAILED).rowcount
    return {"ok": ok, "failed": failed}


def locate_pending(conn):
    """→ {"ok": 좌표를 붙인 단지 수, "failed": 이번에 못 찾은 단지 수}"""
    with conn.transaction():
        snapshot_pending(conn)
        return finish_snapshot(conn)


def retry_failed(conn):
    """address_points를 새로 적재했을 때: 못 찾았던 단지를 다시 찾게 한다."""
    return conn.execute(
        "UPDATE complexes SET geocode_status = 'pending' WHERE geocode_status = 'failed'").rowcount

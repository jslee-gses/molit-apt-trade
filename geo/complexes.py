"""단지(aptSeq) 목록: 거래에서 등록·갱신하고, 좌표 상태를 관리한다."""
from datetime import date

import settings

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

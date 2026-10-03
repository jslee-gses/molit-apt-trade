"""경계 버전 등록과 전환. 화면·집계가 쓰는 활성 버전은 항상 최대 하나다.

새 버전 폴더(geo_data/{version}, 배포에 포함)가 보이면 등록하고, 활성 버전보다 새로우면
모든 단지를 새 경계로 재판정(임시 테이블) → 전환 전 작업(집계) → 한 트랜잭션으로 활성화한다.
"""
import csv
import json
import logging

import settings
from geo import assign, hooks

log = logging.getLogger(__name__)
GEO_DATA = settings.BASE_DIR / "geo_data"


def active(conn):
    row = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchone()
    return row["version"] if row else None


def available(data_dir=None):
    d = data_dir or GEO_DATA
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "meta.json").exists())


def register(conn, version, data_dir=None):
    """geo_data/{version}을 boundary_versions(비활성)와 regions에 등록한다. 이미 있으면 False."""
    if conn.execute("SELECT 1 FROM boundary_versions WHERE version = %s", (version,)).fetchone():
        return False
    d = (data_dir or GEO_DATA) / version
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    with conn.transaction():
        conn.execute("INSERT INTO boundary_versions (version, source, loaded_at) VALUES (%s, %s, %s)",
                     (version, meta.get("source"), settings.now_ts()))
        with open(d / "regions.csv", encoding="utf-8", newline="") as f, conn.cursor() as cur, cur.copy(
                "COPY regions (boundary_version, region_cd, level, name, full_name, parent_cd) FROM STDIN") as copy:
            for r in csv.DictReader(f):
                copy.write_row([version, r["region_cd"], r["level"], r["name"], r["full_name"],
                                r["parent_cd"] or None])
    return True


def switch(conn, version, data_dir=None):
    """모든 단지를 version 경계로 재판정하고, 전환 전 훅을 거쳐 한 트랜잭션으로 활성화한다."""
    boundary = assign.Boundary.load(version, data_dir or GEO_DATA)
    rows = conn.execute("SELECT apt_seq, lon, lat, api_sgg_cd FROM complexes "
                        "WHERE geocode_status IN ('ok', 'manual') AND lon IS NOT NULL AND lat IS NOT NULL").fetchall()
    results = [assign.assign_row(boundary, r, version) for r in rows]
    with conn.transaction():
        conn.execute("CREATE TEMP TABLE staged_regions (apt_seq TEXT PRIMARY KEY, umd TEXT, sgg TEXT, "
                     "match TEXT, version TEXT, mismatch BOOLEAN) ON COMMIT DROP")
        with conn.cursor() as cur, cur.copy(
                "COPY staged_regions (apt_seq, umd, sgg, match, version, mismatch) FROM STDIN") as copy:
            for r in results:
                copy.write_row([r["apt_seq"], r["umd"], r["sgg"], r["match"], r["version"], r["mismatch"]])
        for hook in hooks.BEFORE_ACTIVATE:
            hook(conn, version)
        conn.execute("""
            UPDATE complexes c SET region_umd_cd = s.umd, region_sgg_cd = s.sgg, region_match = s.match,
                   boundary_version = s.version, sgg_mismatch = s.mismatch
              FROM staged_regions s WHERE c.apt_seq = s.apt_seq""")
        # 부분 유니크 인덱스(활성 1개) 때문에 먼저 모두 끄고 새 버전을 켠다
        conn.execute("UPDATE boundary_versions SET is_active = false WHERE is_active")
        conn.execute("UPDATE boundary_versions SET is_active = true WHERE version = %s", (version,))
        conn.execute("DELETE FROM regions WHERE boundary_version <> %s", (version,))
        for hook in hooks.AFTER_ACTIVATE:
            hook(conn, version)
    log.info("경계 버전 %s 활성화 (단지 %d개 재판정)", version, len(results))


def sync(conn, data_dir=None):
    """새 버전 폴더를 등록하고, 활성 버전보다 새 버전이 있으면 전환한다. → 전환한 버전 또는 None"""
    found = available(data_dir)
    for v in found:
        register(conn, v, data_dir)
    if not found:
        return None
    newest, current = found[-1], active(conn)
    if current is None or newest > current:
        switch(conn, newest, data_dir)
        return newest
    return None

"""경계 버전 등록과 전환. 화면·집계가 쓰는 활성 버전은 항상 최대 하나다.

새 버전 폴더(geo_data/{version}, 배포에 포함)가 보이면 등록하고, 활성 버전보다 새로우면
모든 단지를 새 경계로 재판정(임시 테이블) → 전환 전 작업(집계) → 한 트랜잭션으로 활성화한다.
"""
import csv
import json
import logging
import re

import settings
from geo import assign, complexes, hooks

log = logging.getLogger(__name__)
VERSION_RE = re.compile(r"^\d{4}-\d{2}$")


def active(conn):
    row = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchone()
    return row["version"] if row else None


def available(data_dir=None):
    d = data_dir or assign.GEO_DATA
    if not d.exists():
        return []
    return sorted(p.name for p in d.iterdir() if VERSION_RE.match(p.name) and (p / "meta.json").exists())


def register(conn, version, data_dir=None):
    """geo_data/{version}을 boundary_versions(비활성)와 regions에 등록한다. 이미 있으면 False."""
    if conn.execute("SELECT 1 FROM boundary_versions WHERE version = %s", (version,)).fetchone():
        return False
    d = (data_dir or assign.GEO_DATA) / version
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
    data_dir = data_dir or assign.GEO_DATA
    boundary = assign.Boundary.load(version, data_dir)   # 좌표 단지가 없어도 경계 파일을 전환 전에 검증한다
    results = assign.compute(conn, version, boundary=boundary, data_dir=data_dir, pending_only=False)
    with conn.transaction():
        complexes.lock_complexes(conn)
        conn.execute("CREATE TEMP TABLE staged_regions (apt_seq TEXT PRIMARY KEY, umd TEXT, sgg TEXT, "
                     "match TEXT, version TEXT, mismatch BOOLEAN, lon DOUBLE PRECISION, lat DOUBLE PRECISION) "
                     "ON COMMIT DROP")
        with conn.cursor() as cur, cur.copy(
                "COPY staged_regions (apt_seq, umd, sgg, match, version, mismatch, lon, lat) FROM STDIN") as copy:
            for r in results:
                copy.write_row([r["apt_seq"], r["umd"], r["sgg"], r["match"], r["version"], r["mismatch"],
                                r["lon"], r["lat"]])
        for hook in hooks.BEFORE_ACTIVATE:
            hook(conn, version)
        conn.execute("""
            UPDATE complexes c SET region_umd_cd = s.umd, region_sgg_cd = s.sgg, region_match = s.match,
                   boundary_version = s.version, sgg_mismatch = s.mismatch
              FROM staged_regions s
             WHERE c.apt_seq = s.apt_seq
               AND c.lon IS NOT DISTINCT FROM s.lon AND c.lat IS NOT DISTINCT FROM s.lat""")   # 계산 뒤 좌표가 바뀐 단지는 건너뛰고 다음 판정에 맡긴다
        # 부분 유니크 인덱스(활성 1개) 때문에 먼저 모두 끄고 새 버전을 켠다
        conn.execute("UPDATE boundary_versions SET is_active = false WHERE is_active")
        if conn.execute("UPDATE boundary_versions SET is_active = true WHERE version = %s",
                        (version,)).rowcount != 1:
            raise ValueError(f"등록되지 않은 경계 버전: {version}")
        conn.execute("DELETE FROM regions WHERE boundary_version <> %s", (version,))
        for hook in hooks.AFTER_ACTIVATE:
            hook(conn, version)
    log.info("경계 버전 %s 활성화 (단지 %d개 재판정)", version, len(results))


def sync(conn, data_dir=None, skip=()):
    """새 버전 폴더를 등록하고, 활성 버전보다 새 버전이 있으면 전환한다. → 전환한 버전 또는 None. skip에 든 버전으로는 전환하지 않는다"""
    found = available(data_dir)
    for v in found:
        register(conn, v, data_dir)
    if not found:
        return None
    newest, current = found[-1], active(conn)
    if newest in skip:
        return None
    if current is None or newest > current:
        switch(conn, newest, data_dir)
        return newest
    return None

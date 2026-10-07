"""단지가 최신 읍면동 중 어디에 속하는지 정한다.

좌표(ok·manual)가 있으면 경계(shapely STRtree)로 판정하고(within / nearest), 좌표가 없거나 경계 밖이면
거래의 법정동 코드(api_sgg_cd + api_umd_cd 앞 3자리)를 대응표(geo/code_map.csv)로 바꿔 판정한다(code / none).
geo_data/{version}/umd_assign.geojson.gz는 좌표 있는 단지를 판정할 때만 읽고, 끝나면 버린다(서버 메모리 절약).
"""
import gzip
import json
import math
import re

from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

import settings
from geo import code_map, complexes, hooks

GEO_DATA = settings.BASE_DIR / "geo_data"
NEAREST_M = 200
BATCH = 5000   # 한 트랜잭션에 반영할 단지 수(단지 잠금을 오래 쥐지 않게)
_M_PER_DEG_LAT = 110_540
_M_PER_DEG_LON_EQ = 111_320
LOCATED = ("ok", "manual")
_D5 = re.compile(r"[0-9]{5}")
_D3 = re.compile(r"[0-9]{3}")


def _meters(lon1, lat1, lon2, lat2):
    dx = (lon2 - lon1) * _M_PER_DEG_LON_EQ * math.cos(math.radians((lat1 + lat2) / 2))
    dy = (lat2 - lat1) * _M_PER_DEG_LAT
    return math.hypot(dx, dy)


class Boundary:
    def __init__(self, features):
        self.codes = [f["properties"]["region_cd"] for f in features]
        self.geoms = [shape(f["geometry"]) for f in features]
        self.tree = STRtree(self.geoms)

    @classmethod
    def load(cls, version, data_dir=None):
        path = (data_dir or GEO_DATA) / version / "umd_assign.geojson.gz"
        return cls(json.loads(gzip.decompress(path.read_bytes()))["features"])

    def locate(self, lon, lat):
        """→ (읍면동 코드 또는 None, 'within' | 'nearest' | 'none')"""
        p = Point(lon, lat)
        hits = self.tree.query(p, predicate="intersects")
        if len(hits):
            return min(self.codes[i] for i in hits), "within"
        # 경도 1도가 위도 1도보다 짧으므로 경도 기준 반경이면 후보를 빠뜨리지 않는다
        # 가장 가까운 점은 도(degree) 공간에서 찾고 미터로 환산하므로 거리는 근사값이다(오차 몇 % 수준).
        radius_deg = NEAREST_M / (_M_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
        best = None
        for i in self.tree.query(p.buffer(radius_deg)):
            q, _ = nearest_points(self.geoms[i], p)
            d = _meters(lon, lat, q.x, q.y)
            if d <= NEAREST_M and (best is None or (d, self.codes[i]) < best):
                best = (d, self.codes[i])
        return (best[1], "nearest") if best else (None, "none")


def code_region(sgg, umd, valid, mapping):
    """법정동 코드 → 활성 경계의 읍면동 코드(대응표 반영). 형식이 틀리거나 경계에 없으면 None."""
    sgg, umd = (sgg or "").strip(), (umd or "").strip()
    if not (_D5.fullmatch(sgg) and _D3.fullmatch(umd[:3])):
        return None
    emd = sgg + umd[:3]
    emd = mapping.get(emd, emd)
    return emd if emd in valid else None


def assign_row(boundary, row, version, valid=frozenset(), mapping=None):
    lon, lat = row.get("lon"), row.get("lat")
    umd, match, mismatch = None, "none", False
    if (boundary is not None and row.get("geocode_status", "ok") in LOCATED
            and lon is not None and lat is not None):
        x, y = float(lon), float(lat)
        if math.isfinite(x) and math.isfinite(y):
            umd, match = boundary.locate(x, y)
            mismatch = bool(umd and row.get("api_sgg_cd") and umd[:5] != row["api_sgg_cd"])
    if umd is None:
        umd = code_region(row.get("api_sgg_cd"), row.get("api_umd_cd"), valid, mapping or {})
        match = "code" if umd else "none"
    return dict(apt_seq=row["apt_seq"], umd=umd, sgg=umd[:5] if umd else None, match=match,
                version=version, lon=lon, lat=lat, mismatch=mismatch)


# 계산 뒤 좌표가 바뀐 단지는 건드리지 않는다(다음 실행에서 다시 판정)
UPDATE = """
UPDATE complexes SET region_umd_cd = %(umd)s, region_sgg_cd = %(sgg)s, region_match = %(match)s,
       boundary_version = %(version)s, sgg_mismatch = %(mismatch)s
 WHERE apt_seq = %(apt_seq)s
   AND lon IS NOT DISTINCT FROM %(lon)s::float8 AND lat IS NOT DISTINCT FROM %(lat)s::float8
"""


def valid_umd(conn, version):
    return {r["region_cd"] for r in conn.execute(
        "SELECT region_cd FROM regions WHERE boundary_version = %s AND level = 'umd'", (version,))}


def compute(conn, version, boundary=None, data_dir=None, pending_only=True):
    """판정 결과 목록(잠금 없이 계산). pending_only면 이 버전으로 아직 판정하지 않은 단지만."""
    sql = "SELECT apt_seq, lon, lat, api_sgg_cd, api_umd_cd, geocode_status FROM complexes"
    rows = conn.execute(sql + " WHERE boundary_version IS DISTINCT FROM %s", (version,)).fetchall() \
        if pending_only else conn.execute(sql).fetchall()
    if not rows:
        return []
    if boundary is None and any(r["geocode_status"] in LOCATED and r["lon"] is not None and r["lat"] is not None
                                for r in rows):
        boundary = Boundary.load(version, data_dir)
    valid, mapping = valid_umd(conn, version), code_map.read()
    return [assign_row(boundary, r, version, valid, mapping) for r in rows]


def compute_pending(conn, version, boundary=None):
    return compute(conn, version, boundary)


def apply_results(conn, results):
    """판정 결과를 BATCH건씩 나눠 반영한다(묶음마다 트랜잭션·잠금·훅). 계산 뒤 좌표가 바뀐 단지는 건드리지 않아
    다음 실행에서 다시 판정된다. → 전체 건수"""
    for i in range(0, len(results), BATCH):
        chunk = results[i:i + BATCH]
        seqs = [r["apt_seq"] for r in chunk]
        with conn.transaction():
            complexes.lock_complexes(conn)
            for hook in hooks.ON_REGION_CHANGE:   # 바뀌기 전 지역
                hook(conn, seqs)
            with conn.cursor() as cur:
                cur.executemany(UPDATE, chunk)
            for hook in hooks.ON_REGION_CHANGE:   # 바뀐 뒤 지역
                hook(conn, seqs)
    return len(results)


def assign_pending(conn, version, boundary=None):
    """→ 판정한 단지 수"""
    results = compute_pending(conn, version, boundary)
    return apply_results(conn, results) if results else 0

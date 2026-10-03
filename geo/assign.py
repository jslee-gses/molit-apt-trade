"""단지 좌표가 최신 읍면동 경계 중 어디에 속하는지 정한다(shapely STRtree).

geo_data/{version}/umd_assign.geojson.gz를 판정할 단지가 있을 때만 읽고, 끝나면 버린다(서버 메모리 절약).
"""
import gzip
import json
import math

from shapely import STRtree
from shapely.geometry import Point, shape
from shapely.ops import nearest_points

import settings
from geo import hooks

GEO_DATA = settings.BASE_DIR / "geo_data"
NEAREST_M = 200
_M_PER_DEG_LAT = 110_540
_M_PER_DEG_LON_EQ = 111_320


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
        radius_deg = NEAREST_M / (_M_PER_DEG_LON_EQ * math.cos(math.radians(lat)))
        best = None
        for i in self.tree.query(p.buffer(radius_deg)):
            q, _ = nearest_points(self.geoms[i], p)
            d = _meters(lon, lat, q.x, q.y)
            if d <= NEAREST_M and (best is None or (d, self.codes[i]) < best):
                best = (d, self.codes[i])
        return (best[1], "nearest") if best else (None, "none")


def assign_row(boundary, row, version):
    umd, match = boundary.locate(row["lon"], row["lat"])
    sgg = umd[:5] if umd else None
    return dict(apt_seq=row["apt_seq"], umd=umd, sgg=sgg, match=match, version=version,
                mismatch=bool(sgg and row["api_sgg_cd"] and sgg != row["api_sgg_cd"]))


UPDATE = """
UPDATE complexes SET region_umd_cd = %(umd)s, region_sgg_cd = %(sgg)s, region_match = %(match)s,
       boundary_version = %(version)s, sgg_mismatch = %(mismatch)s
 WHERE apt_seq = %(apt_seq)s
"""


def assign_pending(conn, version, boundary=None):
    """좌표가 있는데 아직 이 경계 버전으로 판정하지 않은 단지를 판정한다. → 판정한 단지 수"""
    rows = conn.execute("""
        SELECT apt_seq, lon, lat, api_sgg_cd FROM complexes
         WHERE geocode_status IN ('ok', 'manual') AND boundary_version IS DISTINCT FROM %s""",
                        (version,)).fetchall()
    if not rows:
        return 0
    boundary = boundary or Boundary.load(version)
    results = [assign_row(boundary, r, version) for r in rows]
    seqs = [r["apt_seq"] for r in rows]
    with conn.transaction():
        for hook in hooks.ON_REGION_CHANGE:   # 바뀌기 전 지역
            hook(conn, seqs)
        with conn.cursor() as cur:
            cur.executemany(UPDATE, results)
        for hook in hooks.ON_REGION_CHANGE:   # 바뀐 뒤 지역
            hook(conn, seqs)
    return len(results)

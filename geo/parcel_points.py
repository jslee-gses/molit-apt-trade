"""[로컬 실행] 국토교통부 연속지적도형정보(브이월드, CC BY)로 단지 좌표를 만든다.

사용:
  python -m geo.parcel_points --dir <시도별 AL_D002_*.zip 폴더> [--database-url URL] [--dry-run] [--yes]

- 대상: 좌표가 없는 단지(geocode_status pending·failed). 수동 좌표(manual)와 이미 찾은 단지(ok)는 건드리지 않는다.
- 단지마다 거래에 가장 많이 나온 필지고유번호(PNU)를 고르고, 그 필지 안쪽의 대표점(representative_point)을
  경위도(EPSG:4326, 소수 6자리)로 바꿔 저장한다. 같은 PNU가 여러 조각이면 합친다.
- 저장하면 지역 판정을 지워 다음 지리 처리(10분 주기)가 경계로 다시 판정한다. 필지를 못 찾은 단지는 failed.
- 시작할 때 대상 DB(host:port/dbname)를 출력한다. 로컬이 아닌 DB는 --yes가 있어야 진행한다.
- 이 도구는 마이그레이션을 실행하지 않는다(운영 스키마는 배포된 앱이 관리).
"""
import argparse
import math
import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

SRC_CRS = "EPSG:5186"          # 파일에 .prj가 없을 때
LON_RANGE = (124.0, 132.0)
LAT_RANGE = (33.0, 39.0)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_PNU = re.compile(r"[0-9]{19}")

TARGETS = """
SELECT DISTINCT ON (k.apt_seq) k.apt_seq, k.p
  FROM (SELECT t.apt_seq, pnu(t.sgg_cd, t.umd_cd, t.land_cd, t.bonbun, t.bubun) AS p, COUNT(*) AS n
          FROM trades t JOIN complexes c ON c.apt_seq = t.apt_seq
         WHERE c.geocode_status IN ('pending', 'failed')
         GROUP BY 1, 2) k
 WHERE k.p IS NOT NULL
 ORDER BY k.apt_seq, k.n DESC, k.p
"""


def targets(conn):
    """→ ({단지: 대표 PNU}, 대상 단지 수). PNU를 만들 수 없는 단지는 dict에 없다."""
    got = {r["apt_seq"]: r["p"] for r in conn.execute(TARGETS)}
    n = conn.execute("SELECT COUNT(*) AS n FROM complexes WHERE geocode_status IN ('pending', 'failed')").fetchone()["n"]
    return got, n


def remap(pnu, mapping):
    """개편 전 코드의 PNU → 대응표(geo/code_map.csv, 읍면동 8자리)로 바꾼 새 PNU. 리 코드·지번은 그대로. 바뀌지 않으면 None."""
    new = mapping.get(pnu[:8])
    return new + pnu[8:] if new and new != pnu[:8] else None


def shp_sources(path):
    """zip 안의 .shp들을 GDAL 가상 경로로(큰 시도는 'AL_D002_41_…(2).shp'처럼 여러 조각). .shp 파일이면 그대로."""
    path = Path(path)
    if path.suffix.lower() != ".zip":
        return [str(path)]
    with zipfile.ZipFile(path) as z:
        names = sorted(n for n in z.namelist() if n.lower().endswith(".shp"))
    if not names:
        raise ValueError(f"{path.name}: zip 안에 .shp가 없습니다")
    return [f"/vsizip/{path.as_posix()}/{n}" for n in names]


def pnu_column(src):
    """첫 행들에서 값이 모두 19자리 숫자인 속성 열(연속지적도는 A1)."""
    import pyogrio

    head = pyogrio.read_dataframe(src, read_geometry=False, max_features=50)
    for col in head.columns:
        vals = head[col].dropna().astype(str)
        if len(vals) and vals.map(lambda v: bool(_PNU.fullmatch(v))).all():
            return col
    raise ValueError(f"{src}: PNU(19자리 숫자) 열을 찾지 못했습니다. 연속지적도형정보 파일인지 확인하세요.")


def read_points(paths, wanted):
    """→ ({PNU: (lon, lat)}, 통계). 파일마다 PNU 열만 읽어 거른 뒤 필요한 행의 도형만 읽는다(메모리 절약)."""
    import geopandas as gpd
    import pyogrio

    out, stats = {}, {"files": 0, "parcels": 0, "matched": 0, "dropped": 0}
    for path in paths:
        stats["files"] += 1
        for src in shp_sources(path):
            col = pnu_column(src)
            ids = pyogrio.read_dataframe(src, columns=[col], read_geometry=False, fid_as_index=True)
            stats["parcels"] += len(ids)
            fids = ids.index[ids[col].isin(wanted)].tolist()
            if not fids:
                continue
            gdf = pyogrio.read_dataframe(src, columns=[col], fids=fids)
            if gdf.crs is None:
                gdf = gdf.set_crs(SRC_CRS)
            gdf["geometry"] = gdf.geometry.make_valid()          # 자기 교차 등 잘못된 도형 보정(GEOS 오류 방지)
            empty = gdf.geometry.is_empty | gdf.geometry.isna()
            stats["dropped"] += int(empty.sum())
            gdf = gdf[~empty]
            if gdf.empty:
                continue
            merged = gdf.dissolve(by=col)
            pts = gpd.GeoSeries(merged.geometry.representative_point(), crs=gdf.crs).to_crs("EPSG:4326")
            for p, geom in pts.items():
                lon, lat = geom.x, geom.y
                if (math.isfinite(lon) and math.isfinite(lat)
                        and LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
                    if p not in out:
                        stats["matched"] += 1
                    out[p] = (round(lon, 6), round(lat, 6))
                else:
                    stats["dropped"] += 1
    return out, stats


def save(conn, found, target_seqs):
    """found: {단지: (lon, lat)}. 대상(target_seqs) 중 못 찾은 단지는 failed. 저장 시점에 상태가 바뀐 단지는 건너뛴다.
    → {"located", "failed", "skipped"}"""
    import settings
    from geo import complexes, hooks

    with conn.transaction():
        complexes.lock_complexes(conn)
        still = {r["apt_seq"] for r in conn.execute(
            "SELECT apt_seq FROM complexes WHERE apt_seq = ANY(%s) AND geocode_status IN ('pending', 'failed') "
            "FOR UPDATE", (list(target_seqs),))}
        seqs = sorted(s for s in found if s in still)
        if seqs:
            for hook in hooks.ON_REGION_CHANGE:   # 바뀌기 전 지역
                hook(conn, seqs)
            now = settings.now_ts()
            # 단지마다 UPDATE를 보내면 원격 DB 왕복 지연이 쌓이므로 임시 테이블 COPY + UPDATE 한 번
            conn.execute("CREATE TEMP TABLE parcel_stage (apt_seq TEXT PRIMARY KEY, lon DOUBLE PRECISION, "
                         "lat DOUBLE PRECISION) ON COMMIT DROP")
            with conn.cursor() as cur, cur.copy("COPY parcel_stage (apt_seq, lon, lat) FROM STDIN") as copy:
                for s in seqs:
                    copy.write_row([s, found[s][0], found[s][1]])
            conn.execute("""
                UPDATE complexes c SET lon = s.lon, lat = s.lat, geocode_status = 'ok', geocode_source = 'parcel',
                       geocoded_at = %s, region_sgg_cd = NULL, region_umd_cd = NULL, region_match = NULL,
                       boundary_version = NULL, sgg_mismatch = false
                  FROM parcel_stage s
                 WHERE c.apt_seq = s.apt_seq AND c.geocode_status IN ('pending', 'failed')""", (now,))
            for hook in hooks.ON_REGION_CHANGE:   # 바뀐 뒤 지역
                hook(conn, seqs)
        missing = sorted(still - set(seqs))
        failed = conn.execute("UPDATE complexes SET geocode_status = 'failed' "
                              "WHERE apt_seq = ANY(%s) AND geocode_status = 'pending'", (missing,)).rowcount
    skipped = len([s for s in target_seqs if s not in still])
    return {"located": len(seqs), "failed": failed, "skipped": skipped}


def describe_target(url):
    """비밀번호 없이 host:port/dbname만. → (표시 문자열, host)"""
    u = urlparse(url)
    return f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}", u.hostname


def main(argv=None):
    parser = argparse.ArgumentParser(description="연속지적도형정보 → 단지 좌표")
    parser.add_argument("--dir", required=True, help="시도별 연속지적도 zip(AL_D002_*.zip)이 있는 폴더")
    parser.add_argument("--database-url", help="대상 DB(기본: DATABASE_URL)")
    parser.add_argument("--dry-run", action="store_true", help="저장하지 않고 통계만 출력")
    parser.add_argument("--yes", action="store_true", help="로컬이 아닌 DB에 저장하는 것을 확인함")
    args = parser.parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    import settings

    url = settings.env("DATABASE_URL")
    if not url:
        print("DATABASE_URL이 없습니다. --database-url을 주거나 .env를 설정하세요.")
        return 1
    shown, host = describe_target(url)
    print(f"대상 DB: {shown}")
    if host not in LOCAL_HOSTS and not args.yes and not args.dry_run:
        print("로컬이 아닌 DB입니다. 확인했다면 --yes를 붙여 다시 실행하세요.")
        return 1

    paths = sorted(Path(args.dir).glob("*.zip"))
    if not paths:
        print(f"{args.dir}에 zip 파일이 없습니다.")
        return 1

    import db
    import wiring

    db.close_pool()      # 환경변수를 바꿨을 수 있으므로 새 풀로 연결한다
    wiring.wire()        # 좌표가 바뀐 단지의 집계 대기열 훅
    with db.connection() as conn:
        wanted_by_seq, n_targets = targets(conn)
    db.close_pool()      # 오래 걸리는 파일 읽기 동안 유휴 연결을 잡고 있지 않는다

    from geo import code_map

    # 개편 전 코드로 신고된 거래는 지적도(새 코드)와 PNU가 다르므로 대응표로 바꾼 PNU도 함께 찾는다
    mapping = code_map.read()
    alt = {s: remap(p, mapping) for s, p in wanted_by_seq.items()}
    points, stats = read_points(paths, set(wanted_by_seq.values()) | {a for a in alt.values() if a})
    found, via_map = {}, 0
    for s, p in wanted_by_seq.items():
        if p in points:
            found[s] = points[p]
        elif alt[s] in points:
            found[s] = points[alt[s]]
            via_map += 1
    pct = 100 * len(found) / n_targets if n_targets else 0.0
    print(f"zip {stats['files']}개 · 필지 {stats['parcels']:,}개 읽음 · 대상 단지 {n_targets:,} · "
          f"PNU 없음 {n_targets - len(wanted_by_seq):,} · 찾음 {len(found):,}({pct:.1f}%, 개편 코드로 찾음 {via_map:,}) · "
          f"못 찾음 {len(wanted_by_seq) - len(found):,} · 범위 밖 {stats['dropped']:,}")
    if args.dry_run:
        print("--dry-run: 저장하지 않습니다.")
        return 0
    if not found:
        print("찾은 단지가 없습니다. 저장하지 않습니다.")
        return 1
    with db.connection() as conn:
        result = save(conn, found, list(wanted_by_seq))   # 대상 = PNU를 만든 단지(PNU 없는 단지는 pending으로 남는다)
    print(f"저장: 좌표 {result['located']:,} · failed {result['failed']:,} · 상태가 바뀌어 건너뜀 {result['skipped']:,}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())

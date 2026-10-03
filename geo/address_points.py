"""[로컬 실행] 주소정보누리집 '위치정보요약DB'를 address_points 테이블에 적재한다.

사용:
  python -m geo.address_points --dir <압축 푼 폴더> --month 202609 [--database-url URL] [--encoding cp949]

- 파일: '|' 구분 텍스트, 열 순서는 COLUMNS, 좌표는 UTM-K(EPSG:5179).
- 건물(도로명주소 키)마다 출입구 일련번호가 가장 작은 출입구 하나만 쓴다.
- 적재 대상: 건물용도가 공동주택인 건물 + 거래 데이터에 나온 도로명주소 키(용도와 무관).
- 테이블을 통째로 교체하고, 좌표를 못 찾았던 단지(failed)를 다시 찾게 한다.
- 운영 DB에 적재할 때는 --database-url에 Railway Postgres의 공개 연결 문자열을 준다.
  이 도구는 마이그레이션을 실행하지 않는다(운영 스키마는 배포된 앱이 관리).
"""
import argparse
import os
import sys
from pathlib import Path

COLUMNS = ["sgg_cd", "entrance_seq", "bjd_cd", "sido_nm", "sgg_nm", "emd_nm", "road_cd", "road_nm",
           "underground", "bonbun", "bubun", "bld_nm", "zip_cd", "bld_use", "bld_group", "adm_dong", "x", "y"]
APT_USE = "공동주택"
SRC_CRS = "EPSG:5179"


def parse_line(line):
    parts = line.rstrip("\r\n").split("|")
    if len(parts) < len(COLUMNS):
        return None
    return {c: parts[i].strip() for i, c in enumerate(COLUMNS)}


def read_points(paths, wanted, encoding="cp949"):
    """→ {도로명주소 키: (출입구 일련번호, x, y, 건물명)}"""
    from geo.keys import road_key

    best = {}
    for path in paths:
        with open(path, encoding=encoding, errors="replace") as f:
            for raw in f:
                r = parse_line(raw)
                if r is None:
                    continue
                road = r["road_cd"]
                key = road_key(road[:5], road[5:], r["underground"], r["bonbun"], r["bubun"])
                if key is None or (key not in wanted and APT_USE not in r["bld_use"]):
                    continue
                try:
                    x, y, seq = float(r["x"]), float(r["y"]), int(r["entrance_seq"] or 0)
                except ValueError:
                    continue
                cur = best.get(key)
                if cur is None or seq < cur[0]:
                    best[key] = (seq, x, y, r["bld_nm"])
    return best


def to_lonlat(points):
    from pyproj import Transformer

    tr = Transformer.from_crs(SRC_CRS, "EPSG:4326", always_xy=True)
    keys = list(points)
    lons, lats = tr.transform([points[k][1] for k in keys], [points[k][2] for k in keys])
    return [(k, float(lon), float(lat), points[k][3]) for k, lon, lat in zip(keys, lons, lats)]


def wanted_keys(conn):
    rows = conn.execute("""
        SELECT DISTINCT road_key(road_nm_sgg_cd, road_nm_cd, road_nmb_cd, road_nm_bonbun, road_nm_bubun) AS k
          FROM trades""").fetchall()
    return {r["k"] for r in rows if r["k"]}


def load(conn, rows, month):
    """address_points를 rows로 통째로 교체하고 failed 단지를 다시 찾게 한다. → 적재 행 수"""
    from geo import locate

    with conn.transaction():
        conn.execute("TRUNCATE address_points")
        with conn.cursor() as cur, cur.copy(
                "COPY address_points (road_key, lon, lat, bld_nm, source_month) FROM STDIN") as copy:
            for key, lon, lat, name in rows:
                copy.write_row([key, lon, lat, name or None, month])
        locate.retry_failed(conn)
    return len(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description="위치정보요약DB → address_points 적재")
    parser.add_argument("--dir", required=True, help="압축을 푼 위치정보요약DB 폴더(하위 폴더의 *.txt 모두)")
    parser.add_argument("--month", required=True, help="자료 기준월 YYYYMM")
    parser.add_argument("--database-url", help="적재할 DB(기본: DATABASE_URL)")
    parser.add_argument("--encoding", default="cp949")
    args = parser.parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    import db

    paths = sorted(Path(args.dir).rglob("*.txt"))
    if not paths:
        print(f"{args.dir}에 .txt 파일이 없습니다.")
        return 1
    with db.connection() as conn:
        wanted = wanted_keys(conn)
        points = read_points(paths, wanted, args.encoding)
        rows = to_lonlat(points)
        n = load(conn, rows, args.month)
        matched = len(wanted & set(points))
    print(f"파일 {len(paths)}개 · 적재 {n:,}건 · 거래 도로명주소 키 {len(wanted):,}개 중 {matched:,}개 일치")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())

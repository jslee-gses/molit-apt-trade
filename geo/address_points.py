"""[로컬 실행] 주소정보누리집 '위치정보요약DB'를 address_points 테이블에 적재한다.

사용:
  python -m geo.address_points --dir <압축 푼 폴더> --month 202609 [--database-url URL] [--encoding cp949] [--yes]

- 파일: '|' 구분 텍스트, 열 순서는 COLUMNS, 좌표는 UTM-K(EPSG:5179).
- 건물(도로명주소 키)마다 출입구 일련번호가 가장 작은 출입구 하나만 쓴다.
- 적재 대상: 건물용도가 공동주택인 건물 + 거래 데이터에 나온 도로명주소 키(용도와 무관).
- 테이블을 통째로 교체하고, 좌표를 못 찾았던 단지(failed)를 다시 찾게 한다.
- 시작할 때 대상 DB(host:port/dbname)를 출력한다. 로컬이 아닌 DB는 --yes가 있어야 진행한다.
- 공동주택 줄이 하나도 없거나 적재할 좌표가 없으면(인코딩·형식 오류 가능성) 적재하지 않는다.
- 운영 DB에 적재할 때는 --database-url에 Railway Postgres의 공개 연결 문자열을 준다.
  이 도구는 마이그레이션을 실행하지 않는다(운영 스키마는 배포된 앱이 관리).
"""
import argparse
import math
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

COLUMNS = ["sgg_cd", "entrance_seq", "bjd_cd", "sido_nm", "sgg_nm", "emd_nm", "road_cd", "road_nm",
           "underground", "bonbun", "bubun", "bld_nm", "zip_cd", "bld_use", "bld_group", "adm_dong", "x", "y"]
APT_USE = "공동주택"
SRC_CRS = "EPSG:5179"
LON_RANGE = (124.0, 132.0)
LAT_RANGE = (33.0, 39.0)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def parse_line(line):
    parts = line.rstrip("\r\n").split("|")
    if len(parts) < len(COLUMNS):
        return None
    return {c: parts[i].strip() for i, c in enumerate(COLUMNS)}


def read_points(paths, wanted, encoding="cp949"):
    """→ ({도로명주소 키: (출입구 일련번호, x, y, 건물명)}, 통계)

    통계: lines(읽은 줄), short(열 부족), bad_key(키 형식 오류), bad_number(좌표·번호 오류),
    apt_rows(건물용도에 '공동주택'이 든 줄 수; 0이면 인코딩·형식이 틀렸을 가능성이 크다).
    """
    from geo.keys import road_key

    best = {}
    stats = {"lines": 0, "short": 0, "bad_key": 0, "bad_number": 0, "apt_rows": 0}
    for path in paths:
        with open(path, encoding=encoding, errors="replace") as f:
            for raw in f:
                if not raw.strip():
                    continue
                stats["lines"] += 1
                r = parse_line(raw)
                if r is None:
                    stats["short"] += 1
                    continue
                is_apt = APT_USE in r["bld_use"]
                stats["apt_rows"] += is_apt
                road = r["road_cd"]
                key = road_key(road[:5], road[5:], r["underground"], r["bonbun"], r["bubun"])
                if key is None:
                    stats["bad_key"] += 1
                    continue
                if key not in wanted and not is_apt:
                    continue
                try:
                    x, y, seq = float(r["x"]), float(r["y"]), int(r["entrance_seq"] or 0)
                except ValueError:
                    stats["bad_number"] += 1
                    continue
                cur = best.get(key)
                if cur is None or seq < cur[0]:
                    best[key] = (seq, x, y, r["bld_nm"])
    return best, stats


def to_lonlat(points):
    """UTM-K → WGS84. → ([(키, lon, lat, 건물명)], 버린 수). 유한하지 않거나 한반도 범위 밖이면 버린다."""
    from pyproj import Transformer

    tr = Transformer.from_crs(SRC_CRS, "EPSG:4326", always_xy=True)
    keys = list(points)
    lons, lats = tr.transform([points[k][1] for k in keys], [points[k][2] for k in keys])
    rows, dropped = [], 0
    for k, lon, lat in zip(keys, lons, lats):
        if (math.isfinite(lon) and math.isfinite(lat)
                and LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
            rows.append((k, float(lon), float(lat), points[k][3]))
        else:
            dropped += 1
    return rows, dropped


def wanted_keys(conn):
    rows = conn.execute("""
        SELECT DISTINCT road_key(road_nm_sgg_cd, road_nm_cd, road_nmb_cd, road_nm_bonbun, road_nm_bubun) AS k
          FROM trades""").fetchall()
    return {r["k"] for r in rows if r["k"]}


def load(conn, rows, month):
    """address_points를 rows로 통째로 교체하고 failed 단지를 다시 찾게 한다. → 적재 행 수"""
    from geo import complexes, locate

    if not rows:
        raise ValueError("적재할 행이 없습니다(빈 목록으로 address_points를 비울 수 없음)")
    with conn.transaction():
        complexes.lock_complexes(conn)   # 좌표 연결(locate)과 같은 순서: 단지 잠금 → address_points
        conn.execute("TRUNCATE address_points")
        with conn.cursor() as cur, cur.copy(
                "COPY address_points (road_key, lon, lat, bld_nm, source_month) FROM STDIN") as copy:
            for key, lon, lat, name in rows:
                copy.write_row([key, lon, lat, name or None, month])
        locate.retry_failed(conn)
    return len(rows)


def describe_target(url):
    """비밀번호 없이 host:port/dbname만. → (표시 문자열, host)"""
    u = urlparse(url)
    return f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}", u.hostname


def main(argv=None):
    parser = argparse.ArgumentParser(description="위치정보요약DB → address_points 적재")
    parser.add_argument("--dir", required=True, help="압축을 푼 위치정보요약DB 폴더(하위 폴더의 *.txt 모두)")
    parser.add_argument("--month", required=True, help="자료 기준월 YYYYMM")
    parser.add_argument("--database-url", help="적재할 DB(기본: DATABASE_URL)")
    parser.add_argument("--encoding", default="cp949")
    parser.add_argument("--yes", action="store_true", help="로컬이 아닌 DB에 적재하는 것을 확인함")
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
    if host not in LOCAL_HOSTS and not args.yes:
        print("로컬이 아닌 DB입니다. 확인했다면 --yes를 붙여 다시 실행하세요.")
        return 1

    paths = sorted(Path(args.dir).rglob("*.txt"))
    if not paths:
        print(f"{args.dir}에 .txt 파일이 없습니다.")
        return 1

    import db

    with db.connection() as conn:
        wanted = wanted_keys(conn)
    db.close_pool()  # 오래 걸리는 파일 스캔 동안 유휴 연결을 잡고 있지 않는다

    points, stats = read_points(paths, wanted, args.encoding)
    rows, dropped = to_lonlat(points)
    print(f"파일 {len(paths)}개 · {stats['lines']:,}줄 · 공동주택 {stats['apt_rows']:,}줄 · "
          f"건너뜀: 열 부족 {stats['short']:,}, 키 오류 {stats['bad_key']:,}, 숫자 오류 {stats['bad_number']:,}, "
          f"좌표 범위 밖 {dropped:,}")
    if stats["apt_rows"] == 0:
        print("공동주택 줄이 하나도 없습니다. 인코딩(--encoding)이나 파일 형식을 확인하세요. 적재하지 않습니다.")
        return 1
    if not rows:
        print("적재할 좌표가 없습니다. 적재하지 않습니다.")
        return 1

    with db.connection() as conn:
        n = load(conn, rows, args.month)
    matched = len(wanted & set(points))
    print(f"적재 {n:,}건 · 거래 도로명주소 키 {len(wanted):,}개 중 {matched:,}개 일치")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())

"""[로컬 개발용] 화면 확인용 가짜 데이터를 개발 DB에 만든다. 운영 DB에는 쓰지 않는다.

사용: python scripts/seed_dev.py [--reset]
- 경계 버전 0000-dev(서울 3개·부산 2개 시군구, 각 3개 읍면동 사각형)를 static/geo, geo_data에 쓴다(저장소 제외).
- 2023-01 ~ 이번 달 거래를 단지마다 무작위로 만들고, 단지 좌표·지역 판정·집계까지 만든다.
"""
import argparse
import csv
import gzip
import json
import random
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import settings  # noqa: E402
import wiring  # noqa: E402
from collector import codes, store  # noqa: E402
from geo import versions  # noqa: E402
from shapely.geometry import box, mapping  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

VERSION = "0000-dev"
SGG = {"11110": (126.95, 37.58), "11140": (126.98, 37.58), "11170": (126.95, 37.55),
       "26110": (129.02, 35.10), "26140": (128.99, 35.10)}
SIZE = 0.03   # 시군구 한 변(도)
BASE_PRICE = {"11": 90000, "26": 40000}


def squares():
    """읍면동 코드 → (lon0, lat0, lon1, lat1). 시군구 사각형을 세로 3칸으로 나눈다."""
    out = {}
    for sgg, (x, y) in SGG.items():
        for i in range(3):
            out[f"{sgg}{101 + i:03d}"] = (x + i * SIZE / 3, y, x + (i + 1) * SIZE / 3, y + SIZE)
    return out


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def write_boundary():
    names = {r.LAWD_CD: (r.시도, r.시군구) for r in codes.load_codes().itertuples()}
    sq = squares()
    umd = {c: box(*b) for c, b in sq.items()}
    sgg = {s: unary_union([g for c, g in umd.items() if c[:5] == s]) for s in SGG}
    sido = {d: unary_union([g for s, g in sgg.items() if s[:2] == d]) for d in {s[:2] for s in SGG}}
    feat = lambda geom, **props: {"type": "Feature", "properties": props, "geometry": mapping(geom)}  # noqa: E731
    web = settings.BASE_DIR / "static" / "geo" / VERSION
    data = settings.BASE_DIR / "geo_data" / VERSION
    web.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    (web / "sido.json").write_text(json.dumps(fc([feat(g, region_cd=d, name=names[next(s for s in SGG if s[:2] == d)][0])
                                                  for d, g in sido.items()]), ensure_ascii=False), encoding="utf-8")
    (web / "sgg.json").write_text(json.dumps(fc([feat(g, region_cd=s, name=names[s][1], sido_cd=s[:2])
                                                 for s, g in sgg.items()]), ensure_ascii=False), encoding="utf-8")
    for d in sido:
        (web / f"umd_{d}.json").write_text(json.dumps(fc([feat(g, region_cd=c, name=f"{c[-1]}동", sgg_cd=c[:5])
                                                          for c, g in umd.items() if c[:2] == d]),
                                                      ensure_ascii=False), encoding="utf-8")
    (data / "umd_assign.geojson.gz").write_bytes(gzip.compress(json.dumps(fc(
        [feat(g, region_cd=c, sgg_cd=c[:5]) for c, g in umd.items()])).encode()))
    with open(data / "regions.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["region_cd", "level", "name", "full_name", "parent_cd"])
        for d in sorted(sido):
            sname = names[next(s for s in SGG if s[:2] == d)][0]
            w.writerow([d, "sido", sname, sname, ""])
        for s in SGG:
            w.writerow([s, "sgg", names[s][1], f"{names[s][0]} {names[s][1]}", s[:2]])
        for c in sq:
            w.writerow([c, "umd", f"{c[-1]}동", f"{names[c[:5]][0]} {names[c[:5]][1]} {c[-1]}동", c[:5]])
    (data / "meta.json").write_text(json.dumps({"version": VERSION, "source": "seed_dev"}), encoding="utf-8")
    return sq


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="기존 데이터를 지우고 다시 만든다")
    args = parser.parse_args(argv)
    if urlparse(settings.require("DATABASE_URL")).hostname not in ("localhost", "127.0.0.1"):
        print("seed_dev는 로컬 DB에만 씁니다.")
        return 1
    db.migrate()
    wiring.wire()
    rnd = random.Random(42)
    sq = write_boundary()
    with db.connection() as conn:
        if conn.execute("SELECT EXISTS (SELECT 1 FROM trades) AS e").fetchone()["e"]:
            if not args.reset:
                print("이미 거래가 있습니다. 지우고 다시 만들려면 --reset")
                return 1
            conn.execute("TRUNCATE trades, jobs, changes, api_usage, complexes, address_points, agg_month, "
                         "agg_dirty, regions, boundary_versions RESTART IDENTITY")
        complexes_rows = []
        for code, (x0, y0, x1, y1) in sq.items():
            for k in range(2):
                seq = f"{code[:5]}-{code[5:]}{k}"
                complexes_rows.append((seq, f"{code[-1]}동{k + 1}단지", code[:5], (x0 + x1) / 2, (y0 + y1) / 2,
                                       rnd.choice([1995, 2003, 2012, 2019])))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO complexes (apt_seq, apt_nm, api_sgg_cd, lon, lat, build_year, "
                            "geocode_status, geocode_source) VALUES (%s, %s, %s, %s, %s, %s, 'ok', 'manual')",
                            complexes_rows)
        months = codes.month_range("202301", codes.months_ago(0))
        for i, ym in enumerate(months):
            for sgg in SGG:
                items = []
                for seq, name, s, *_ in complexes_rows:
                    if s != sgg:
                        continue
                    level = BASE_PRICE[sgg[:2]] * (1 + 0.004 * i) * (0.8 + 0.4 * (sum(map(ord, seq)) % 7) / 6)
                    for _ in range(rnd.randint(0 if ym >= codes.months_ago(1) else 1, 5)):
                        area = rnd.choice([49.9, 59.9, 84.9, 114.8])
                        items.append(dict(
                            sggCd=sgg, umdCd="10100", umdNm=f"{seq[-2]}동", aptNm=name, aptSeq=seq,
                            dealYear=ym[:4], dealMonth=str(int(ym[4:])), dealDay=str(rnd.randint(1, 28)),
                            dealAmount=f"{int(level * area / 84.9 * rnd.uniform(0.9, 1.1)):,}",
                            excluUseAr=str(area), floor=str(rnd.randint(1, 25)), buildYear="2005",
                            cdealType="O" if rnd.random() < 0.03 else ""))
                conn.execute("INSERT INTO jobs (lawd_cd, deal_ymd) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (sgg, ym))
                store.save_job(conn, sgg, ym, items, len(items))
        versions.register(conn, VERSION)
        versions.switch(conn, VERSION)          # 단지 지역 판정 + 집계(BEFORE_ACTIVATE)
        n = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
    print(f"경계 {VERSION}, 거래 {n:,}건, 단지 {len(complexes_rows)}개를 만들었습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""테스트용 경계 버전 파일(geo_data/{version}) 만들기. geopandas 없이 쓴다."""
import csv
import gzip
import json

# 읍면동 코드 → (lon0, lat0, lon1, lat1) 사각형
SQUARES = {
    "11110101": (126.95, 37.57, 126.96, 37.58),
    "11110102": (126.96, 37.57, 126.97, 37.58),
    "11140101": (126.97, 37.55, 126.98, 37.56),
}
NAMES = {"11110101": "청운동", "11110102": "신교동", "11140101": "무교동"}
SGG = {"11110": "종로구", "11140": "중구"}


def feature(code, lon0, lat0, lon1, lat1):
    ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
    return {"type": "Feature", "properties": {"region_cd": code, "sgg_cd": code[:5]},
            "geometry": {"type": "Polygon", "coordinates": [ring]}}


def make_version(data_dir, version, squares=None):
    squares = squares or SQUARES
    d = data_dir / version
    d.mkdir(parents=True, exist_ok=True)
    fc = {"type": "FeatureCollection", "features": [feature(c, *b) for c, b in squares.items()]}
    (d / "umd_assign.geojson.gz").write_bytes(gzip.compress(json.dumps(fc).encode()))
    with open(d / "regions.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["region_cd", "level", "name", "full_name", "parent_cd"])
        w.writerow(["11", "sido", "서울특별시", "서울특별시", ""])
        for sgg in sorted({c[:5] for c in squares}):
            w.writerow([sgg, "sgg", SGG.get(sgg, sgg), f"서울특별시 {SGG.get(sgg, sgg)}", "11"])
        for c in squares:
            w.writerow([c, "umd", NAMES.get(c, c), f"서울특별시 {SGG.get(c[:5], c[:5])} {NAMES.get(c, c)}", c[:5]])
    (d / "meta.json").write_text(json.dumps({"version": version, "source": "test"}), encoding="utf-8")
    return d

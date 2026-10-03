"""[로컬 실행] 읍면동 경계 SHP → 화면용 GeoJSON, 지역 판정용 폴리곤, 지역 목록.

사용:
  python -m geo.boundaries --shp <LT_C_ADEMD_INFO.shp 또는 .zip> --version 2026-10 \
      [--src-crs EPSG:5186] [--source "브이월드 LT_C_ADEMD_INFO 2026-09"]

- 입력: 브이월드·국가공간정보포털에서 내려받은 읍면동 경계(속성 EMD_CD 8자리, EMD_KOR_NM).
- 경계 코드의 시군구(앞 5자리)가 lawd_codes.csv에 없으면 geo/code_map.csv로 바꾸고, 그래도 없으면 목록을 출력하고 멈춘다.
- 시군구·시도 폴리곤은 읍면동을 합쳐 만든다(데이터 코드와 지도가 항상 일치).
결과:
  static/geo/{version}/sido.json, sgg.json, umd_{시도코드}.json   화면용(EPSG:4326, 단순화)
  geo_data/{version}/umd_assign.geojson.gz, regions.csv, meta.json  지역 판정·목록용
"""
import argparse
import gzip
import json
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import shapely

import settings
from collector import codes as lawd

STATIC_GEO = settings.BASE_DIR / "static" / "geo"
GEO_DATA = settings.BASE_DIR / "geo_data"
CODE_MAP = settings.BASE_DIR / "geo" / "code_map.csv"
METRIC_CRS = "EPSG:5179"
TOLERANCE_M = {"assign": 5, "umd": 30, "sgg": 80, "sido": 200}   # 단순화 허용 오차(m)


class UnknownCodes(Exception):
    def __init__(self, codes):
        self.codes = sorted(codes)
        super().__init__(f"lawd_codes.csv에 없는 시군구의 읍면동 코드 {len(self.codes)}개")


def load_emd(path, src_crs=None):
    gdf = gpd.read_file(path)
    geom = gdf.geometry.name
    gdf = gdf.rename(columns={c: c.lower() for c in gdf.columns if c != geom})
    if gdf.crs is None:
        if not src_crs:
            raise SystemExit("경계 파일에 좌표계(.prj)가 없습니다. --src-crs로 지정하세요(예: EPSG:5186).")
        gdf = gdf.set_crs(src_crs)
    gdf = gdf.rename(columns={"emd_kor_nm": "name"})
    gdf["emd_cd"] = gdf["emd_cd"].astype(str).str.strip().str.zfill(8)
    gdf = gdf.rename_geometry("geometry") if geom != "geometry" else gdf
    return gdf[["emd_cd", "name", "geometry"]].to_crs(METRIC_CRS)


def read_code_map(path=CODE_MAP):
    if not Path(path).exists():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(df["old_emd_cd"].str.strip(), df["new_emd_cd"].str.strip()))


def normalize_codes(emd, valid_sgg, code_map):
    out = emd.copy()
    out["emd_cd"] = out["emd_cd"].map(lambda c: code_map.get(c, c))
    unknown = set(out.loc[~out["emd_cd"].str[:5].isin(valid_sgg), "emd_cd"])
    if unknown:
        raise UnknownCodes(unknown)
    # 같은 코드의 조각(섬·비지)은 하나로 합친다
    return out.dissolve(by="emd_cd", aggfunc={"name": "first"}).reset_index()


def _simplify(gdf, meters):
    out = gdf.copy()
    out["geometry"] = out.geometry.simplify(meters, preserve_topology=True)
    return out


def _to_web(gdf):
    out = gdf.to_crs("EPSG:4326")
    precise = shapely.set_precision(out.geometry.values, 1e-5)   # 약 1m
    out["geometry"] = gpd.GeoSeries(precise, index=out.index, crs=out.crs)
    return out


def _write_geojson(gdf, path, props, gz=False):
    data = json.loads(gdf[props + ["geometry"]].to_json(drop_id=True))
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if gz:
        path.write_bytes(gzip.compress(text.encode("utf-8")))
    else:
        path.write_text(text, encoding="utf-8")


def build(emd, codes_df, version, source, static_dir=None, data_dir=None):
    """emd: normalize_codes를 거친 읍면동(EPSG:5179, 열 emd_cd·name·geometry)."""
    static_dir = Path(static_dir or STATIC_GEO) / version
    data_dir = Path(data_dir or GEO_DATA) / version
    sgg_names = dict(zip(codes_df["LAWD_CD"], codes_df["시군구"]))
    sido_names = {c[:2]: s for c, s in zip(codes_df["LAWD_CD"], codes_df["시도"])}

    umd = emd.rename(columns={"emd_cd": "region_cd"})
    umd["sgg_cd"] = umd["region_cd"].str[:5]
    umd["sido_cd"] = umd["region_cd"].str[:2]
    umd["full_name"] = [f"{sido_names[s]} {sgg_names[g]} {n}"
                        for s, g, n in zip(umd["sido_cd"], umd["sgg_cd"], umd["name"])]

    sgg = umd[["sgg_cd", "sido_cd", "geometry"]].dissolve(by="sgg_cd", aggfunc="first").reset_index()
    sgg = sgg.rename(columns={"sgg_cd": "region_cd"})
    sgg["name"] = sgg["region_cd"].map(sgg_names)
    sgg["full_name"] = sgg["sido_cd"].map(sido_names) + " " + sgg["name"]

    sido = umd[["sido_cd", "geometry"]].dissolve(by="sido_cd").reset_index()
    sido = sido.rename(columns={"sido_cd": "region_cd"})
    sido["name"] = sido["region_cd"].map(sido_names)
    sido["full_name"] = sido["name"]

    _write_geojson(_to_web(_simplify(sido, TOLERANCE_M["sido"])), static_dir / "sido.json", ["region_cd", "name"])
    _write_geojson(_to_web(_simplify(sgg, TOLERANCE_M["sgg"])), static_dir / "sgg.json",
                   ["region_cd", "name", "sido_cd"])
    for sido_cd, part in umd.groupby("sido_cd"):
        _write_geojson(_to_web(_simplify(part, TOLERANCE_M["umd"])), static_dir / f"umd_{sido_cd}.json",
                       ["region_cd", "name", "sgg_cd"])
    _write_geojson(_to_web(_simplify(umd, TOLERANCE_M["assign"])), data_dir / "umd_assign.geojson.gz",
                   ["region_cd", "sgg_cd"], gz=True)

    regions = pd.concat([
        pd.DataFrame({"region_cd": sido["region_cd"], "level": "sido", "name": sido["name"],
                      "full_name": sido["full_name"], "parent_cd": ""}),
        pd.DataFrame({"region_cd": sgg["region_cd"], "level": "sgg", "name": sgg["name"],
                      "full_name": sgg["full_name"], "parent_cd": sgg["sido_cd"]}),
        pd.DataFrame({"region_cd": umd["region_cd"], "level": "umd", "name": umd["name"],
                      "full_name": umd["full_name"], "parent_cd": umd["sgg_cd"]}),
    ], ignore_index=True).sort_values(["level", "region_cd"])
    regions.to_csv(data_dir / "regions.csv", index=False, encoding="utf-8")
    counts = {"sido": len(sido), "sgg": len(sgg), "umd": len(umd)}
    (data_dir / "meta.json").write_text(json.dumps(
        {"version": version, "source": source, "created": settings.now_str(), "counts": counts},
        ensure_ascii=False, indent=2), encoding="utf-8")
    return regions


def main(argv=None):
    parser = argparse.ArgumentParser(description="읍면동 경계 → 화면·판정용 경계 파일")
    parser.add_argument("--shp", required=True, help="LT_C_ADEMD_INFO .shp(.zip) 경로")
    parser.add_argument("--version", required=True, help="경계 버전 YYYY-MM")
    parser.add_argument("--src-crs", help="경계 파일에 .prj가 없을 때 좌표계")
    parser.add_argument("--source", default="", help="출처 메모(meta.json에 기록)")
    args = parser.parse_args(argv)

    codes_df = lawd.load_codes()
    emd = load_emd(args.shp, args.src_crs)
    try:
        emd = normalize_codes(emd, set(codes_df["LAWD_CD"]), read_code_map())
    except UnknownCodes as e:
        print(f"{e}. geo/code_map.csv에 '옛 코드,새 코드'를 추가하거나 lawd_codes.csv를 확인하세요:")
        for c in e.codes:
            print(f"  {c}")
        return 1
    regions = build(emd, codes_df, args.version, args.source or str(args.shp))
    print(f"경계 {args.version}: " + ", ".join(f"{k} {v}개" for k, v in regions["level"].value_counts().items()))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())

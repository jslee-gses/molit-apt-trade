"""[로컬 실행] 읍면동·시군구 경계 SHP → 화면용 GeoJSON, 지역 판정용 폴리곤, 지역 목록.

사용:
  python -m geo.boundaries --shp <N3A_G0110000.zip> --sgg-shp <N3A_G0100000.zip> --version 2026-10 \
      [--src-crs EPSG:5186] [--source "국토지리정보원 행정경계 2026-09"]

- 입력: 읍면동 경계. 국토지리정보원 연속수치지형도 행정경계(읍면동) N3A_G0110000(속성 BJCD 10자리·NAME, CC BY)를 쓴다.
  EMD_CD 8자리와 EMD_KOR_NM 또는 EMD_NM을 가진 파일도 읽는다.
- 경계 코드의 시군구(앞 5자리)가 lawd_codes.csv에 없으면 geo/code_map.csv로 바꾸고, 그래도 없으면 목록을 출력하고 멈춘다.
- 시군구 화면 경계는 국토지리정보원 행정경계(시군구) N3A_G0100000 원본(CC BY)을 쓴다. 원본에 없거나 읍면동 합과
  면적이 다른 시군구(2026 개편 등)만 읍면동을 합쳐 대신한다. 시도 원본은 변경금지(CC BY-NC-ND)라 쓰지 않고,
  전국(시도 단계) 화면은 시군구 경계를 시도 코드로 칠한다. 지역 목록의 시군구·시도와 판정은 읍면동 기준이다.
- 화면용은 원본을 1m만 단순화하고 작은 섬도 남긴다. 원본이 도로·하천을 따라 붙인 폭 4m 미만의 띠만 깎는다.
결과:
  static/geo/{version}/nation.json, sgg_{시도코드}.json, umd_{시군구코드}.json   화면용(EPSG:4326)
  geo_data/{version}/umd_assign.geojson.gz, regions.csv, meta.json  지역 판정·목록용
"""
import argparse
import gzip
import json
import re
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
# 단순화 허용 오차(m). 화면용은 원본과 눈으로 구분되지 않는 1m만 단순화하고 작은 섬도 지우지 않는다.
TOLERANCE_M = {"assign": 5, "display": 1}
SLIVER_M = 2        # 화면용: 폭 2×SLIVER_M(4m) 미만의 가는 띠(원본이 도로·하천을 따라 붙인 꼬리)를 깎아낸다
AREA_MATCH = 0.005   # 시군구 원본과 (개편 반영) 읍면동 합의 면적 차이가 이보다 크면 원본 대신 읍면동 합을 쓴다


class UnknownCodes(Exception):
    def __init__(self, codes):
        self.codes = sorted(codes)
        super().__init__(f"lawd_codes.csv에 없는 시군구의 읍면동 코드 {len(self.codes)}개")


def _polygonal(geom):
    """유효하게 고친 뒤 면(Polygon/MultiPolygon)만 남긴다. 면이 없으면 None."""
    if geom is None or geom.is_empty:
        return None
    geom = shapely.make_valid(geom)
    if geom.geom_type in ("Polygon", "MultiPolygon"):
        return None if geom.is_empty else geom
    parts = [g for g in getattr(geom, "geoms", []) if g.geom_type in ("Polygon", "MultiPolygon") and not g.is_empty]
    return shapely.union_all(parts) if parts else None


def load_emd(path, src_crs=None, encoding=None):
    gdf = gpd.read_file(path, encoding=encoding) if encoding else gpd.read_file(path)
    geom = gdf.geometry.name
    gdf = gdf.rename(columns={c: c.lower() for c in gdf.columns if c != geom})
    if gdf.crs is None:
        if not src_crs:
            raise SystemExit("경계 파일에 좌표계(.prj)가 없습니다. --src-crs로 지정하세요(예: EPSG:5186).")
        gdf = gdf.set_crs(src_crs)
    # 국토지리정보원: BJCD(법정동 10자리)·NAME / 국토교통부·브이월드: EMD_CD·EMD_KOR_NM 또는 EMD_NM
    if "emd_cd" not in gdf.columns and "bjcd" in gdf.columns:
        gdf["emd_cd"] = gdf["bjcd"].astype(str).str.strip().str[:8]
    if "name" not in gdf.columns:
        gdf = gdf.rename(columns={"emd_kor_nm": "name"} if "emd_kor_nm" in gdf.columns else {"emd_nm": "name"})
    gdf["emd_cd"] = gdf["emd_cd"].astype(str).str.strip().str.zfill(8)
    gdf = gdf.rename_geometry("geometry") if geom != "geometry" else gdf
    gdf = gdf[["emd_cd", "name", "geometry"]].copy()
    fixed = [_polygonal(g) for g in gdf.geometry]
    dropped = sum(g is None for g in fixed)
    if dropped:
        print(f"경고: 면적이 없거나 비어 있는 경계 {dropped}개를 제외했습니다.")
    gdf["geometry"] = gpd.GeoSeries(fixed, index=gdf.index, crs=gdf.crs)
    gdf = gdf[gdf.geometry.notna()]
    return gdf.to_crs(METRIC_CRS)


def read_code_map(path=CODE_MAP):
    if not Path(path).exists():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    return dict(zip(df["old_emd_cd"].str.strip().str.zfill(8), df["new_emd_cd"].str.strip().str.zfill(8)))


def read_code_names(path=CODE_MAP):
    """code_map.csv의 new_name(있으면): 새 코드 → 새 이름(개편으로 이름도 바뀐 동)."""
    if not Path(path).exists():
        return {}
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "new_name" not in df.columns:
        return {}
    df = df[df["new_name"].str.strip() != ""]
    return dict(zip(df["new_emd_cd"].str.strip().str.zfill(8), df["new_name"].str.strip()))


def normalize_codes(emd, valid_sgg, code_map, names=None):
    out = emd.copy()
    out["emd_cd"] = out["emd_cd"].map(lambda c: code_map.get(c, c))
    if names:
        out["name"] = [names.get(c, n) for c, n in zip(out["emd_cd"], out["name"])]
    unknown = set(out.loc[~out["emd_cd"].str[:5].isin(valid_sgg), "emd_cd"])
    if unknown:
        raise UnknownCodes(unknown)
    # 같은 코드의 조각(섬·비지)은 하나로 합친다
    return out.dissolve(by="emd_cd", aggfunc={"name": "first"}).reset_index()


def _simplify(gdf, meters):
    out = gdf.copy()
    out["geometry"] = out.geometry.simplify(meters, preserve_topology=True)
    return out


def _drop_slivers(gdf, w=SLIVER_M):
    """폭 2w 미만의 가는 띠를 깎는다(안쪽으로 w 줄였다 다시 w 늘림, 모서리는 각지게 유지).
    원본 경계에 도로·하천을 따라 폭 1m 안팎·길이 수백 m의 띠가 붙어 있어 선택 테두리가 꼬리처럼 튀어나온다."""
    def clean(g):
        o = g.buffer(-w, join_style="mitre").buffer(w, join_style="mitre")
        return g if o.is_empty else o
    out = gdf.copy()
    out["geometry"] = gpd.GeoSeries([clean(g) for g in out.geometry], index=out.index, crs=out.crs)
    return out


def _display(gdf):
    # 화면용은 좌표 정밀도 맞춤(set_precision)을 하지 않는다: 1m 단순화에서는 맞닿은 고리가 생겨 위상 오류가 날 수 있고,
    # 좌표는 쓸 때 소수 5자리(약 1m)로 반올림한다
    return _to_web(_simplify(_drop_slivers(gdf), TOLERANCE_M["display"]), precise=False)


def load_sgg(path, src_crs=None, encoding=None):
    """국토지리정보원 행정경계(시군구) N3A_G0100000: BJCD 10자리 앞 5자리 = 시군구 코드. → (sgg_cd, geometry) EPSG:5179"""
    gdf = gpd.read_file(path, encoding=encoding) if encoding else gpd.read_file(path)
    if gdf.crs is None:
        gdf = gdf.set_crs(src_crs or METRIC_CRS)
    cols = {c.lower(): c for c in gdf.columns}
    code = gdf[cols["bjcd"]].astype(str).str.strip().str[:5]
    out = gpd.GeoDataFrame({"sgg_cd": code}, geometry=gdf.geometry.make_valid(), crs=gdf.crs).to_crs(METRIC_CRS)
    return out.dissolve(by="sgg_cd").reset_index()


def choose_sgg(dissolved, original):
    """시군구 경계: 원본(국토지리정보원)이 있고 읍면동 합과 면적이 맞으면 원본, 아니면(2026 개편 등) 읍면동 합.
    → (GeoDataFrame region_cd·geometry, 읍면동 합으로 대신한 코드 목록)"""
    orig = original.set_index("sgg_cd").geometry if original is not None else {}
    rows, fallback = [], []
    for code, geom in zip(dissolved["region_cd"], dissolved.geometry):
        o = orig.get(code) if len(orig) else None
        if o is not None and abs(o.area - geom.area) <= AREA_MATCH * geom.area:
            rows.append(o)
        else:
            rows.append(geom)
            fallback.append(code)
    out = dissolved.copy()
    out["geometry"] = gpd.GeoSeries(rows, index=out.index, crs=dissolved.crs)
    return out, fallback


def _to_web(gdf, precise=True):
    out = gdf.to_crs("EPSG:4326")
    if precise:
        snapped = shapely.set_precision(out.geometry.values, 1e-5)   # 약 1m
        out["geometry"] = gpd.GeoSeries(snapped, index=out.index, crs=out.crs)
    empty = out.geometry.isna() | out.geometry.is_empty
    if empty.any():
        print(f"경고: 단순화 후 비어 버린 경계 {int(empty.sum())}개를 제외했습니다.")
        out = out[~empty]
    return out


def _round_coords(c, nd=5):
    if isinstance(c, (list, tuple)):
        if c and isinstance(c[0], (int, float)):
            return [round(v, nd) for v in c]
        return [_round_coords(x, nd) for x in c]
    return c


def _write_geojson(gdf, path, props, gz=False):
    data = json.loads(gdf[props + ["geometry"]].to_json(drop_id=True))
    for f in data["features"]:
        f["geometry"]["coordinates"] = _round_coords(f["geometry"]["coordinates"])
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    path.parent.mkdir(parents=True, exist_ok=True)
    if gz:
        path.write_bytes(gzip.compress(text.encode("utf-8"), mtime=0))
    else:
        path.write_text(text, encoding="utf-8")


def build(emd, codes_df, version, source, static_dir=None, data_dir=None, sgg_original=None):
    """emd: normalize_codes를 거친 읍면동(EPSG:5179, 열 emd_cd·name·geometry).
    sgg_original: load_sgg 결과(없으면 시군구를 읍면동 합으로 만든다)."""
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

    # 화면용: 시도 단계는 시군구 원본 경계를 시도 코드로 칠한다(시도 원본은 변경금지 라이선스라 쓰지 않는다)
    sgg_web, fallback = choose_sgg(sgg, sgg_original)
    if sgg_original is not None and fallback:
        print(f"시군구 원본 대신 읍면동 합을 쓴 곳 {len(fallback)}개(2026 개편 등): {', '.join(fallback)}")
    sgg_web = _display(sgg_web)
    _write_geojson(sgg_web, static_dir / "nation.json", ["region_cd", "name", "sido_cd"])
    for sido_cd, part in sgg_web.groupby("sido_cd"):
        _write_geojson(part, static_dir / f"sgg_{sido_cd}.json", ["region_cd", "name", "sido_cd"])
    for sgg_cd, part in umd.groupby("sgg_cd"):
        _write_geojson(_display(part), static_dir / f"umd_{sgg_cd}.json", ["region_cd", "name", "sgg_cd"])
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
    parser.add_argument("--shp", required=True, help="읍면동 경계 .shp(.zip) 경로(국토지리정보원 N3A_G0110000)")
    parser.add_argument("--sgg-shp", help="시군구 경계 .shp(.zip) 경로(국토지리정보원 N3A_G0100000). 없으면 읍면동 합")
    parser.add_argument("--version", required=True, help="경계 버전 YYYY-MM")
    parser.add_argument("--src-crs", help="경계 파일에 .prj가 없을 때 좌표계")
    parser.add_argument("--encoding", help="속성 인코딩(기본: 자동). 한글이 깨지면 cp949 지정")
    parser.add_argument("--source", default="", help="출처 메모(meta.json에 기록)")
    args = parser.parse_args(argv)

    if not re.fullmatch(r"\d{4}-\d{2}", args.version):
        print(f"--version은 YYYY-MM 형식이어야 합니다(예: 2026-10): {args.version!r}. 서버는 이 형식의 폴더만 경계 버전으로 인식합니다.")
        return 1
    codes_df = lawd.load_codes()
    emd = load_emd(args.shp, args.src_crs, args.encoding)
    try:
        emd = normalize_codes(emd, set(codes_df["LAWD_CD"]), read_code_map(), read_code_names())
    except UnknownCodes as e:
        print(f"{e}. geo/code_map.csv에 '옛 코드,새 코드'를 추가하거나 lawd_codes.csv를 확인하세요:")
        for c in e.codes:
            print(f"  {c}")
        return 1
    sgg_original = load_sgg(args.sgg_shp, args.src_crs, args.encoding) if args.sgg_shp else None
    regions = build(emd, codes_df, args.version, args.source or str(args.shp), sgg_original=sgg_original)
    print(f"경계 {args.version}: " + ", ".join(f"{k} {v}개" for k, v in regions["level"].value_counts().items()))
    for base in (STATIC_GEO, GEO_DATA):
        for f in sorted((base / args.version).glob("*")):
            print(f"  {f.relative_to(base.parent)}  {f.stat().st_size / 1024:.1f} KB")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())

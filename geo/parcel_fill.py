"""국토지리정보원 읍면동 경계에 빠진 읍면동을 연속지적도(국토교통부, CC BY) 필지로 채운다.

국토지리정보원 연속수치지형도 행정경계는 접경지역 일부 도엽(파주 문산·운정, 김포 통진·하성, 강화 강화읍·선원면,
고양 가좌동, 철원·고성 일부 등)이 빠져 있다. 필지고유번호(PNU) 앞 8자리 = 읍면동 코드이므로, 경계에 없는 코드의
필지를 합치면 그 읍면동의 경계가 된다.
- 필지 사이 틈(도로·하천이 필지가 아닌 곳)은 CLOSE_M만큼 넓혔다 줄여 메운다.
- 기존 경계와 겹치는 부분은 뺀다. 남은 면적이 MIN_AREA_M2 미만이거나 필지 면적의 OUTSIDE_SHARE 미만이면
  (경계에 다른 코드로 이미 있는 동, 경계선 부근 필지 몇 개) 채우지 않는다.
"""
import zipfile
from collections import defaultdict
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio
import shapely
from shapely import STRtree

CLOSE_M = 15
MIN_AREA_M2 = 50_000
OUTSIDE_SHARE = 0.5
CHUNK = 40   # where 절 하나에 넣을 코드 수


def _latest_zips(parcel_dir):
    """시도마다 기준일이 가장 늦은 AL_D002_<시도>_*.zip 하나."""
    by_sido = {}
    for z in sorted(Path(parcel_dir).glob("AL_D002_*.zip")):
        by_sido[z.name.split("_")[2]] = z
    return by_sido


def _layers(z):
    """zip 안의 .shp 조각(큰 시도는 여러 개) → GDAL 가상 경로"""
    return [f"/vsizip/{z.as_posix()}/{n}" for n in zipfile.ZipFile(z).namelist() if n.lower().endswith(".shp")]


def _name(addr, ri):
    """'경기도 파주시 문산읍 문산리' → 문산읍(리 단위 코드면 끝에서 둘째), '… 다율동' → 다율동"""
    parts = (addr or "").split()
    if not parts:
        return ""
    return parts[-2] if ri != "00" and len(parts) >= 2 else parts[-1]


def parcel_codes(parcel_dir, code_map=None, log=print):
    """필지에 나오는 읍면동 코드(8자리, 대응표 반영) → 이름"""
    code_map = code_map or {}
    names = {}
    for sido, z in _latest_zips(parcel_dir).items():
        for layer in _layers(z):
            df = pyogrio.read_dataframe(layer, columns=["A1", "A3"], read_geometry=False, encoding="cp949")
            df = df.drop_duplicates(subset=["A3"]).assign(code=lambda d: d["A1"].str[:8], ri=lambda d: d["A1"].str[8:10])
            for code, ri, addr in zip(df["code"], df["ri"], df["A3"]):
                code = code_map.get(code, code)
                if code not in names or ri == "00":
                    names[code] = _name(addr, ri)
        log(f"  필지 코드 읽음: {z.name}")
    return names


def fill(parcel_dir, emd, code_map=None, log=print):
    """emd(EPSG:5179, 열 emd_cd·name·geometry)에 없는 읍면동을 필지로 만든다. → 같은 열의 GeoDataFrame"""
    code_map = code_map or {}
    reverse = defaultdict(set)                     # 대응표의 새 코드 → 필지에 남아 있을 수 있는 옛 코드
    for old, new in code_map.items():
        reverse[new].add(old)
    names = parcel_codes(parcel_dir, code_map, log)
    have = set(emd["emd_cd"])
    missing = sorted(c for c in names if c not in have)
    log(f"경계에 없는 읍면동 코드 {len(missing)}개(필지 기준)")
    if not missing:
        return gpd.GeoDataFrame({"emd_cd": [], "name": []}, geometry=[], crs=emd.crs)

    zips = _latest_zips(parcel_dir)
    tree = STRtree(list(emd.geometry))
    geoms = list(emd.geometry)
    rows = []
    by_sido = defaultdict(list)
    for c in missing:
        by_sido[c[:2]].append(c)
    for sido, codes in sorted(by_sido.items()):
        z = zips.get(sido)
        if z is None:
            continue
        for i in range(0, len(codes), CHUNK):
            chunk = codes[i:i + CHUNK]
            prefixes = sorted({p for c in chunk for p in {c} | reverse.get(c, set())})
            where = " OR ".join(f"A1 LIKE '{p}%'" for p in prefixes)
            parts = [pyogrio.read_dataframe(layer, columns=["A1"], where=where) for layer in _layers(z)]
            gdf = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else None
            if gdf is None:
                continue
            gdf = gpd.GeoDataFrame(gdf, crs=parts[0].crs).to_crs(emd.crs)
            gdf["code"] = [code_map.get(a[:8], a[:8]) for a in gdf["A1"]]
            for code, g in gdf.groupby("code"):
                if code not in chunk:
                    continue
                parcels = shapely.union_all(shapely.make_valid(g.geometry.values))
                closed = parcels.buffer(CLOSE_M, join_style="mitre").buffer(-CLOSE_M, join_style="mitre")
                near = [geoms[j] for j in tree.query(closed)]
                outside = closed.difference(shapely.union_all(near)) if near else closed
                outside = shapely.make_valid(outside)
                if outside.is_empty or outside.area < MIN_AREA_M2 or outside.area < OUTSIDE_SHARE * parcels.area:
                    continue
                polys = [p for p in getattr(outside, "geoms", [outside]) if p.geom_type in ("Polygon", "MultiPolygon")]
                if not polys:
                    continue
                rows.append((code, names[code], shapely.union_all(polys)))
        log(f"  {sido}: 채움 {sum(r[0][:2] == sido for r in rows)}개")
    out = gpd.GeoDataFrame({"emd_cd": [r[0] for r in rows], "name": [r[1] for r in rows]},
                           geometry=[r[2] for r in rows], crs=emd.crs)
    # 새로 만든 읍면동끼리 겹치면 코드 순으로 앞선 쪽에 둔다
    taken = None
    fixed = []
    for g in out.geometry:
        g2 = g if taken is None else shapely.make_valid(g.difference(taken))
        fixed.append(g2)
        taken = g if taken is None else shapely.union_all([taken, g])
    out["geometry"] = gpd.GeoSeries(fixed, index=out.index, crs=out.crs)
    return out[~out.geometry.is_empty]

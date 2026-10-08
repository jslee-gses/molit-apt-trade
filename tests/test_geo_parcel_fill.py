import zipfile

import geopandas as gpd
from shapely.geometry import box

from geo import parcel_fill

X0, Y0 = 197_000, 551_000   # EPSG:5186


def parcel_zip(tmp_path, rows):
    """rows: [(PNU, 법정동 주소, geometry)] → AL_D002_11_*.zip(연속지적도 형식: A1 PNU, A3 주소)"""
    gdf = gpd.GeoDataFrame({"A1": [r[0] for r in rows], "A3": [r[1] for r in rows]},
                           geometry=[r[2] for r in rows], crs="EPSG:5186")
    d = tmp_path / "shp"
    d.mkdir()
    gdf.to_file(d / "AL_D002_11_20260908.shp", encoding="cp949")
    zp = tmp_path / "AL_D002_11_20260908.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in d.iterdir():
            z.write(f, f.name)
    return tmp_path


def test_fill_missing_umd(tmp_path):
    # 기존 경계: 11110101(왼쪽 500m 정사각형). 필지: 같은 동 + 경계에 없는 11110102(오른쪽, 필지 사이 10m 틈) + 11110103(기존 경계 안)
    rows = [("1111010100100010000", "서울특별시 종로구 청운동", box(X0, Y0, X0 + 500, Y0 + 500))]
    for i in range(5):
        x = X0 + 500 + i * 110
        rows.append((f"11110102001{i:03d}0000", "서울특별시 종로구 신교동", box(x, Y0, x + 100, Y0 + 500)))
    rows.append(("1111010300100010000", "서울특별시 종로구 궁정동", box(X0 + 100, Y0 + 100, X0 + 200, Y0 + 200)))
    rows.append(("1125025021100010000", "서울특별시 어딘가 문산읍 문산리", box(X0 - 2000, Y0, X0 - 1500, Y0 + 500)))
    folder = parcel_zip(tmp_path, rows)
    emd = gpd.GeoDataFrame({"emd_cd": ["11110101"], "name": ["청운동"]},
                           geometry=[box(X0, Y0, X0 + 500, Y0 + 500)], crs="EPSG:5186").to_crs("EPSG:5179")
    out = parcel_fill.fill(folder, emd, log=lambda *_: None)
    got = dict(zip(out["emd_cd"], zip(out["name"], out.geometry)))
    assert set(got) == {"11110102", "11250250"}           # 기존 경계 안에 있는 궁정동은 채우지 않음
    name, geom = got["11110102"]
    assert name == "신교동" and abs(geom.area - 540 * 500) < 2_000   # 필지 사이 10m 틈이 메워짐
    assert geom.intersection(emd.geometry.iloc[0]).area < 1
    assert got["11250250"][0] == "문산읍"                  # 리 단위 코드는 읍면 이름으로


def test_name():
    assert parcel_fill._name("경기도 파주시 문산읍 문산리", "21") == "문산읍"
    assert parcel_fill._name("경기도 파주시 다율동", "00") == "다율동"

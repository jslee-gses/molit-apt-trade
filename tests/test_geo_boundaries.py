import gzip
import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from collector import codes
from geo import boundaries

X0, Y0 = 953_000, 1_952_000   # 서울 부근 UTM-K


def emd_frame(rows):
    """rows: [(emd_cd, name, dx_km, dy_km)] → 1km 정사각형 읍면동"""
    return gpd.GeoDataFrame(
        {"emd_cd": [r[0] for r in rows], "name": [r[1] for r in rows]},
        geometry=[box(X0 + r[2] * 1000, Y0 + r[3] * 1000, X0 + (r[2] + 1) * 1000, Y0 + (r[3] + 1) * 1000)
                  for r in rows],
        crs="EPSG:5179",
    )


ROWS = [("11110101", "청운동", 0, 0), ("11110102", "신교동", 1, 0), ("11110102", "신교동", 3, 3),
        ("11140101", "무교동", 0, -2)]


def test_normalize_dissolves_pieces_and_maps_codes():
    emd = emd_frame(ROWS + [("11999101", "옛무교동", 1, -2)])
    out = boundaries.normalize_codes(emd, {"11110", "11140"}, {"11999101": "11140101"})
    assert sorted(out["emd_cd"]) == ["11110101", "11110102", "11140101"]
    assert out.set_index("emd_cd").loc["11110102"].geometry.geom_type == "MultiPolygon"


def test_code_map_new_name_overrides(tmp_path):
    path = tmp_path / "map.csv"
    path.write_text("old_emd_cd,new_emd_cd,old_name,new_name\n11999101,11140101,옛무교동,무교동\n", encoding="utf-8")
    code_map, names = boundaries.read_code_map(path), boundaries.read_code_names(path)
    emd = emd_frame(ROWS[:1] + [("11999101", "옛무교동", 1, -2)])
    out = boundaries.normalize_codes(emd, {"11110", "11140"}, code_map, names)
    assert out.set_index("emd_cd").loc["11140101", "name"] == "무교동"


def test_unknown_codes_stop_with_list():
    emd = emd_frame(ROWS + [("99999101", "어딘가", 5, 5)])
    with pytest.raises(boundaries.UnknownCodes) as e:
        boundaries.normalize_codes(emd, {"11110", "11140"}, {})
    assert e.value.codes == ["99999101"]


def test_build_writes_files(tmp_path):
    emd = boundaries.normalize_codes(emd_frame(ROWS), {"11110", "11140"}, {})
    regions = boundaries.build(emd, codes.load_codes(), "2026-10", "테스트", tmp_path / "static", tmp_path / "data")

    web = tmp_path / "static" / "2026-10"
    sgg = json.loads((web / "sgg.json").read_text(encoding="utf-8"))
    assert sorted(f["properties"]["region_cd"] for f in sgg["features"]) == ["11110", "11140"]
    assert {f["properties"]["name"] for f in sgg["features"]} == {"종로구", "중구"}
    sido = json.loads((web / "sido.json").read_text(encoding="utf-8"))
    assert [f["properties"]["region_cd"] for f in sido["features"]] == ["11"]
    umd = json.loads((web / "umd_11.json").read_text(encoding="utf-8"))
    assert len(umd["features"]) == 3
    lon, lat = umd["features"][0]["geometry"]["coordinates"][0][0][:2] \
        if umd["features"][0]["geometry"]["type"] == "Polygon" \
        else umd["features"][0]["geometry"]["coordinates"][0][0][0][:2]
    assert 126 < lon < 128 and 37 < lat < 38

    data = tmp_path / "data" / "2026-10"
    assign = json.loads(gzip.decompress((data / "umd_assign.geojson.gz").read_bytes()))
    assert {f["properties"]["sgg_cd"] for f in assign["features"]} == {"11110", "11140"}
    csv = pd.read_csv(data / "regions.csv", dtype=str, keep_default_na=False)
    assert sorted(csv["level"].value_counts().items()) == [("sgg", 2), ("sido", 1), ("umd", 3)]
    row = csv.set_index("region_cd").loc["11110101"]
    assert row["full_name"] == "서울특별시 종로구 청운동" and row["parent_cd"] == "11110"
    meta = json.loads((data / "meta.json").read_text(encoding="utf-8"))
    assert meta["version"] == "2026-10" and meta["counts"] == {"sido": 1, "sgg": 2, "umd": 3}
    assert len(regions) == 6


def test_display_drops_tiny_islands_but_assign_keeps_them(tmp_path):
    speck = box(X0 + 5000, Y0 + 5000, X0 + 5010, Y0 + 5010)    # 100㎡ 섬
    emd = boundaries.normalize_codes(emd_frame(ROWS), {"11110", "11140"}, {})
    i = emd.index[emd["emd_cd"] == "11110101"][0]
    emd.loc[i, "geometry"] = emd.loc[i, "geometry"].union(speck)
    boundaries.build(emd, codes.load_codes(), "2026-10", "t", tmp_path / "s", tmp_path / "d")

    def parts(features, code):
        g = next(f["geometry"] for f in features if f["properties"]["region_cd"] == code)
        return 1 if g["type"] == "Polygon" else len(g["coordinates"])
    umd = json.loads((tmp_path / "s" / "2026-10" / "umd_11.json").read_text(encoding="utf-8"))["features"]
    assign = json.loads(gzip.decompress((tmp_path / "d" / "2026-10" / "umd_assign.geojson.gz").read_bytes()))
    assert parts(umd, "11110101") == 1
    assert parts(assign["features"], "11110101") == 2


def test_load_emd_reads_file_and_lowercases(tmp_path):
    src = emd_frame(ROWS).rename(columns={"emd_cd": "EMD_CD", "name": "EMD_KOR_NM"})
    path = tmp_path / "emd.gpkg"
    src.to_file(path)
    out = boundaries.load_emd(path)
    assert list(out.columns) == ["emd_cd", "name", "geometry"]
    assert out.crs.to_epsg() == 5179


def test_load_emd_reads_ngii_bjcd(tmp_path):
    """국토지리정보원 연속수치지형도 행정경계: BJCD(법정동 10자리)·NAME."""
    src = emd_frame(ROWS).rename(columns={"name": "NAME"})
    src["BJCD"] = src.pop("emd_cd") + "00"
    src["UFID"] = "u"
    path = tmp_path / "ngii.gpkg"
    src.to_file(path)
    out = boundaries.load_emd(path)
    assert list(out.columns) == ["emd_cd", "name", "geometry"]
    assert sorted(set(out["emd_cd"])) == ["11110101", "11110102", "11140101"]
    assert out.set_index("emd_cd").loc["11110101", "name"] == "청운동"


def test_load_emd_reads_emd_nm(tmp_path):
    """국토교통부 행정구역_읍면동: EMD_CD·EMD_NM."""
    src = emd_frame(ROWS).rename(columns={"emd_cd": "EMD_CD", "name": "EMD_NM"})
    path = tmp_path / "lsmd.gpkg"
    src.to_file(path)
    assert boundaries.load_emd(path)["name"].tolist()[0] == "청운동"


def test_main_reports_unknown_codes(tmp_path, capsys, monkeypatch):
    path = tmp_path / "emd.gpkg"
    emd_frame([("99999101", "어딘가", 0, 0)]).rename(columns={"name": "emd_kor_nm"}).to_file(path)
    monkeypatch.setattr(boundaries, "STATIC_GEO", tmp_path / "static")
    monkeypatch.setattr(boundaries, "GEO_DATA", tmp_path / "data")
    assert boundaries.main(["--shp", str(path), "--version", "2026-10"]) == 1
    assert "99999101" in capsys.readouterr().out


def test_load_emd_repairs_invalid_and_drops_empty(tmp_path):
    from shapely.geometry import Polygon
    bow = Polygon([(X0, Y0), (X0 + 1000, Y0 + 1000), (X0 + 1000, Y0), (X0, Y0 + 1000)])
    src = gpd.GeoDataFrame(
        {"EMD_CD": ["11110101", "11110102", "11110103"], "EMD_KOR_NM": ["청운동", "신교동", "빈동"]},
        geometry=[bow, box(X0 + 2000, Y0, X0 + 3000, Y0 + 1000), None], crs="EPSG:5179")
    path = tmp_path / "emd.gpkg"
    src.to_file(path)
    out = boundaries.load_emd(path)
    assert sorted(out["emd_cd"]) == ["11110101", "11110102"]
    assert out.geometry.is_valid.all()
    emd = boundaries.normalize_codes(out, {"11110"}, {})
    boundaries.build(emd, codes.load_codes(), "2026-10", "t", tmp_path / "s", tmp_path / "d")


def test_coordinates_rounded_to_five_decimals(tmp_path):
    emd = boundaries.normalize_codes(emd_frame(ROWS), {"11110", "11140"}, {})
    boundaries.build(emd, codes.load_codes(), "2026-10", "t", tmp_path / "s", tmp_path / "d")
    text = (tmp_path / "s" / "2026-10" / "sgg.json").read_text(encoding="utf-8")
    import re
    nums = re.findall(r"\d+\.(\d+)", text)
    assert nums and max(len(n) for n in nums) <= 5


def test_gzip_is_reproducible_and_code_map_zero_pads(tmp_path):
    emd = boundaries.normalize_codes(emd_frame(ROWS), {"11110", "11140"}, {})
    args = (emd, codes.load_codes(), "2026-10", "t")
    boundaries.build(*args, tmp_path / "s", tmp_path / "d1")
    boundaries.build(*args, tmp_path / "s", tmp_path / "d2")
    assert ((tmp_path / "d1" / "2026-10" / "umd_assign.geojson.gz").read_bytes()
            == (tmp_path / "d2" / "2026-10" / "umd_assign.geojson.gz").read_bytes())
    m = tmp_path / "m.csv"
    m.write_text("old_emd_cd,new_emd_cd\n1111010,1114010\n", encoding="utf-8")
    assert boundaries.read_code_map(m) == {"01111010": "01114010"}


def test_main_rejects_bad_version_before_reading(capsys):
    assert boundaries.main(["--shp", "does-not-exist.shp", "--version", "2026-9"]) == 1
    assert "YYYY-MM" in capsys.readouterr().out

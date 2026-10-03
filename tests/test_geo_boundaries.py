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


def test_load_emd_reads_file_and_lowercases(tmp_path):
    src = emd_frame(ROWS).rename(columns={"emd_cd": "EMD_CD", "name": "EMD_KOR_NM"})
    path = tmp_path / "emd.gpkg"
    src.to_file(path)
    out = boundaries.load_emd(path)
    assert list(out.columns) == ["emd_cd", "name", "geometry"]
    assert out.crs.to_epsg() == 5179


def test_main_reports_unknown_codes(tmp_path, capsys, monkeypatch):
    path = tmp_path / "emd.gpkg"
    emd_frame([("99999101", "어딘가", 0, 0)]).rename(columns={"name": "emd_kor_nm"}).to_file(path)
    monkeypatch.setattr(boundaries, "STATIC_GEO", tmp_path / "static")
    monkeypatch.setattr(boundaries, "GEO_DATA", tmp_path / "data")
    assert boundaries.main(["--shp", str(path), "--version", "2026-10"]) == 1
    assert "99999101" in capsys.readouterr().out

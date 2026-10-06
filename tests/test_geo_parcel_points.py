import zipfile
from datetime import datetime

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import Point, Polygon, box

import settings
from collector import store
from geo import hooks, parcel_points
from tests.helpers import add_job, item

X0, Y0 = 197_000, 551_000   # EPSG:5186, 서울 종로 부근


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 7, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])


def make_zip(tmp_path, name, rows, crs="EPSG:5186"):
    """rows: [(pnu, geometry)] → 연속지적도 형식(A0 일련번호, A1 PNU, A2 법정동코드) SHP를 담은 zip"""
    gdf = gpd.GeoDataFrame({"A0": [str(i) for i in range(len(rows))], "A1": [p for p, _ in rows],
                            "A2": [p[:10] for p, _ in rows]}, geometry=[g for _, g in rows], crs=crs)
    d = tmp_path / name
    d.mkdir()
    gdf.to_file(d / f"{name}.shp", encoding="cp949")
    zp = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in d.iterdir():
            z.write(f, f.name)
    return zp


L_SHAPE = Polygon([(X0, Y0), (X0 + 100, Y0), (X0 + 100, Y0 + 10), (X0 + 10, Y0 + 10),
                   (X0 + 10, Y0 + 100), (X0, Y0 + 100)])   # 무게중심이 밖에 있는 ㄱ자 필지
P1 = "1111010100100010000"
P2 = "1111010100100020000"
P3 = "1111010100100030000"


def seed(conn):
    add_job(conn, "11110", "202601")
    store.save_job(conn, "11110", "202601", [
        item(aptSeq="A", landCd="1", bonbun="0001", bubun="0000", dealDay="1"),
        item(aptSeq="A", landCd="1", bonbun="0001", bubun="0000", dealDay="2"),
        item(aptSeq="A", landCd="1", bonbun="0009", bubun="0000", dealDay="3"),   # 소수 지번
        item(aptSeq="B", landCd="1", bonbun="0002", bubun="0000"),
        item(aptSeq="C", landCd="1", bonbun="0007", bubun="0000"),               # 필지 없음
        item(aptSeq="M", landCd="1", bonbun="0003", bubun="0000"),               # 수동 좌표 단지
        item(aptSeq="Z", landCd="1", bonbun="", bubun=""),                       # PNU 못 만듦
    ], 7)
    conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, geocode_status) VALUES
        ('A', '11110', 'pending'), ('B', '11110', 'failed'), ('C', '11110', 'pending'),
        ('Z', '11110', 'pending')""")
    conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status, geocode_source)
        VALUES ('M', '11110', 127.0, 37.5, 'manual', 'manual')""")


def test_targets_pick_most_common_pnu(pg):
    with pg.connection() as conn:
        seed(conn)
        got, n = parcel_points.targets(conn)
    assert got == {"A": P1, "B": P2, "C": "1111010100100070000"}
    assert n == 4                                              # A·B·C·Z (M은 수동)


def test_read_points(tmp_path):
    z1 = make_zip(tmp_path, "AL_D002_11", [(P1, L_SHAPE), (P2, box(X0 + 200, Y0, X0 + 300, Y0 + 100)),
                                           (P2, box(X0 + 300, Y0, X0 + 400, Y0 + 100)),   # 같은 PNU 두 조각
                                           ("1111010100100990000", box(X0, Y0 + 500, X0 + 10, Y0 + 510))])
    pts, stats = parcel_points.read_points([z1], {P1, P2, P3})
    assert set(pts) == {P1, P2}
    assert stats == {"files": 1, "parcels": 4, "matched": 2, "dropped": 0}
    back = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
    x, y = back.transform(*pts[P1])
    assert L_SHAPE.buffer(0.5).contains(Point(x, y))        # 대표점은 필지 안
    x, y = back.transform(*pts[P2])
    assert X0 + 200 <= x <= X0 + 400                          # 두 조각을 합친 필지 안
    lon, lat = pts[P1]
    assert 126.9 < lon < 127.1 and 37.5 < lat < 37.6
    assert lon == round(lon, 6)


def test_pnu_column_requires_19_digit_field(tmp_path):
    gdf = gpd.GeoDataFrame({"A0": ["1"], "NAME": ["가"]}, geometry=[box(X0, Y0, X0 + 1, Y0 + 1)], crs="EPSG:5186")
    d = tmp_path / "bad"
    d.mkdir()
    gdf.to_file(d / "bad.shp")
    with pytest.raises(ValueError, match="PNU"):
        parcel_points.pnu_column(str(d / "bad.shp"))


def test_save_updates_skips_changed_and_marks_failed(pg):
    seen = []
    hooks.ON_REGION_CHANGE.append(lambda conn, seqs: seen.append(sorted(seqs)))
    with pg.connection() as conn:
        seed(conn)
        conn.execute("UPDATE complexes SET boundary_version = '2026-10', region_umd_cd = '11110101' WHERE apt_seq = 'A'")
        conn.execute("UPDATE complexes SET lon = 126.9, lat = 37.5, geocode_status = 'manual' WHERE apt_seq = 'B'")  # 계산 뒤 수동 입력
        result = parcel_points.save(conn, {"A": (126.966, 37.5585), "B": (126.967, 37.559)}, ["A", "B", "C"])
        rows = {r["apt_seq"]: r for r in conn.execute("SELECT * FROM complexes")}
    assert result == {"located": 1, "failed": 1, "skipped": 1}
    a = rows["A"]
    assert (a["lon"], a["lat"], a["geocode_status"], a["geocode_source"]) == (126.966, 37.5585, "ok", "parcel")
    assert a["boundary_version"] is None and a["region_umd_cd"] is None      # 다음 지리 처리에서 경계로 재판정
    assert rows["B"]["geocode_status"] == "manual" and rows["B"]["lon"] == 126.9
    assert rows["C"]["geocode_status"] == "failed"
    assert rows["Z"]["geocode_status"] == "pending"                          # 대상 목록 밖은 그대로
    assert seen == [["A"], ["A"]]


def test_main_dry_run_and_yes(pg, tmp_path, capsys, monkeypatch):
    import wiring
    monkeypatch.setattr(wiring, "wire", lambda: None)   # 다른 테스트로 훅 설정이 새지 않게
    with pg.connection() as conn:
        seed(conn)
    make_zip(tmp_path, "AL_D002_11", [(P1, L_SHAPE)])
    assert parcel_points.main(["--dir", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "대상 DB: localhost" in out or "대상 DB: 127.0.0.1" in out
    assert "찾음 1" in out and "저장하지 않습니다" in out
    with pg.connection() as conn:
        assert conn.execute("SELECT geocode_status FROM complexes WHERE apt_seq = 'A'").fetchone()["geocode_status"] == "pending"
    assert parcel_points.main(["--dir", str(tmp_path)]) == 0
    with pg.connection() as conn:
        assert conn.execute("SELECT geocode_status FROM complexes WHERE apt_seq = 'A'").fetchone()["geocode_status"] == "ok"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@db.example.com:5432/x")
    assert parcel_points.main(["--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "--yes" in out and "secret" not in out


def test_main_stops_without_zip(pg, tmp_path, capsys, monkeypatch):
    import wiring
    monkeypatch.setattr(wiring, "wire", lambda: None)
    assert parcel_points.main(["--dir", str(tmp_path)]) == 1
    assert "zip" in capsys.readouterr().out

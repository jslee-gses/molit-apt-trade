from datetime import datetime

import pytest
from pyproj import Transformer

import settings
from collector import store
from geo import address_points, complexes, locate
from tests.helpers import add_job, item


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [complexes.register])


TO_UTMK = Transformer.from_crs("EPSG:4326", "EPSG:5179", always_xy=True)


def line(road_cd="111104100135", under="0", bon="1", bu="0", seq="1", use="공동주택",
         lon=126.97, lat=37.58, name="테스트아파트"):
    x, y = TO_UTMK.transform(lon, lat)
    cols = dict(sgg_cd=road_cd[:5], entrance_seq=seq, bjd_cd="1111010100", sido_nm="서울특별시",
                sgg_nm="종로구", emd_nm="청운동", road_cd=road_cd, road_nm="자하문로", underground=under,
                bonbun=bon, bubun=bu, bld_nm=name, zip_cd="03047", bld_use=use, bld_group="1",
                adm_dong="청운효자동", x=f"{x:.6f}", y=f"{y:.6f}")
    return "|".join(cols[c] for c in address_points.COLUMNS)


def write(tmp_path, lines, name="entrc_seoul.txt"):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="cp949")
    return p


def test_parse_line_short_line_is_none():
    assert address_points.parse_line("a|b|c") is None


def test_read_points_filters_and_keeps_first_entrance(tmp_path):
    p = write(tmp_path, [
        line(seq="2", lon=126.99),
        line(seq="1", lon=126.97),                                        # 같은 건물, 더 작은 출입구 번호
        line(road_cd="111104100136", use="단독주택"),                     # 공동주택도 아니고 거래에도 없음
        line(road_cd="111104100137", use="제2종근린생활시설"),             # 거래에 나온 키
        line(road_cd="1111041001", bon="1"),                              # 형식 오류
    ])
    pts = address_points.read_points([p], wanted={"111104100137|0|1|0"})
    assert set(pts) == {"111104100135|0|1|0", "111104100137|0|1|0"}
    seq, x, y, name = pts["111104100135|0|1|0"]
    assert seq == 1 and name == "테스트아파트"
    (key, lon, lat, _), = [r for r in address_points.to_lonlat(pts) if r[0] == "111104100135|0|1|0"]
    assert lon == pytest.approx(126.97, abs=1e-6) and lat == pytest.approx(37.58, abs=1e-6)


def seed_trades(conn):
    add_job(conn, "11110", "202601")
    store.save_job(conn, "11110", "202601", [
        item(aptSeq="A", roadNmCd="4100135", roadNmBonbun="00001"),
        item(aptSeq="A", roadNmCd="4100135", roadNmBonbun="00001"),
        item(aptSeq="A", roadNmCd="4100135", roadNmBonbun="00003"),      # 같은 단지의 소수 키
        item(aptSeq="B", roadNmCd="4100999", roadNmBonbun="00009"),      # 좌표 없는 키
        item(aptSeq="C", roadNmCd="", roadNmBonbun=""),                   # 도로명 정보 없음
    ], 5)


def test_wanted_keys_and_load(pg):
    with pg.connection() as conn:
        seed_trades(conn)
        wanted = address_points.wanted_keys(conn)
        assert wanted == {"111104100135|0|1|0", "111104100135|0|3|0", "111104100999|0|9|0"}
        n = address_points.load(conn, [("111104100135|0|1|0", 126.97, 37.58, "가")], "202609")
        row = conn.execute("SELECT * FROM address_points").fetchone()
    assert n == 1 and row["source_month"] == "202609"


def test_locate_prefers_most_common_key(pg):
    with pg.connection() as conn:
        seed_trades(conn)
        address_points.load(conn, [("111104100135|0|1|0", 126.97, 37.58, "가"),
                                   ("111104100135|0|3|0", 126.99, 37.59, "가")], "202609")
        result = locate.locate_pending(conn)
        rows = {r["apt_seq"]: r for r in conn.execute("SELECT * FROM complexes")}
    assert result == {"ok": 1, "failed": 2}
    a = rows["A"]
    assert (a["lon"], a["lat"]) == (126.97, 37.58)
    assert a["geocode_status"] == "ok" and a["geocode_source"] == "road"
    assert a["geocoded_at"] == datetime(2026, 10, 3, 7, 0)
    assert rows["B"]["geocode_status"] == "failed" and rows["C"]["geocode_status"] == "failed"


def test_locate_never_touches_manual(pg):
    with pg.connection() as conn:
        seed_trades(conn)
        conn.execute("UPDATE complexes SET geocode_status = 'manual', lon = 1, lat = 2 WHERE apt_seq = 'A'")
        address_points.load(conn, [("111104100135|0|1|0", 126.97, 37.58, "가")], "202609")
        locate.locate_pending(conn)
        a = conn.execute("SELECT * FROM complexes WHERE apt_seq = 'A'").fetchone()
    assert (a["geocode_status"], a["lon"], a["lat"]) == ("manual", 1, 2)


def test_load_retries_failed(pg):
    with pg.connection() as conn:
        seed_trades(conn)
        locate.locate_pending(conn)                          # 모두 failed
        address_points.load(conn, [("111104100999|0|9|0", 127.0, 37.5, "나")], "202610")
        result = locate.locate_pending(conn)
        b = conn.execute("SELECT * FROM complexes WHERE apt_seq = 'B'").fetchone()
    assert result["ok"] == 1 and b["geocode_status"] == "ok"

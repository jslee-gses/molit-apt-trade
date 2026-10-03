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
    pts, stats = address_points.read_points([p], wanted={"111104100137|0|1|0"})
    assert set(pts) == {"111104100135|0|1|0", "111104100137|0|1|0"}
    seq, x, y, name = pts["111104100135|0|1|0"]
    assert seq == 1 and name == "테스트아파트"
    assert stats["bad_key"] == 1 and stats["short"] == 0 and stats["apt_rows"] == 3
    rows, dropped = address_points.to_lonlat(pts)
    assert dropped == 0
    (key, lon, lat, _), = [r for r in rows if r[0] == "111104100135|0|1|0"]
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


def test_read_points_counts_skipped_lines(tmp_path):
    cols = line().split("|")
    cols[address_points.COLUMNS.index("x")] = "abc"
    p = write(tmp_path, ["a|b|c", "|".join(cols), line(road_cd="1111041001")])
    pts, stats = address_points.read_points([p], wanted=set())
    assert pts == {}
    assert (stats["short"], stats["bad_number"], stats["bad_key"], stats["apt_rows"]) == (1, 1, 1, 2)


def test_to_lonlat_drops_out_of_bbox_and_nonfinite():
    pts = {"k1": (1, *TO_UTMK.transform(126.97, 37.58), "가"),
           "k2": (1, 0.0, 0.0, "먼곳"),
           "k3": (1, float("inf"), float("inf"), "무한")}
    rows, dropped = address_points.to_lonlat(pts)
    assert [r[0] for r in rows] == ["k1"] and dropped == 2


def test_locate_does_not_fail_complex_registered_after_snapshot(pg):
    with pg.connection() as conn:
        seed_trades(conn)
        with conn.transaction():
            locate.snapshot_pending(conn)
            conn.execute("INSERT INTO complexes (apt_seq) VALUES ('NEW')")   # 수집기가 뒤늦게 등록
            result = locate.finish_snapshot(conn)
        rows = {r["apt_seq"]: r["geocode_status"] for r in conn.execute("SELECT * FROM complexes")}
    assert result == {"ok": 0, "failed": 3}
    assert rows["NEW"] == "pending"


def test_load_refuses_empty_rows(pg):
    with pg.connection() as conn:
        address_points.load(conn, [("111104100135|0|1|0", 126.97, 37.58, "가")], "202609")
        with pytest.raises(ValueError):
            address_points.load(conn, [], "202610")
        assert conn.execute("SELECT COUNT(*) AS n FROM address_points").fetchone()["n"] == 1


def run_main(tmp_path, lines, extra=()):
    write(tmp_path, lines)
    return address_points.main(["--dir", str(tmp_path), "--month", "202609", *extra])


def count_points(pg):
    with pg.connection() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM address_points").fetchone()["n"]


def test_main_refuses_without_apt_rows(pg, tmp_path, capsys):
    with pg.connection() as conn:
        seed_trades(conn)
        address_points.load(conn, [("111104100135|0|1|0", 126.97, 37.58, "가")], "202609")
    assert run_main(tmp_path, [line(use="단독주택")]) == 1
    assert "공동주택 줄이 하나도 없습니다" in capsys.readouterr().out
    assert count_points(pg) == 1


def test_main_refuses_when_no_rows(pg, tmp_path):
    with pg.connection() as conn:
        seed_trades(conn)
        address_points.load(conn, [("111104100135|0|1|0", 126.97, 37.58, "가")], "202609")
    assert run_main(tmp_path, [line(road_cd="1111041001")]) == 1   # 공동주택 줄은 있으나 키 오류
    assert count_points(pg) == 1


def test_main_loads_local(pg, tmp_path, capsys):
    with pg.connection() as conn:
        seed_trades(conn)
    assert run_main(tmp_path, [line()]) == 0
    out = capsys.readouterr().out
    assert "대상 DB: " in out and "적재 1건" in out
    assert count_points(pg) == 1


def test_main_requires_yes_for_remote_host(tmp_path, monkeypatch, capsys):
    import os

    import db
    monkeypatch.setenv("DATABASE_URL", os.environ["DATABASE_URL"])   # main()이 바꾼 값을 테스트 뒤 되돌리기 위해
    monkeypatch.setattr(db, "connection", lambda: pytest.fail("연결을 시도하면 안 됨"))
    url = "postgresql://user:secret@db.example.invalid:5432/prod"
    assert run_main(tmp_path, [line()], ["--database-url", url]) == 1
    out = capsys.readouterr().out
    assert "db.example.invalid:5432/prod" in out and "secret" not in out and "--yes" in out

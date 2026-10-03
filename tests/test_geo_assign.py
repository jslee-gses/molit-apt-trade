import pytest

from geo import assign, hooks
from tests.geo_fixtures import SQUARES, feature, make_version


@pytest.fixture
def boundary():
    return assign.Boundary([feature(c, *b) for c, b in SQUARES.items()])


def test_locate_within(boundary):
    assert boundary.locate(126.955, 37.575) == ("11110101", "within")


def test_locate_on_shared_edge_is_deterministic(boundary):
    assert boundary.locate(126.96, 37.575) == ("11110101", "within")   # 두 폴리곤 경계 → 작은 코드


def test_locate_nearest_and_none(boundary):
    # 동쪽 경계(126.98)에서 약 88m 바깥 → nearest, 약 880m 바깥 → none
    assert boundary.locate(126.981, 37.555) == ("11140101", "nearest")
    assert boundary.locate(126.99, 37.555) == (None, "none")


def test_locate_nearest_north(boundary):
    assert boundary.locate(126.975, 37.5608) == ("11140101", "nearest")


def test_assign_row_nan_is_none(boundary):
    r = assign.assign_row(boundary, dict(apt_seq="X", lon=float("nan"), lat=37.5, api_sgg_cd="11110"), "v")
    assert (r["umd"], r["sgg"], r["match"], r["mismatch"]) == (None, None, "none", False)


def test_boundary_load(tmp_path):
    make_version(tmp_path, "2026-10")
    b = assign.Boundary.load("2026-10", tmp_path)
    assert b.locate(126.975, 37.555) == ("11140101", "within")


def seed_complexes(conn):
    conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status) VALUES
        ('A', '11110', 126.955, 37.575, 'ok'),
        ('B', '11110', 126.975, 37.555, 'manual'),   -- API는 종로구, 좌표는 중구 → 불일치
        ('C', '11140', 127.5, 37.0, 'ok'),           -- 경계 밖
        ('D', '11110', NULL, NULL, 'pending')""")


def test_assign_pending(pg, tmp_path, monkeypatch):
    make_version(tmp_path, "2026-10")
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    calls = []
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [lambda conn, seqs: calls.append(sorted(seqs))])
    with pg.connection() as conn:
        seed_complexes(conn)
        assert assign.assign_pending(conn, "2026-10") == 3
        rows = {r["apt_seq"]: r for r in conn.execute("SELECT * FROM complexes")}
        assert assign.assign_pending(conn, "2026-10") == 0           # 이미 이 버전으로 판정됨
    assert calls == [["A", "B", "C"], ["A", "B", "C"]]             # 바뀌기 전·후
    a, b, c, d = rows["A"], rows["B"], rows["C"], rows["D"]
    assert (a["region_umd_cd"], a["region_sgg_cd"], a["region_match"], a["sgg_mismatch"]) == \
        ("11110101", "11110", "within", False)
    assert (b["region_sgg_cd"], b["sgg_mismatch"]) == ("11140", True)
    assert (c["region_umd_cd"], c["region_sgg_cd"], c["region_match"]) == (None, None, "none")
    assert c["boundary_version"] == "2026-10"
    assert d["boundary_version"] is None


def test_null_coords_with_ok_status_skipped(pg, tmp_path, monkeypatch):
    make_version(tmp_path, "2026-10")
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    with pg.connection() as conn:
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status) VALUES
            ('A', '11110', 126.955, 37.575, 'ok'), ('N', '11110', NULL, NULL, 'ok')""")
        assert assign.assign_pending(conn, "2026-10") == 1
        n = conn.execute("SELECT boundary_version FROM complexes WHERE apt_seq = 'N'").fetchone()
        assert n["boundary_version"] is None


def test_stale_result_not_applied(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    b = assign.Boundary.load("2026-10", tmp_path)
    with pg.connection() as conn:
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status) VALUES
            ('A', '11110', 126.955, 37.575, 'ok'), ('B', '11110', 126.965, 37.575, 'ok')""")
        results = assign.compute_pending(conn, "2026-10", b)
        conn.execute("UPDATE complexes SET lon = 126.975, lat = 37.555 WHERE apt_seq = 'A'")
        assign.apply_results(conn, results)
        rows = {r["apt_seq"]: r for r in conn.execute("SELECT * FROM complexes")}
        assert rows["A"]["boundary_version"] is None and rows["A"]["region_umd_cd"] is None
        assert rows["B"]["boundary_version"] == "2026-10"
        assert assign.assign_pending(conn, "2026-10", b) == 1   # 다음 실행에서 다시 판정

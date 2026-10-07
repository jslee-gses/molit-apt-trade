import pytest

from geo import assign, code_map, hooks
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


@pytest.fixture(autouse=True)
def code_map_file(tmp_path, monkeypatch):
    path = tmp_path / "code_map.csv"
    path.write_text("old_emd_cd,new_emd_cd,old_name,new_name\n11999101,11140101,옛무교동,무교동\n", encoding="utf-8")
    monkeypatch.setattr(code_map, "PATH", path)
    return path


def seed_complexes(conn):
    conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, api_umd_cd, lon, lat, geocode_status) VALUES
        ('A', '11110', '10100', 126.955, 37.575, 'ok'),
        ('B', '11110', '10100', 126.975, 37.555, 'manual'),   -- API는 종로구, 좌표는 중구 → 불일치
        ('C', '11140', '10100', 127.5, 37.0, 'ok'),           -- 경계 밖 → 코드 판정으로 대체
        ('D', '11110', '10200', NULL, NULL, 'pending'),      -- 좌표 없음 → 코드
        ('E', '11999', '10100', NULL, NULL, 'failed'),       -- 대응표로 새 코드
        ('F', '11110', '99900', NULL, NULL, 'pending'),      -- 경계에 없는 코드 → none
        ('G', '11110', NULL, NULL, NULL, 'pending')""")      # 코드 없음 → none


def test_assign_pending(pg, tmp_path, monkeypatch):
    make_version(tmp_path, "2026-10")
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    calls = []
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [lambda conn, seqs: calls.append(sorted(seqs))])
    with pg.connection() as conn:
        versions_register(conn)
        seed_complexes(conn)
        assert assign.assign_pending(conn, "2026-10") == 7
        rows = {r["apt_seq"]: r for r in conn.execute("SELECT * FROM complexes")}
        assert assign.assign_pending(conn, "2026-10") == 0           # 이미 이 버전으로 판정됨
    assert calls == [list("ABCDEFG"), list("ABCDEFG")]             # 바뀌기 전·후
    got = {k: (r["region_umd_cd"], r["region_sgg_cd"], r["region_match"], r["sgg_mismatch"]) for k, r in rows.items()}
    assert got == {
        "A": ("11110101", "11110", "within", False),
        "B": ("11140101", "11140", "within", True),
        "C": ("11140101", "11140", "code", False),
        "D": ("11110102", "11110", "code", False),
        "E": ("11140101", "11140", "code", False),
        "F": (None, None, "none", False),
        "G": (None, None, "none", False),
    }
    assert all(r["boundary_version"] == "2026-10" for r in rows.values())


def versions_register(conn):
    from geo import versions
    versions.register(conn, "2026-10")


def test_ok_status_without_coords_uses_code(pg, tmp_path, monkeypatch):
    make_version(tmp_path, "2026-10")
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    with pg.connection() as conn:
        versions_register(conn)
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, api_umd_cd, lon, lat, geocode_status) VALUES
            ('N', '11110', '10100', NULL, NULL, 'ok')""")
        assert assign.assign_pending(conn, "2026-10") == 1
        n = conn.execute("SELECT * FROM complexes WHERE apt_seq = 'N'").fetchone()
    assert (n["region_umd_cd"], n["region_match"]) == ("11110101", "code")


def test_code_region():
    valid = {"11110101", "11140101"}
    mapping = {"11999101": "11140101"}
    assert assign.code_region("11110", "10100", valid, mapping) == "11110101"
    assert assign.code_region(" 11110 ", "10100 ", valid, mapping) == "11110101"
    assert assign.code_region("11999", "10123", valid, mapping) == "11140101"
    assert assign.code_region("11110", "99900", valid, mapping) is None
    assert assign.code_region("11110", "1a", valid, mapping) is None
    assert assign.code_region(None, "10100", valid, mapping) is None
    assert assign.code_region("11110", None, valid, mapping) is None


def test_code_map_read_and_digest(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("old_emd_cd,new_emd_cd\n1111010,1114010\n,\n", encoding="utf-8")
    assert code_map.read(p) == {"01111010": "01114010"}
    d1 = code_map.digest(p)
    p.write_text("old_emd_cd,new_emd_cd\n11110101,11140101\n", encoding="utf-8")
    assert code_map.digest(p) != d1
    assert code_map.read(tmp_path / "없음.csv") == {} and code_map.digest(tmp_path / "없음.csv") == "none"


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


def test_apply_results_in_batches(pg, tmp_path, monkeypatch):
    make_version(tmp_path, "2026-10")
    monkeypatch.setattr(assign, "GEO_DATA", tmp_path)
    monkeypatch.setattr(assign, "BATCH", 2)
    calls = []
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [lambda conn, seqs: calls.append(sorted(seqs))])
    with pg.connection() as conn:
        versions_register(conn)
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, api_umd_cd, geocode_status) VALUES
            ('A', '11110', '10100', 'pending'), ('B', '11110', '10100', 'pending'),
            ('C', '11110', '10100', 'pending'), ('D', '11110', '10100', 'pending'),
            ('E', '11110', '10100', 'pending')""")
        assert assign.assign_pending(conn, "2026-10") == 5
        n = conn.execute("SELECT COUNT(*) AS n FROM complexes WHERE boundary_version = '2026-10'").fetchone()["n"]
    assert n == 5
    assert len(calls) == 6                    # 3묶음 × (전·후)

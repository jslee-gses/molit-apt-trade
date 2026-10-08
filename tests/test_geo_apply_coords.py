import csv
from datetime import datetime

import pytest

import settings
from geo import apply_coords, hooks

HEAD = ["단지 코드", "제안 출처", "확신도", "경도", "위도", "좌표 복사할 새 단지 코드"]
ROWS = [
    ["A", "근처 지번(같은 본번)", "중간", "127.01", "37.51", ""],
    ["B", "재건축 새 단지", "중간", "", "", "NEW"],
    ["C", "브이월드 단지명", "중간", "127.03", "37.53", ""],        # 기본값에서 뺀 출처
    ["D", "근처 지번(본번 차이 5)", "낮음", "127.04", "37.54", ""],  # 확신도 낮음
    ["E", "근처 지번(같은 본번)", "높음", "200", "37.5", ""],        # 범위 밖
    ["F", "근처 지번(같은 본번)", "중간", "127.06", "37.56", ""],     # 그사이 좌표가 생김
    ["G", "재건축 새 단지", "중간", "", "", "NOCOORD"],               # 복사할 좌표 없음
]


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 9, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])


def test_pick_defaults():
    rows = [dict(zip(HEAD, r)) for r in ROWS]
    picked = apply_coords.pick(rows, "중간", ["근처", "재건축"])
    assert [p[0] for p in picked] == ["A", "B", "F", "G"]
    assert picked[0] == ("A", 127.01, 37.51, None, "parcel_near") and picked[1] == ("B", None, None, "NEW", "rebuild")
    assert [p[0] for p in apply_coords.pick(rows, "낮음", ["근처", "재건축", "브이월드"])] == ["A", "B", "C", "D", "F", "G"]


def test_save(pg):
    with pg.connection() as conn:
        conn.execute("""INSERT INTO complexes (apt_seq, lon, lat, geocode_status, geocode_source, region_umd_cd, region_match) VALUES
            ('A', NULL, NULL, 'failed', NULL, '11110101', 'code'), ('B', NULL, NULL, 'failed', NULL, NULL, NULL),
            ('F', 127.9, 37.9, 'manual', 'manual', NULL, NULL), ('G', NULL, NULL, 'failed', NULL, NULL, NULL),
            ('NEW', 126.9, 37.6, 'ok', 'parcel', NULL, NULL), ('NOCOORD', NULL, NULL, 'failed', NULL, NULL, NULL)""")
        rows = [dict(zip(HEAD, r)) for r in ROWS]
        result = apply_coords.save(conn, apply_coords.pick(rows, "중간", ["근처", "재건축"]))
        got = {r["apt_seq"]: r for r in conn.execute("SELECT * FROM complexes")}
    assert result == {"located": 2, "skipped": 2}
    assert (got["A"]["lon"], got["A"]["geocode_status"], got["A"]["geocode_source"]) == (127.01, "ok", "parcel_near")
    assert got["A"]["region_umd_cd"] is None                         # 경계로 다시 판정
    assert (got["B"]["lon"], got["B"]["lat"], got["B"]["geocode_source"]) == (126.9, 37.6, "rebuild")
    assert got["F"]["lon"] == 127.9 and got["F"]["geocode_status"] == "manual"   # 이미 좌표 있는 단지는 그대로
    assert got["G"]["geocode_status"] == "failed"


def test_main_dry_run_needs_no_db(tmp_path, capsys):
    path = tmp_path / "r.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        csv.writer(f).writerows([HEAD, *ROWS])
    assert apply_coords.main(["--csv", str(path), "--dry-run"]) == 0
    assert "넣을 대상 4개" in capsys.readouterr().out

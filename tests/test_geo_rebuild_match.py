from datetime import date

from geo import rebuild_match


def seed(conn):
    conn.execute("""INSERT INTO complexes (apt_seq, apt_nm, api_sgg_cd, api_umd_nm, jibun, build_year, last_deal_date,
                                           region_umd_cd, lon, lat, geocode_status, geocode_source) VALUES
        ('OLD', '주공', '11110', '청운동', '621', 1982, '2011-01-31', '11110101', NULL, NULL, 'failed', NULL),
        ('NEW', '힐스테이트', '11110', '청운동', '621-3', 2023, '2026-09-01', '11110101', 127.0, 37.5, 'ok', 'parcel'),
        ('FAR', '자이', '11110', '청운동', '900', 2015, '2026-09-01', '11110101', 127.0, 37.5, 'ok', 'parcel'),
        ('OLDER', '옛단지', '11110', '청운동', '620', 1980, '2026-09-01', '11110101', 127.0, 37.5, 'ok', 'parcel'),
        ('OTHER', '다른동', '11110', '신교동', '621', 2023, '2026-09-01', '11110102', 127.0, 37.5, 'ok', 'parcel'),
        ('LIVE', '현존', '11110', '청운동', '10', 1990, '2026-09-01', '11110101', NULL, NULL, 'pending', NULL),
        ('NOREG', '판정없음', '11110', '신교동', '5', 1985, '2010-05-01', NULL, NULL, NULL, 'failed', NULL)""")


def test_rebuild_candidates(pg):
    with pg.connection() as conn:
        seed(conn)
        rows = rebuild_match.build_rows(conn.execute(rebuild_match.QUERY).fetchall(), date(2026, 10, 9))
    by = {r[0]: dict(zip(rebuild_match.COLUMNS, r)) for r in rows}
    assert set(by) == {"OLD", "LIVE", "NOREG"}                     # 좌표 없는 단지만
    old = by["OLD"]
    assert old["판정"] == "후보" and old["후보1 코드"] == "NEW" and old["후보1 본번 차이"] == 0
    assert old["후보2 단지명"] == "자이" and old["후보3 단지명"] == ""  # 더 오래된 단지·다른 동은 후보 아님
    assert by["LIVE"]["판정"] == "후보 없음"                         # 최근에도 거래: 그 뒤에 지은 단지가 없어 재건축 후보 없음
    assert by["NOREG"]["판정"] == "확인 필요" and by["NOREG"]["후보1 코드"] == "OTHER"   # 판정 없으면 신고 시군구 + 법정동, 본번이 멀어 확인 필요


def test_main_writes_csv(pg, tmp_path, monkeypatch):
    with pg.connection() as conn:
        seed(conn)
    out = tmp_path / "c.csv"
    assert rebuild_match.main(["--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8-sig")
    assert text.startswith("단지 코드,단지명") and "힐스테이트" in text and "127.0" not in text   # 좌표 숫자 없음

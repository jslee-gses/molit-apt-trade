"""분석 API 테스트용 데이터: 2024-01~2026-09, 종로구(단지 A·B)·중구(단지 C)."""
from analytics import aggregates
from collector import codes, store
from tests.helpers import add_job, item

V = "2026-10"
REGIONS = [("11", "sido", "서울특별시", "서울특별시", None),
           ("11110", "sgg", "종로구", "서울특별시 종로구", "11"),
           ("11140", "sgg", "중구", "서울특별시 중구", "11"),
           ("11110101", "umd", "청운동", "서울특별시 종로구 청운동", "11110"),
           ("11110102", "umd", "신교동", "서울특별시 종로구 신교동", "11110"),
           ("11140101", "umd", "무교동", "서울특별시 중구 무교동", "11140")]


def seed_analytics(conn):
    """store.AFTER_SAVE는 호출하는 쪽에서 비워 둔다."""
    conn.execute("INSERT INTO boundary_versions (version, loaded_at, is_active) VALUES (%s, now(), true)", (V,))
    with conn.cursor() as cur:
        cur.executemany("INSERT INTO regions (boundary_version, region_cd, level, name, full_name, parent_cd) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", [(V, *r) for r in REGIONS])
    conn.execute("""INSERT INTO complexes (apt_seq, apt_nm, api_sgg_cd, api_umd_nm, build_year, geocode_status,
                        lon, lat, region_sgg_cd, region_umd_cd, region_match, boundary_version, last_deal_date) VALUES
        ('A', '청운아파트', '11110', '청운동', 2001, 'ok', 126.955, 37.575, '11110', '11110101', 'within', %(v)s, '2026-09-20'),
        ('B', '신교빌', '11110', '신교동', 2010, 'ok', 126.965, 37.575, '11110', '11110102', 'within', %(v)s, '2026-09-20'),
        ('C', '무교타워', '11140', '무교동', 2015, 'failed', NULL, NULL, NULL, NULL, NULL, NULL, '2026-09-20')""",
                 {"v": V})
    for i, ym in enumerate(codes.month_range("202401", "202609")):
        m = str(int(ym[4:]))
        y = ym[:4]
        jongno = [item(aptSeq="A", aptNm="청운아파트", dealYear=y, dealMonth=m, dealDay=str(d), dealAmount=str(50000 + 100 * i + d),
                       excluUseAr="59.9") for d in (1, 2, 3)]
        jongno += [item(aptSeq="B", aptNm="신교빌", dealYear=y, dealMonth=m, dealDay=str(d), dealAmount=str(90000 + 200 * i),
                        excluUseAr="114.8") for d in (4, 5)]
        junggu = [item(aptSeq="C", aptNm="무교타워", sggCd="11140", dealYear=y, dealMonth=m, dealDay=str(d),
                       dealAmount=str(40000 + 50 * i), excluUseAr="84.0") for d in (6, 7)]
        for lawd, items_list in (("11110", jongno), ("11140", junggu)):
            add_job(conn, lawd, ym)
            store.save_job(conn, lawd, ym, items_list, len(items_list))
    aggregates.rebuild_all(conn, V, "live")

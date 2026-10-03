"""테스트용 API 항목·작업 생성 도우미."""


def item(**overrides):
    """국토부 API 응답 항목 하나(camelCase). 필요한 값만 덮어쓴다."""
    base = dict(
        sggCd="11110", umdCd="10100", umdNm="청운동", jibun="1", aptNm="테스트아파트", aptSeq="11110-1",
        roadNm="자하문로", roadNmSggCd="11110", roadNmCd="4100135", roadNmbCd="0",
        roadNmBonbun="00001", roadNmBubun="00000",
        dealYear="2026", dealMonth="1", dealDay="5", dealAmount="84,000", excluUseAr="84.97",
        floor="12", buildYear="2005", cdealType="",
    )
    base.update(overrides)
    return base


def add_job(conn, lawd_cd, deal_ymd, **cols):
    conn.execute("INSERT INTO jobs(lawd_cd, deal_ymd) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                 (lawd_cd, deal_ymd))
    if cols:
        sets = ", ".join(f"{k} = %({k})s" for k in cols)
        conn.execute(f"UPDATE jobs SET {sets} WHERE lawd_cd = %(lawd_cd)s AND deal_ymd = %(deal_ymd)s",
                     {**cols, "lawd_cd": lawd_cd, "deal_ymd": deal_ymd})

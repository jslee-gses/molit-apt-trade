"""[로컬 실행·읽기 전용] 좌표를 못 찾은 옛 단지(대개 재건축으로 사라진 단지)를 지금 그 자리의 새 단지와 짝짓는다.

사용:
  python -m geo.rebuild_match --out <후보.csv> [--database-url URL]

- 대상: 좌표가 없는 단지(geocode_status pending·failed).
- 후보: 같은 읍면동(판정된 읍면동 코드, 없으면 신고 시군구 + 법정동 이름)에 있는 좌표 있는 단지 중
  옛 단지보다 5년 이상 뒤에 지어졌고, 옛 단지의 마지막 거래 해 - 1년 이후에 지어진 단지.
- 순위: 지번 본번 차이가 작은 순 → 건축년도 늦은 순. 옛 단지마다 후보 3개까지.
- 판정: 1순위 후보의 본번 차이가 0~20이고 옛 단지 마지막 거래가 2년 넘게 지났으면 "후보", 아니면 "확인 필요".
- DB에 쓰지 않는다. 좌표 숫자는 출력하지 않는다(적용은 검토 뒤 별도 단계).
"""
import argparse
import csv
import os
import sys

QUERY = r"""
WITH o AS (
    SELECT apt_seq, apt_nm, api_sgg_cd, api_umd_nm, jibun, build_year, last_deal_date, region_umd_cd, geocode_status,
           NULLIF(substring(jibun FROM '^(?:산\s?)?([0-9]+)'), '')::int AS bon
      FROM complexes WHERE geocode_status IN ('pending', 'failed')),
c AS (
    SELECT apt_seq, apt_nm, api_sgg_cd, api_umd_nm, jibun, build_year, region_umd_cd,
           NULLIF(substring(jibun FROM '^(?:산\s?)?([0-9]+)'), '')::int AS bon
      FROM complexes
     WHERE geocode_status IN ('ok', 'manual') AND lon IS NOT NULL AND lat IS NOT NULL AND build_year IS NOT NULL),
pairs AS (
    SELECT o.*, c.apt_seq AS c_seq, c.apt_nm AS c_nm, c.jibun AS c_jibun, c.build_year AS c_build_year,
           abs(o.bon - c.bon) AS bon_diff,
           row_number() OVER (PARTITION BY o.apt_seq ORDER BY abs(o.bon - c.bon) NULLS LAST, c.build_year DESC, c.apt_seq) AS rn
      FROM o JOIN c
        ON (o.region_umd_cd IS NOT NULL AND c.region_umd_cd = o.region_umd_cd)
        OR (o.region_umd_cd IS NULL AND c.api_sgg_cd = o.api_sgg_cd AND c.api_umd_nm = o.api_umd_nm)
     WHERE (o.build_year IS NULL OR c.build_year >= o.build_year + 5)
       AND (o.last_deal_date IS NULL OR c.build_year >= EXTRACT(YEAR FROM o.last_deal_date)::int - 1))
SELECT o.apt_seq, o.apt_nm, o.api_sgg_cd, o.api_umd_nm, o.jibun, o.build_year, o.last_deal_date, o.geocode_status,
       p.rn, p.c_seq, p.c_nm, p.c_jibun, p.c_build_year, p.bon_diff
  FROM o LEFT JOIN pairs p ON p.apt_seq = o.apt_seq AND p.rn <= 3
 ORDER BY o.apt_seq, p.rn
"""

COLUMNS = ["단지 코드", "단지명", "신고 시군구 코드", "법정동", "지번", "건축년도", "최근 계약일", "판정",
           "후보1 코드", "후보1 단지명", "후보1 지번", "후보1 건축년도", "후보1 본번 차이",
           "후보2 단지명", "후보2 지번", "후보2 건축년도", "후보3 단지명", "후보3 지번", "후보3 건축년도"]


def judge(old, first, today):
    if first is None:
        return "후보 없음"
    stale = old["last_deal_date"] is not None and (today - old["last_deal_date"]).days > 730
    if first["bon_diff"] is not None and first["bon_diff"] <= 20 and stale:
        return "후보"
    return "확인 필요"


def build_rows(records, today):
    by_seq = {}
    for r in records:
        by_seq.setdefault(r["apt_seq"], []).append(r)
    out = []
    for seq, rs in by_seq.items():
        old = rs[0]
        cands = [r for r in rs if r["rn"] is not None]
        first = cands[0] if cands else None
        row = [seq, old["apt_nm"], old["api_sgg_cd"], old["api_umd_nm"], old["jibun"], old["build_year"],
               old["last_deal_date"], judge(old, first, today)]
        row += ([first["c_seq"], first["c_nm"], first["c_jibun"], first["c_build_year"], first["bon_diff"]]
                if first else [""] * 5)
        for c in cands[1:3]:
            row += [c["c_nm"], c["c_jibun"], c["c_build_year"]]
        row += [""] * (len(COLUMNS) - len(row))
        out.append(["" if v is None else v for v in row])
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="좌표 없는 옛 단지 ↔ 그 자리의 새 단지 후보(읽기 전용)")
    parser.add_argument("--out", required=True, help="후보 CSV 경로")
    parser.add_argument("--database-url", help="대상 DB(기본: DATABASE_URL)")
    args = parser.parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    import db
    import settings
    from geo.parcel_points import describe_target

    url = settings.env("DATABASE_URL")
    if not url:
        print("DATABASE_URL이 없습니다. --database-url을 주거나 .env를 설정하세요.")
        return 1
    print(f"대상 DB: {describe_target(url)[0]} (읽기만 합니다)")
    db.close_pool()
    with db.connection() as conn:
        records = conn.execute(QUERY).fetchall()
    rows = build_rows(records, settings.now_kst().date())
    with open(args.out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerows(rows)
    counts = {}
    for r in rows:
        counts[r[7]] = counts.get(r[7], 0) + 1
    print(f"좌표 없는 단지 {len(rows):,}개 → " + " · ".join(f"{k} {v:,}" for k, v in counts.items()))
    print(f"저장: {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

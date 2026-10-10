"""[로컬 실행] 검토한 좌표표(coords_review.csv)를 좌표 없는 단지에 넣는다.

사용:
  python -m geo.apply_coords --csv <검토표.csv> [--min 중간] [--sources 근처,재건축] [--database-url URL] [--dry-run] [--yes]

- 검토표 열: 단지 코드, 제안 출처, 확신도(높음/중간/낮음), 경도, 위도, 좌표 복사할 새 단지 코드.
- --min 이상 확신도이고 제안 출처가 --sources 중 하나로 시작하는 행만 넣는다(기본: 중간 이상, 근처 지번·재건축).
  브이월드 API에서 온 좌표는 약관 확인 전에는 넣지 않도록 기본값에서 뺐다.
- '재건축 새 단지'는 좌표 대신 새 단지 코드를 받아, 저장 시점의 그 단지 좌표(ok·manual)를 복사한다.
- 좌표 없는 단지(pending·failed)만 바꾼다. 저장하면 geocode_status ok, geocode_source parcel_near / rebuild / user(사용자가 따로 찾은 좌표),
  지역 판정을 지워 다음 지리 처리가 경계로 다시 판정한다.
- 시작할 때 대상 DB(host:port/dbname)를 출력한다. 로컬이 아닌 DB는 --yes가 있어야 저장한다.
"""
import argparse
import csv
import math
import os
import sys

LEVELS = {"높음": 0, "중간": 1, "낮음": 2}
SOURCE_TAG = {"근처": "parcel_near", "재건축": "rebuild", "사용자": "user", "브이월드": "vworld"}
KOREA = (124.0, 33.0, 132.0, 39.0)


def pick(rows, min_level, sources):
    """→ [(apt_seq, lon|None, lat|None, 복사할 단지|None, source)]"""
    out = []
    for r in rows:
        level = LEVELS.get(r.get("확신도", ""))
        src = next((s for s in sources if r.get("제안 출처", "").startswith(s)), None)
        if level is None or level > LEVELS[min_level] or src is None:
            continue
        copy_from = (r.get("좌표 복사할 새 단지 코드") or "").strip() or None
        lon = lat = None
        if not copy_from:
            try:
                lon, lat = float(r["경도"]), float(r["위도"])
            except (KeyError, ValueError):
                continue
            if not (math.isfinite(lon) and math.isfinite(lat) and KOREA[0] <= lon <= KOREA[2] and KOREA[1] <= lat <= KOREA[3]):
                continue
        out.append((r["단지 코드"], lon, lat, copy_from, SOURCE_TAG[src]))
    return out


def save(conn, picked):
    """→ {"located", "skipped"}. 저장 시점에 좌표가 생긴 단지·복사할 좌표가 없는 단지는 건너뛴다."""
    import settings
    from geo import complexes, hooks

    with conn.transaction():
        complexes.lock_complexes(conn)
        conn.execute("CREATE TEMP TABLE coords_stage (apt_seq TEXT PRIMARY KEY, lon DOUBLE PRECISION, "
                     "lat DOUBLE PRECISION, copy_from TEXT, source TEXT) ON COMMIT DROP")
        with conn.cursor() as cur, cur.copy("COPY coords_stage (apt_seq, lon, lat, copy_from, source) FROM STDIN") as copy:
            for row in picked:
                copy.write_row(row)
        conn.execute("""
            UPDATE coords_stage s SET lon = c.lon, lat = c.lat
              FROM complexes c
             WHERE s.copy_from IS NOT NULL AND c.apt_seq = s.copy_from
               AND c.geocode_status IN ('ok', 'manual') AND c.lon IS NOT NULL AND c.lat IS NOT NULL""")
        seqs = [r["apt_seq"] for r in conn.execute("""
            SELECT s.apt_seq FROM coords_stage s JOIN complexes c ON c.apt_seq = s.apt_seq
             WHERE s.lon IS NOT NULL AND s.lat IS NOT NULL AND c.geocode_status IN ('pending', 'failed')
             ORDER BY 1 FOR UPDATE OF c""")]
        if seqs:
            for hook in hooks.ON_REGION_CHANGE:   # 바뀌기 전 지역
                hook(conn, seqs)
            conn.execute("""
                UPDATE complexes c SET lon = s.lon, lat = s.lat, geocode_status = 'ok', geocode_source = s.source,
                       geocoded_at = %s, region_sgg_cd = NULL, region_umd_cd = NULL, region_match = NULL,
                       boundary_version = NULL, sgg_mismatch = false
                  FROM coords_stage s
                 WHERE c.apt_seq = s.apt_seq AND c.apt_seq = ANY(%s)""", (settings.now_ts(), seqs))
            for hook in hooks.ON_REGION_CHANGE:   # 바뀐 뒤 지역
                hook(conn, seqs)
    return {"located": len(seqs), "skipped": len(picked) - len(seqs)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="검토한 좌표표 → 좌표 없는 단지")
    parser.add_argument("--csv", required=True, help="검토표(coords_review.csv)")
    parser.add_argument("--min", default="중간", choices=list(LEVELS), help="이 확신도 이상만(기본: 중간)")
    parser.add_argument("--sources", default="근처,재건축", help="넣을 제안 출처(쉼표로, 기본: 근처,재건축)")
    parser.add_argument("--database-url", help="대상 DB(기본: DATABASE_URL)")
    parser.add_argument("--dry-run", action="store_true", help="저장하지 않고 개수만 출력")
    parser.add_argument("--yes", action="store_true", help="로컬이 아닌 DB에 저장하는 것을 확인함")
    args = parser.parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    if unknown := [s for s in sources if s not in SOURCE_TAG]:
        print(f"알 수 없는 제안 출처: {', '.join(unknown)} (가능: {', '.join(SOURCE_TAG)})")
        return 1
    with open(args.csv, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    picked = pick(rows, args.min, sources)
    by_src = {}
    for p in picked:
        by_src[p[4]] = by_src.get(p[4], 0) + 1
    print(f"검토표 {len(rows):,}행 → 넣을 대상 {len(picked):,}개 ({', '.join(f'{k} {v:,}' for k, v in by_src.items()) or '없음'}) · "
          f"확신도 {args.min} 이상 · 출처 {', '.join(sources)}")
    if args.dry_run:
        print("--dry-run: DB에 접속하지 않고 저장하지 않습니다.")
        return 0
    if not picked:
        return 1

    import settings
    from geo.parcel_points import LOCAL_HOSTS, describe_target

    url = settings.env("DATABASE_URL")
    if not url:
        print("DATABASE_URL이 없습니다. --database-url을 주거나 .env를 설정하세요.")
        return 1
    shown, host = describe_target(url)
    print(f"대상 DB: {shown}")
    if host not in LOCAL_HOSTS and not args.yes:
        print("로컬이 아닌 DB입니다. 확인했다면 --yes를 붙여 다시 실행하세요.")
        return 1

    import db
    import wiring

    db.close_pool()
    wiring.wire()        # 좌표가 바뀐 단지의 집계 대기열 훅
    with db.connection() as conn:
        result = save(conn, picked)
    print(f"저장: 좌표 {result['located']:,} · 건너뜀 {result['skipped']:,}(이미 좌표가 생겼거나 복사할 새 단지 좌표 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

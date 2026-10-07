"""월별 집계(agg_month): 원본 거래를 최신 경계 기준 전국·시도·시군구·읍면동 x 면적 구간으로 요약한다.

- 계약월 단위로 통째로 다시 계산한다(한 달 전국 거래 수만 건 → 1초 안팎).
- 해제 거래·금액 없는 거래는 빼고, 완전 중복 행은 1건으로 센다.
- 지역은 단지 판정 결과(complexes.region_*)를 쓴다. 판정은 좌표 경계로, 좌표가 없거나 경계 밖이면 법정동 코드로 한다.
  둘 다 안 되어 판정이 없으면 시군구만 수집 코드(lawd_cd)로 대신하고 읍면동 집계에서는 빠진다.
- 수집 저장·지역 변경이 생긴 계약월은 agg_dirty에 표시하고, 지리 처리(10분 주기) 끝에 다시 계산한다.
"""
import logging

import settings
from collector import api
from geo import versions

log = logging.getLogger(__name__)

MAPPINGS = {
    "live": "SELECT apt_seq, region_sgg_cd AS sgg, region_umd_cd AS umd FROM complexes",
    "staged": "SELECT apt_seq, sgg, umd FROM staged_regions",
}
_DEDUP_COLS = ", ".join(["lawd_cd", *api.COLUMNS])


def _refresh_sql(mapping):
    return """
WITH m AS (""" + MAPPINGS[mapping] + """),
d AS (
    SELECT DISTINCT """ + _DEDUP_COLS + """
      FROM trades
     WHERE deal_ymd = %(ym)s AND NOT is_cancelled AND deal_amount IS NOT NULL
),
base AS (
    SELECT COALESCE(m.sgg, d.lawd_cd) AS sgg, m.umd,
           d.deal_amount::float8 AS price, d.exclu_use_ar::float8 AS area
      FROM d LEFT JOIN m ON m.apt_seq = btrim(d.apt_seq)
),
banded AS (
    SELECT left(sgg, 2) AS sido, sgg, umd, price,
           CASE WHEN area > 0 THEN price / area END AS ppm2, v.band
      FROM base
     CROSS JOIN LATERAL (VALUES ('all'),
           (CASE WHEN area IS NULL THEN NULL WHEN area <= 60 THEN 'le60'
                 WHEN area <= 85 THEN '60_85' ELSE 'gt85' END)) AS v(band)
     WHERE v.band IS NOT NULL
),
g AS (
    SELECT GROUPING(sido) AS g_sido, GROUPING(sgg) AS g_sgg, GROUPING(umd) AS g_umd,
           sido, sgg, umd, band, COUNT(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY price) AS median_price,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY price) AS p25_price,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY price) AS p75_price,
           avg(price) AS mean_price,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY ppm2) AS median_ppm2
      FROM banded
     GROUP BY GROUPING SETS ((sido, sgg, umd, band), (sido, sgg, band), (sido, band), (band))
)
INSERT INTO agg_month (boundary_version, level, region_cd, ym, size_band, n_trades,
                       median_price, p25_price, p75_price, mean_price, median_ppm2)
SELECT %(version)s,
       CASE WHEN g_sido = 1 THEN 'nation' WHEN g_sgg = 1 THEN 'sido' WHEN g_umd = 1 THEN 'sgg' ELSE 'umd' END,
       CASE WHEN g_sido = 1 THEN '00' WHEN g_sgg = 1 THEN sido WHEN g_umd = 1 THEN sgg ELSE umd END,
       %(ym)s, band, n, median_price, p25_price, p75_price, mean_price, median_ppm2
  FROM g
 WHERE NOT (g_umd = 0 AND umd IS NULL)
"""


def refresh_month(conn, ym, version, mapping="live"):
    """version의 ym 집계를 통째로 다시 계산한다."""
    with conn.transaction():
        conn.execute("DELETE FROM agg_month WHERE boundary_version = %s AND ym = %s", (version, ym))
        conn.execute(_refresh_sql(mapping), {"ym": ym, "version": version})


def _mark_ts():
    """대기열 표시 시각: KST, 시간대 없음, 마이크로초까지(같은 초 안의 표시가 처리 때 지워지지 않게)."""
    return settings.now_kst().replace(tzinfo=None)


def mark_months(conn, yms):
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO agg_dirty (ym, marked_at) VALUES (%s, %s) "
            "ON CONFLICT (ym) DO UPDATE SET marked_at = EXCLUDED.marked_at",
            [(ym, _mark_ts()) for ym in sorted(set(yms))])


def on_saved(conn, lawd_cd, deal_ymd, rows):
    """store.AFTER_SAVE: 저장한 작업의 계약월을 대기열에."""
    mark_months(conn, [deal_ymd])


def on_region_change(conn, apt_seqs):
    """geo.hooks.ON_REGION_CHANGE: 지역이 바뀌는 단지의 거래가 있는 계약월을 대기열에."""
    conn.execute("""
        INSERT INTO agg_dirty (ym, marked_at)
        SELECT DISTINCT deal_ymd, %s FROM trades WHERE apt_seq = ANY(%s)
        ON CONFLICT (ym) DO UPDATE SET marked_at = EXCLUDED.marked_at""",
                 (_mark_ts(), list(apt_seqs)))


def refresh_dirty(conn):
    """대기열의 계약월을 활성 버전으로 다시 계산한다. 활성 버전이 없으면 기다린다. → 처리한 달 수"""
    version = versions.active(conn)
    if version is None:
        return 0
    if not conn.execute("SELECT 1 FROM agg_month WHERE boundary_version = %s LIMIT 1", (version,)).fetchone():
        # 이 버전 집계가 아직 없다(이미 활성인 경계가 있는 채로 배포한 경우): 거래가 있는 모든 달을 대기열에
        mark_months(conn, [r["deal_ymd"] for r in conn.execute(
            "SELECT DISTINCT deal_ymd FROM jobs WHERE stored_count > 0")])
    todo = conn.execute("SELECT ym, marked_at FROM agg_dirty ORDER BY ym DESC").fetchall()
    done = 0
    for row in todo:
        try:
            with conn.transaction():
                refresh_month(conn, row["ym"], version)
                # 계산 도중 새로 표시된 달은 남겨 둔다
                conn.execute("DELETE FROM agg_dirty WHERE ym = %s AND marked_at <= %s", (row["ym"], row["marked_at"]))
            done += 1
        except Exception:
            log.exception("%s 집계 갱신 실패: 대기열에 남기고 다음 실행에서 다시 시도합니다", row["ym"])
    return done


def rebuild_all(conn, version, mapping):
    """거래가 있는 모든 계약월을 다시 계산한다. → 계산한 달 수"""
    yms = [r["deal_ymd"] for r in conn.execute(
        "SELECT DISTINCT deal_ymd FROM jobs WHERE stored_count > 0 ORDER BY 1")]
    for ym in yms:
        refresh_month(conn, ym, version, mapping)
    return len(yms)


def before_activate(conn, version):
    """경계 전환 직전(같은 트랜잭션): 새 경계 판정(staged_regions)으로 새 버전 집계를 만든다.
    대기열은 비우지 않는다(전환 중 들어온 거래는 전환 뒤 다시 계산)."""
    rebuild_all(conn, version, "staged")


def after_activate(conn, version):
    """경계 전환 직후: 이전 버전 집계를 지운다."""
    conn.execute("DELETE FROM agg_month WHERE boundary_version <> %s", (version,))


def status(conn):
    version = versions.active(conn)
    dirty = conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"]
    row = conn.execute("SELECT COUNT(*) AS n, MAX(ym) AS latest FROM agg_month WHERE boundary_version = %s",
                       (version,)).fetchone()
    return dict(dirty=dirty, rows=row["n"], latest_ym=row["latest"])

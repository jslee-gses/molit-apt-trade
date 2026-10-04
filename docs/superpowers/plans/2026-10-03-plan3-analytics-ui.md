# 계획 3: 집계·API·분석 화면 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 최신 경계 기준 월별 집계(`agg_month`)를 만들고, 그 위에 추이·지도·단지·추출 API와 화면(대시보드, 추이, 지도, 단지, 추출)을 올린다.

**Architecture:** `analytics/aggregates.py`가 계약월 단위로 전국·시도·시군구·읍면동 × 면적 구간 집계를 SQL(GROUPING SETS + percentile_cont)로 다시 계산한다. 수집 저장·지역 변경이 생긴 달은 `agg_dirty`에 표시되고, 지리 처리(10분 주기) 끝에 처리된다. 경계 전환 때는 임시 테이블 `staged_regions`로 새 버전 집계를 미리 만든 뒤 전환한다. `analytics/queries.py`·`analytics/export.py`가 조회·추출을 맡고, `web/`이 JSON API와 Jinja 화면을, `static/js/`가 ECharts 차트를 맡는다.

**Tech Stack:** Postgres(GROUPING SETS, percentile_cont), Flask, pyarrow 21 이상(Parquet), ECharts 5.6(cdnjs), 바닐라 JS

**Spec:** `docs/superpowers/specs/2026-10-03-analysis-dashboard-design.md` (§1.2–1.3, §4.5, §5.1, §5.3, §6, §7, §8, §9)

**선행:** 계획 1, 계획 2 완료. 이 계획은 그 인터페이스(`db.connection`, `store.AFTER_SAVE`, `geo.hooks.*`, `geo.versions.active/switch`, `geo.pipeline.AFTER_RUN`, `wiring.wire`, `complexes`, `regions`, 픽스처 `pg`·`app`·`client`, `tests.helpers.item/add_job`)를 쓴다.

**명세와 다른 구현 세부(의도적):**
- 집계 대기열은 `(시군구, 계약월)`이 아니라 **계약월** 단위다. 한 달 전국 재계산이 1초 안팎이라 더 단순하고, 시도·전국 중위값을 다시 계산해야 하는 점은 어차피 같다.
- `agg_coverage` 테이블은 만들지 않는다. 읍면동 커버리지는 `agg_month`의 (읍면동 거래 합 / 시군구 거래)로 바로 계산된다.
- 증분 집계는 "수집 종료 직후 + 매시"가 아니라 지리 처리와 함께 **10분마다** 돈다(대기열이 비면 아무것도 하지 않음).
- 지도의 "기간 값"은 월별 중위가를 거래량으로 가중평균한 값이다(기간 중위가를 정확히 구하려면 원본을 다시 훑어야 함). 화면에 그렇게 표기한다.

## Global Constraints

- 계획 1·2의 Global Constraints 전부.
- 집계 규칙: 해제 거래(`is_cancelled`)·금액 없는 거래 제외, 완전 중복 행은 1건, 지역은 `complexes.region_*`, 판정이 없으면 시군구만 `lawd_cd`로 대신하고 읍면동 집계에서는 뺀다.
- 면적 구간 `size_band`: `all` / `le60`(≤60㎡) / `60_85`(60㎡ 초과 85㎡ 이하) / `gt85`(85㎡ 초과).
- 지역 수준 `level`: `nation`(코드 `00`) / `sido`(2자리) / `sgg`(5자리) / `umd`(8자리).
- 잠정: 최근 2개월(이번 달·지난달). `provisional_from = months_ago(1)`. 차트에 음영.
- 추이 비교 지역은 최대 8개. 원본 추출은 한 요청당 최대 60개월.
- 집계 조회 API 응답에 `Cache-Control: private, max-age=600`.
- 파라미터 오류는 400 + `{"error": "<한국어 메시지>"}`. 활성 경계가 없으면 503 + `{"error"}`.
- 차트: 이중 축 금지(측정값이 다르면 차트를 나눈다). 범주 색은 `--series-1`~`--series-8` 고정 순서이며 지역에 붙고 순위에 붙지 않는다. 계열 2개 이상이면 범례. 크기·연속값은 단일 색조 순차 램프, 증감은 파랑↔빨강 발산(가운데 회색, 상승=빨강). 글자는 글자 색 토큰을 쓰고 계열 색을 쓰지 않는다. 모든 차트에 툴팁. 표 보기 제공(추이).
- 외부 스크립트는 cdnjs의 ECharts 하나만(`https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js`).

## Review Focus

1. **거래가 없는 달**: 어떤 지역·면적 구간에 거래가 0건인 달은 집계 행이 없다. 추이 차트는 그 달을 0이 아니라 끊긴 선(빈 값)으로 그려야 하고, 이동평균·지수도 빈 값을 0으로 취급하면 안 된다 → Task 2 `test_agg_series_has_gaps`(서버가 빈 달을 만들지 않음) + Task 5 브라우저 확인 항목.
2. **좌표 미판정 거래**: 단지 좌표가 없는 거래가 시군구·시도·전국 합계에는 들어가고 읍면동에는 빠져야 하며, 읍면동 지도에 커버리지가 표시돼야 한다 → Task 1 `test_unassigned_counts_in_sgg_not_umd`, Task 2 `test_map_umd_coverage`.
3. **API 시군구와 좌표 시군구가 다른 단지**: 좌표 기준 시군구로 집계돼야 한다(명세: 최신 경계 기준) → Task 1 `test_refresh_month_matches_pandas`의 단지 D.
4. **잘못된 조회 조건**: 5자리가 아닌 시군구 코드, 9개 이상 지역, 시작월 > 종료월, 없는 면적 구간, 61개월 원본 추출은 400과 이유를 돌려줘야 한다 → Task 2 `test_agg_rejects_bad_params`, Task 3 `test_export_raw_limit`.
5. **경계 전환 중 수집**: 전환 집계(임시 매핑) 도중 들어온 새 거래의 달은 대기열에 남아 전환 뒤 다시 계산돼야 한다 → Task 1 `test_before_activate_keeps_dirty_queue`.

---

## File Structure

| 파일 | 책임 |
|---|---|
| `migrations/003_agg.sql` (새로) | `agg_month`, `agg_dirty` |
| `analytics/__init__.py` (새로) | 빈 패키지 |
| `analytics/params.py` (새로) | 조회 조건 검증(`BadParam`), 수준·면적 구간·계약월·지역 목록 |
| `analytics/aggregates.py` (새로) | 월별 집계 계산, 대기열, 경계 전환 훅, 현황 |
| `analytics/queries.py` (새로) | 지역 목록, 추이, 지도 값, 대시보드 요약, 단지 검색·상세 |
| `analytics/export.py` (새로) | 원본·집계 추출 쿼리, CSV·Parquet 스트리밍, 코드북 |
| `wiring.py` (수정) | 집계 훅 연결 |
| `web/api.py` (수정) | 분석 JSON API, 오류 처리, 캐시 헤더 |
| `web/export.py` (새로) | `/export`, `/export.csv`, `/export.parquet`, `/export/codebook.csv` |
| `web/pages.py` (수정) | `/` 대시보드, `/trades`(옛 목록), `/trends`, `/map`, `/complexes`, `/complexes/<seq>`, `/download.csv` 리다이렉트 |
| `templates/base.html` (수정) | 메뉴, 차트 색 토큰, 스크립트 블록 |
| `templates/_charts.html`, `dashboard.html`, `trends.html`, `map.html`, `complexes.html`, `complex.html`, `export.html` (새로) | 화면 |
| `templates/index.html` → `templates/trades.html` (이름 변경) | 옛 거래 목록 |
| `templates/status.html` (수정) | 집계 현황 |
| `static/js/common.js`, `dashboard.js`, `trends.js`, `map.js`, `complex.js`, `export.js` (새로) | 차트·상호작용 |
| `scripts/seed_dev.py` (새로) | [로컬] 화면 확인용 가짜 데이터 |
| `requirements.txt` (수정) | pyarrow |
| `tests/analytics_fixtures.py`, `tests/test_aggregates.py`, `tests/test_analytics_api.py`, `tests/test_export.py`, `tests/test_pages.py` (새로), `tests/test_web.py` (수정) | 테스트 |
| `README.md` (수정) | 화면·API·추출 설명 |

---

### Task 1: 월별 집계와 대기열

**Files:**
- Create: `migrations/003_agg.sql`, `analytics/__init__.py`, `analytics/aggregates.py`, `tests/test_aggregates.py`
- Modify: `wiring.py`

**Interfaces:**
- Consumes: 계획 2 `geo.versions.active`, `geo.hooks.*`, `geo.pipeline.AFTER_RUN`, 임시 테이블 `staged_regions(apt_seq, umd, sgg, ...)`
- Produces:
  - 테이블 `agg_month(boundary_version, level, region_cd, ym, size_band, n_trades, median_price, p25_price, p75_price, mean_price, median_ppm2)` PK `(boundary_version, level, region_cd, size_band, ym)`, 가격 단위 만원, ㎡당 가격 만원/㎡, 실수형
  - 테이블 `agg_dirty(ym PK, marked_at)`
  - `analytics.aggregates.refresh_month(conn, ym, version, mapping="live"|"staged")`
  - `aggregates.mark_months(conn, yms)`, `aggregates.on_saved(conn, lawd_cd, deal_ymd, rows)`(AFTER_SAVE), `aggregates.on_region_change(conn, apt_seqs)`(ON_REGION_CHANGE)
  - `aggregates.refresh_dirty(conn) -> int`(pipeline.AFTER_RUN), `aggregates.rebuild_all(conn, version, mapping) -> int`
  - `aggregates.before_activate(conn, version)`(BEFORE_ACTIVATE), `aggregates.after_activate(conn, version)`(AFTER_ACTIVATE)
  - `aggregates.status(conn) -> dict(dirty, rows, latest_ym)`

- [ ] **Step 1: 테스트 작성**

`tests/test_aggregates.py`:
```python
from datetime import datetime

import pandas as pd
import pytest

import settings
from analytics import aggregates
from collector import store
from geo import hooks
from tests.helpers import add_job, item

V = "2026-10"


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])


# (lawd_cd, aptSeq, 금액, 면적, 덮어쓸 항목)
TRADES = [
    ("11110", "A", "50,000", "59.5", {}),
    ("11110", "A", "70,000", "84.9", {"dealDay": "6"}),
    ("11110", "A", "70,000", "84.9", {"dealDay": "6"}),          # 완전 중복 → 1건
    ("11110", "B", "90,000", "114.8", {"dealDay": "7"}),
    ("11110", "B", "80,000", "84.9", {"cdealType": "O"}),       # 해제 → 제외
    ("11110", "D", "60,000", "59.9", {"dealDay": "8"}),         # 좌표상 중구
    ("11110", "E", "", "59.9", {}),                              # 금액 없음 → 제외
    ("11140", "C", "40,000", "49.0", {"sggCd": "11140"}),       # 좌표 미판정
    ("11140", "C", "45,000", "49.0", {"sggCd": "11140", "dealDay": "9"}),
]
# 단지 → (시군구, 읍면동)
REGIONS = {"A": ("11110", "11110101"), "B": ("11110", "11110102"), "D": ("11140", "11140101"),
           "C": (None, None), "E": (None, None)}


def seed(conn, ym="202601"):
    for lawd in ("11110", "11140"):
        add_job(conn, lawd, ym)
        items = [item(aptSeq=s, dealAmount=a, excluUseAr=ar, dealMonth=str(int(ym[4:])), **kw)
                 for l, s, a, ar, kw in TRADES if l == lawd]
        store.save_job(conn, lawd, ym, items, len(items))
    for seq, (sgg, umd) in REGIONS.items():
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, geocode_status, region_sgg_cd, region_umd_cd,
                            region_match, boundary_version) VALUES (%s, '11110', 'ok', %s, %s, %s, %s)
                        ON CONFLICT (apt_seq) DO NOTHING""",
                     (seq, sgg, umd, "within" if umd else "none", V))
    conn.execute("INSERT INTO boundary_versions (version, loaded_at, is_active) VALUES (%s, now(), true) "
                 "ON CONFLICT DO NOTHING", (V,))


def band(area):
    return "le60" if area <= 60 else "60_85" if area <= 85 else "gt85"


def expected():
    """같은 규칙을 pandas로 계산한 기대값 {(level, region_cd, size_band): {...}}"""
    seen, rows = set(), []
    for lawd, seq, amount, area, kw in TRADES:
        key = (lawd, seq, amount, area, tuple(sorted(kw.items())))
        if kw.get("cdealType") or not amount or key in seen:
            continue
        seen.add(key)
        sgg, umd = REGIONS[seq]
        price, a = float(amount.replace(",", "")), float(area)
        rows.append(dict(sgg=sgg or lawd, umd=umd, price=price, ppm2=price / a, band=band(a)))
    df = pd.DataFrame(rows)
    df = pd.concat([df.assign(band="all"), df])
    df["sido"], df["nation"] = df["sgg"].str[:2], "00"
    out = {}
    for level, col in [("nation", "nation"), ("sido", "sido"), ("sgg", "sgg"), ("umd", "umd")]:
        for (code, b), g in df.dropna(subset=[col]).groupby([col, "band"]):
            out[(level, code, b)] = dict(
                n_trades=len(g), median_price=g.price.median(), p25_price=g.price.quantile(0.25),
                p75_price=g.price.quantile(0.75), mean_price=g.price.mean(), median_ppm2=g.ppm2.median())
    return out


def fetch(conn, version=V, ym="202601"):
    rows = conn.execute("SELECT * FROM agg_month WHERE boundary_version = %s AND ym = %s", (version, ym)).fetchall()
    return {(r["level"], r["region_cd"], r["size_band"]): r for r in rows}


def test_refresh_month_matches_pandas(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        got = fetch(conn)
    exp = expected()
    assert set(got) == set(exp)
    for key, e in exp.items():
        for k, v in e.items():
            assert got[key][k] == pytest.approx(v), (key, k)
    assert got[("sgg", "11140", "all")]["n_trades"] == 3          # C 2건 + 좌표상 중구인 D 1건


def test_unassigned_counts_in_sgg_not_umd(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        got = fetch(conn)
    umd_sum = sum(r["n_trades"] for (lv, code, b), r in got.items() if lv == "umd" and b == "all"
                  and code.startswith("11140"))
    assert umd_sum == 1 and got[("sgg", "11140", "all")]["n_trades"] == 3
    assert not any(code is None for (_, code, _) in got)


def test_refresh_month_replaces(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        conn.execute("DELETE FROM trades WHERE apt_seq = 'B'")
        aggregates.refresh_month(conn, "202601", V)
        got = fetch(conn)
    assert ("umd", "11110102", "all") not in got


def test_hooks_mark_and_refresh_dirty(pg, monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [aggregates.on_saved])
    with pg.connection() as conn:
        seed(conn)
        assert [r["ym"] for r in conn.execute("SELECT ym FROM agg_dirty")] == ["202601"]
        assert aggregates.refresh_dirty(conn) == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"] == 0
        assert fetch(conn)[("nation", "00", "all")]["n_trades"] == 6
        aggregates.on_region_change(conn, ["C"])
        assert [r["ym"] for r in conn.execute("SELECT ym FROM agg_dirty")] == ["202601"]


def test_refresh_dirty_waits_for_active_version(pg):
    with pg.connection() as conn:
        seed(conn)
        conn.execute("UPDATE boundary_versions SET is_active = false")
        aggregates.mark_months(conn, ["202601"])
        assert aggregates.refresh_dirty(conn) == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"] == 1


def test_incremental_equals_full_rebuild(pg):
    with pg.connection() as conn:
        seed(conn, "202601")
        seed(conn, "202602")
        aggregates.mark_months(conn, ["202601", "202602"])
        aggregates.refresh_dirty(conn)
        inc = {**fetch(conn, ym="202601"), **{("x",) + k: v for k, v in fetch(conn, ym="202602").items()}}
        conn.execute("DELETE FROM agg_month")
        assert aggregates.rebuild_all(conn, V, "live") == 2
        full = {**fetch(conn, ym="202601"), **{("x",) + k: v for k, v in fetch(conn, ym="202602").items()}}
    assert inc == full


def test_before_activate_uses_staged_mapping(pg):
    with pg.connection() as conn:
        seed(conn)
        with conn.transaction():
            conn.execute("CREATE TEMP TABLE staged_regions (apt_seq TEXT PRIMARY KEY, umd TEXT, sgg TEXT, "
                         "match TEXT, version TEXT, mismatch BOOLEAN) ON COMMIT DROP")
            conn.execute("INSERT INTO staged_regions (apt_seq, umd, sgg) VALUES ('A', '11110199', '11110')")
            aggregates.before_activate(conn, "2027-01")
            aggregates.after_activate(conn, "2027-01")
        new = fetch(conn, "2027-01")
        old = fetch(conn, V)
    assert ("umd", "11110199", "all") in new and ("umd", "11110101", "all") not in new
    assert old == {}


def test_before_activate_keeps_dirty_queue(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.mark_months(conn, ["202601"])
        with conn.transaction():
            conn.execute("CREATE TEMP TABLE staged_regions (apt_seq TEXT PRIMARY KEY, umd TEXT, sgg TEXT, "
                         "match TEXT, version TEXT, mismatch BOOLEAN) ON COMMIT DROP")
            aggregates.before_activate(conn, "2027-01")
            aggregates.after_activate(conn, "2027-01")
        assert conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"] == 1


def test_status(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        aggregates.mark_months(conn, ["202602"])
        s = aggregates.status(conn)
    assert s["dirty"] == 1 and s["rows"] > 0 and s["latest_ym"] == "202601"


def test_wire_connects_aggregates(monkeypatch):
    import wiring
    from geo import pipeline
    monkeypatch.setattr(pipeline, "AFTER_RUN", [])
    monkeypatch.setattr(hooks, "BEFORE_ACTIVATE", [])
    monkeypatch.setattr(hooks, "AFTER_ACTIVATE", [])
    wiring.wire()
    assert aggregates.on_saved in store.AFTER_SAVE
    assert hooks.ON_REGION_CHANGE == [aggregates.on_region_change]
    assert hooks.BEFORE_ACTIVATE == [aggregates.before_activate]
    assert hooks.AFTER_ACTIVATE == [aggregates.after_activate]
    assert pipeline.AFTER_RUN == [aggregates.refresh_dirty]
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_aggregates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'analytics'`

- [ ] **Step 3: 마이그레이션 작성**

`migrations/003_agg.sql`:
```sql
-- 월별 집계: 최신 경계 기준 지역 x 계약월 x 면적 구간. 가격 단위 만원, ㎡당 가격 만원/㎡
CREATE TABLE agg_month (
    boundary_version TEXT NOT NULL,
    level TEXT NOT NULL,            -- nation / sido / sgg / umd
    region_cd TEXT NOT NULL,        -- nation은 '00'
    ym TEXT NOT NULL,
    size_band TEXT NOT NULL,        -- all / le60 / 60_85 / gt85
    n_trades INTEGER NOT NULL,
    median_price DOUBLE PRECISION,
    p25_price DOUBLE PRECISION,
    p75_price DOUBLE PRECISION,
    mean_price DOUBLE PRECISION,
    median_ppm2 DOUBLE PRECISION,
    PRIMARY KEY (boundary_version, level, region_cd, size_band, ym)
);
CREATE INDEX ix_agg_map ON agg_month (boundary_version, level, ym, size_band);

-- 다시 집계할 계약월
CREATE TABLE agg_dirty (ym TEXT PRIMARY KEY, marked_at TIMESTAMP NOT NULL);
```

- [ ] **Step 4: 집계 구현**

`analytics/__init__.py`는 빈 파일. `analytics/aggregates.py`:
```python
"""월별 집계(agg_month): 원본 거래를 최신 경계 기준 전국·시도·시군구·읍면동 x 면적 구간으로 요약한다.

- 계약월 단위로 통째로 다시 계산한다(한 달 전국 거래 수만 건 → 1초 안팎).
- 해제 거래·금액 없는 거래는 빼고, 완전 중복 행은 1건으로 센다.
- 지역은 단지 판정 결과(complexes.region_*)를 쓰고, 판정이 없으면 시군구만 수집 코드(lawd_cd)로 대신한다
  (읍면동 집계에서는 빠진다).
- 수집 저장·지역 변경이 생긴 계약월은 agg_dirty에 표시하고, 지리 처리(10분 주기) 끝에 다시 계산한다.
"""
import settings
from collector import api
from geo import versions

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


def mark_months(conn, yms):
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO agg_dirty (ym, marked_at) VALUES (%s, %s) "
            "ON CONFLICT (ym) DO UPDATE SET marked_at = EXCLUDED.marked_at",
            [(ym, settings.now_ts()) for ym in sorted(set(yms))])


def on_saved(conn, lawd_cd, deal_ymd, rows):
    """store.AFTER_SAVE: 저장한 작업의 계약월을 대기열에."""
    mark_months(conn, [deal_ymd])


def on_region_change(conn, apt_seqs):
    """geo.hooks.ON_REGION_CHANGE: 지역이 바뀌는 단지의 거래가 있는 계약월을 대기열에."""
    conn.execute("""
        INSERT INTO agg_dirty (ym, marked_at)
        SELECT DISTINCT deal_ymd, %s FROM trades WHERE apt_seq = ANY(%s)
        ON CONFLICT (ym) DO UPDATE SET marked_at = EXCLUDED.marked_at""",
                 (settings.now_ts(), list(apt_seqs)))


def refresh_dirty(conn):
    """대기열의 계약월을 활성 버전으로 다시 계산한다. 활성 버전이 없으면 기다린다. → 처리한 달 수"""
    version = versions.active(conn)
    if version is None:
        return 0
    todo = conn.execute("SELECT ym, marked_at FROM agg_dirty ORDER BY ym").fetchall()
    for row in todo:
        with conn.transaction():
            refresh_month(conn, row["ym"], version)
            # 계산 도중 새로 표시된 달은 남겨 둔다
            conn.execute("DELETE FROM agg_dirty WHERE ym = %s AND marked_at <= %s", (row["ym"], row["marked_at"]))
    return len(todo)


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
```

`wiring.py` 전체를 다음으로 바꾼다:
```python
"""모듈 사이 후처리 연결. 앱 시작 때 한 번 부른다.

수집 저장 → 단지 등록·집계 대기열 / 단지 지역 변경 → 집계 대기열 /
경계 전환 → 새 버전 집계·이전 버전 삭제 / 지리 처리 끝 → 대기열 집계
"""
from analytics import aggregates
from collector import store
from geo import complexes, hooks, pipeline


def wire():
    store.AFTER_SAVE = [complexes.register, aggregates.on_saved]
    hooks.ON_REGION_CHANGE = [aggregates.on_region_change]
    hooks.BEFORE_ACTIVATE = [aggregates.before_activate]
    hooks.AFTER_ACTIVATE = [aggregates.after_activate]
    pipeline.AFTER_RUN = [aggregates.refresh_dirty]
```

계획 2의 `tests/test_geo_complexes.py::test_wire_registers_hook`는 그대로 통과한다(`complexes.register in store.AFTER_SAVE`).

주의: `geo/assign.py`, `geo/versions.py`, `geo/complexes.py`, `geo/pipeline.py`는 훅 목록을 `hooks.ON_REGION_CHANGE`처럼 **모듈 속성으로 매번 읽어야** 재바인딩이 반영된다. 계획 2 코드가 `for hook in hooks.ON_REGION_CHANGE:` / `for step in AFTER_RUN:` 형태인지 확인한다. `pipeline.py`의 `for step in AFTER_RUN:`은 모듈 전역을 읽으므로 `pipeline.AFTER_RUN = [...]` 재바인딩이 반영된다.

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체. test_aggregates 10 passed 포함)

- [ ] **Step 6: Commit**

```bash
git add migrations/003_agg.sql analytics/ wiring.py tests/test_aggregates.py
git commit -m "최신 경계 기준 월별 집계와 집계 대기열·경계 전환 훅 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 조회 조건 검증, 조회 함수, 분석 JSON API

**Files:**
- Create: `analytics/params.py`, `analytics/queries.py`, `tests/analytics_fixtures.py`, `tests/test_analytics_api.py`
- Modify: `web/api.py`

**Interfaces:**
- Consumes: Task 1, 계획 1 `codes.months_ago/month_range`
- Produces:
  - `analytics.params.BadParam(ValueError)`, `LEVELS`, `BANDS: list[(value, label)]`, `CODE_LEN`, `ym(value, name) -> str`, `choice(value, options, name, default)`, `region_items(value, max_n=8) -> list[(level, code)]`, `region_item(value) -> (level, code)`, `ym_range(args, default_months=60, max_months=None) -> (from, to)`
  - `analytics.queries.NotReady(Exception)`, `PROVISIONAL_MONTHS=2`, `shift_ym(ym, n)`, `provisional_from()`, `confirmed_ym()`, `active_version(conn)`, `regions(conn, version, level, parent=None)`, `series(conn, version, items, band, ym_from, ym_to)`, `map_values(conn, version, level, parent, band, ym_from, ym_to) -> dict(values, coverage, prev_from, prev_to, parents)`, `summary(conn, version)`, `search_complexes(conn, version, q=None, region=None, limit=50)`, `complex_detail(conn, version, apt_seq)`(없으면 `LookupError`)
  - API: `GET /api/regions`, `GET /api/agg`, `GET /api/map`, `GET /api/summary`, `GET /api/complexes`, `GET /api/complexes/<apt_seq>`
  - 테스트 도우미 `tests.analytics_fixtures.seed_analytics(conn)`

- [ ] **Step 1: 테스트 도우미 작성**

`tests/analytics_fixtures.py`:
```python
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
        for lawd, items in (("11110", jongno), ("11140", junggu)):
            add_job(conn, lawd, ym)
            store.save_job(conn, lawd, ym, items, len(items))
    aggregates.rebuild_all(conn, V, "live")
```

- [ ] **Step 2: 테스트 작성**

`tests/test_analytics_api.py`:
```python
from datetime import datetime

import pytest

import settings
from analytics import params, queries
from collector import store
from tests.analytics_fixtures import seed_analytics


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])


@pytest.fixture
def seeded(pg, monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [])   # client 픽스처의 wire()가 바꾼 것을 되돌림
    with pg.connection() as conn:
        seed_analytics(conn)


def test_params_helpers():
    assert params.ym("2026-01", "from") == "202601"
    with pytest.raises(params.BadParam):
        params.ym("202613", "from")
    assert params.region_items("sgg:11110, umd:11110101") == [("sgg", "11110"), ("umd", "11110101")]
    assert params.region_items("") == []
    for bad in ("sgg:1111", "xx:11", "umd:1111010", "nation:01", "sgg"):
        with pytest.raises(params.BadParam):
            params.region_items(bad)
    with pytest.raises(params.BadParam):
        params.region_items(",".join(f"sgg:{11110 + i}" for i in range(9)))
    assert queries.shift_ym("202601", -1) == "202512" and queries.shift_ym("202612", 13) == "202801"
    assert queries.provisional_from() == "202609" and queries.confirmed_ym() == "202608"


def test_regions_api(client, seeded):
    sido = client.get("/api/regions?level=sido").get_json()
    assert sido["version"] == "2026-10" and [r["region_cd"] for r in sido["regions"]] == ["11"]
    sgg = client.get("/api/regions?level=sgg&parent=11").get_json()
    assert [r["name"] for r in sgg["regions"]] == ["종로구", "중구"]
    assert client.get("/api/regions?level=nation").get_json()["regions"][0]["region_cd"] == "00"


def test_agg_series(client, seeded):
    resp = client.get("/api/agg?regions=sgg:11110,umd:11110101&from=202601&to=202603")
    data = resp.get_json()
    assert resp.headers["Cache-Control"] == "private, max-age=600"
    assert (data["from"], data["to"], data["provisional_from"]) == ("202601", "202603", "202609")
    s0, s1 = data["series"]
    assert (s0["key"], s0["name"]) == ("sgg:11110", "서울특별시 종로구")
    assert s1["name"] == "서울특별시 종로구 청운동"
    assert [p["ym"] for p in s0["points"]] == ["202601", "202602", "202603"]
    assert s0["points"][0]["n_trades"] == 5


def test_agg_default_range(client, seeded):
    data = client.get("/api/agg?regions=nation:00").get_json()
    assert data["to"] == "202610" and data["from"] == "202111"


def test_agg_series_has_gaps(client, seeded):
    data = client.get("/api/agg?regions=sgg:11140&band=le60&from=202601&to=202602").get_json()
    assert data["series"][0]["points"] == []     # 중구 거래는 84㎡뿐 → 60㎡ 이하 집계 행 없음


@pytest.mark.parametrize("qs", [
    "regions=sgg:1111", "regions=sgg:11110&band=xx", "regions=sgg:11110&from=202605&to=202601",
    "regions=" + ",".join(f"sgg:{11110 + i}" for i in range(9)), "regions=sgg:11110&from=2026",
])
def test_agg_rejects_bad_params(client, seeded, qs):
    resp = client.get(f"/api/agg?{qs}")
    assert resp.status_code == 400 and resp.get_json()["error"]


def test_not_ready_without_active_version(client, pg):
    resp = client.get("/api/agg?regions=nation:00")
    assert resp.status_code == 503 and "경계" in resp.get_json()["error"]


def test_map_sgg(client, seeded):
    data = client.get("/api/map?level=sgg&parent=11&from=202607&to=202609").get_json()
    assert (data["prev_from"], data["prev_to"]) == ("202507", "202509")
    by = {v["region_cd"]: v for v in data["values"]}
    assert by["11110"]["n"] == 15 and by["11140"]["n"] == 6
    assert by["11140"]["yoy_price"] == pytest.approx(
        100 * ((40000 + 50 * 31) - (40000 + 50 * 19)) / (40000 + 50 * 19), abs=0.1)
    assert [p["name"] for p in data["parents"]] == ["서울특별시"]
    assert data["coverage"] is None


def test_map_umd_coverage(client, seeded):
    data = client.get("/api/map?level=umd&parent=11140&from=202607&to=202609").get_json()
    assert data["coverage"] == 0.0                    # 중구 단지 C는 좌표 없음
    assert [v["n"] for v in data["values"]] == [0]
    assert [p["region_cd"] for p in data["parents"]] == ["11", "11140"]
    data = client.get("/api/map?level=umd&parent=11110&from=202607&to=202609").get_json()
    assert data["coverage"] == 100.0


def test_map_rejects_bad_level(client, seeded):
    assert client.get("/api/map?level=dong").status_code == 400
    assert client.get("/api/map?level=umd").status_code == 400    # 읍면동은 시군구 지정 필요


def test_summary(client, seeded):
    data = client.get("/api/summary").get_json()
    assert data["kpi"]["ym"] == "202608" and data["kpi"]["n"] == 7
    assert data["series"][0]["ym"] == "202411" and data["series"][-1]["ym"] == "202610"
    assert [m["region_cd"] for m in data["movers"]["up"]] == ["11110"]   # 중구는 3개월 6건 < 10건
    assert data["movers"]["window"] == ["202606", "202608"]


def test_complex_search_and_detail(client, seeded):
    found = client.get("/api/complexes?q=청운").get_json()
    assert [c["apt_seq"] for c in found] == ["A"] and found[0]["region_name"] == "서울특별시 종로구 청운동"
    by_region = client.get("/api/complexes?region=11140").get_json()
    assert [c["apt_seq"] for c in by_region] == ["C"] and by_region[0]["region_name"] == "서울특별시 중구"
    detail = client.get("/api/complexes/A").get_json()
    assert detail["complex"]["apt_nm"] == "청운아파트" and len(detail["trades"]) == 33 * 3
    t = detail["trades"][0]
    assert set(t) == {"deal_date", "deal_amount", "area", "floor", "apt_dong", "dealing_gbn", "is_cancelled", "ppm2"}
    assert client.get("/api/complexes/ZZZ").status_code == 404
    assert client.get("/api/complexes?region=1").status_code == 400
```

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_analytics_api.py -v`
Expected: FAIL — `ImportError: cannot import name 'params' from 'analytics'`

- [ ] **Step 4: 조회 조건 검증 구현**

`analytics/params.py`:
```python
"""조회 조건 검증. 잘못된 값은 BadParam(한국어 메시지) → API가 400으로 돌려준다."""
import re

from collector import codes


class BadParam(ValueError):
    pass


LEVELS = ("nation", "sido", "sgg", "umd")
LEVEL_LABELS = {"nation": "전국", "sido": "시도", "sgg": "시군구", "umd": "읍면동"}
CODE_LEN = {"nation": 2, "sido": 2, "sgg": 5, "umd": 8}
BANDS = [("all", "전체 면적"), ("le60", "60㎡ 이하"), ("60_85", "60~85㎡"), ("gt85", "85㎡ 초과")]
_YM = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])$")


def ym(value, name):
    v = (value or "").replace("-", "").strip()
    if not _YM.match(v):
        raise BadParam(f"{name}은(는) YYYYMM 형식이어야 합니다: {value!r}")
    return v


def choice(value, options, name, default=None):
    if value in (None, ""):
        if default is None:
            raise BadParam(f"{name}을(를) 지정하세요.")
        return default
    if value not in options:
        raise BadParam(f"{name}은(는) {', '.join(options)} 중 하나여야 합니다: {value!r}")
    return value


def region_item(value):
    level, sep, code = (value or "").strip().partition(":")
    if not sep or level not in LEVELS:
        raise BadParam(f"지역은 '수준:코드' 형식이어야 합니다(예: sgg:11110): {value!r}")
    if not (code.isdigit() and len(code) == CODE_LEN[level]) or (level == "nation" and code != "00"):
        raise BadParam(f"{LEVEL_LABELS[level]} 코드는 {CODE_LEN[level]}자리 숫자여야 합니다: {value!r}")
    return level, code


def region_items(value, max_n=8):
    items = [region_item(v) for v in (value or "").split(",") if v.strip()]
    if len(items) > max_n:
        raise BadParam(f"지역은 최대 {max_n}개까지 비교할 수 있습니다.")
    return list(dict.fromkeys(items))


def ym_range(args, default_months=60, max_months=None):
    """from/to(YYYYMM). 없으면 to=이번 달, from=to에서 default_months-1개월 전."""
    to = ym(args.get("to"), "종료월") if args.get("to") else codes.months_ago(0)
    if args.get("from"):
        start = ym(args.get("from"), "시작월")
    else:
        from analytics.queries import shift_ym
        start = shift_ym(to, -(default_months - 1))
    if start > to:
        raise BadParam("시작월이 종료월보다 늦습니다.")
    if max_months and len(codes.month_range(start, to)) > max_months:
        raise BadParam(f"기간은 최대 {max_months}개월까지 지정할 수 있습니다.")
    return start, to
```

- [ ] **Step 5: 조회 함수 구현**

`analytics/queries.py`:
```python
"""집계·단지 조회(화면·API용). 모두 활성 경계 버전 기준."""
from collector import codes

PROVISIONAL_MONTHS = 2   # 이번 달·지난달은 신고기한(30일) 때문에 잠정
METRICS = ("n_trades", "median_price", "p25_price", "p75_price", "mean_price", "median_ppm2")
MOVER_MIN_TRADES = 10


class NotReady(Exception):
    pass


def shift_ym(ym, months):
    y, m = int(ym[:4]), int(ym[4:]) + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}{m:02d}"


def provisional_from():
    return codes.months_ago(PROVISIONAL_MONTHS - 1)


def confirmed_ym():
    return codes.months_ago(PROVISIONAL_MONTHS)


def _pct(cur, prev):
    return round(100 * (cur - prev) / prev, 1) if cur is not None and prev else None


def active_version(conn):
    row = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchone()
    if not row:
        raise NotReady("경계 데이터가 아직 준비되지 않았습니다. 수집 현황에서 활성 경계를 확인하세요.")
    return row["version"]


def regions(conn, version, level, parent=None):
    if level == "nation":
        return [dict(region_cd="00", level="nation", name="전국", full_name="전국", parent_cd=None)]
    sql = ("SELECT region_cd, level, name, full_name, parent_cd FROM regions "
           "WHERE boundary_version = %s AND level = %s")
    args = [version, level]
    if parent:
        sql += " AND parent_cd = %s"
        args.append(parent)
    return conn.execute(sql + " ORDER BY region_cd", args).fetchall()


def _names(conn, version, items):
    out = {("nation", "00"): "전국"}
    for r in conn.execute("SELECT level, region_cd, full_name FROM regions "
                          "WHERE boundary_version = %s AND region_cd = ANY(%s)",
                          (version, [c for _, c in items])):
        out[(r["level"], r["region_cd"])] = r["full_name"]
    return out


def series(conn, version, items, band, ym_from, ym_to):
    """지역별 월 집계. 거래가 없는 달은 행이 없다(화면에서 빈 값으로 그린다)."""
    rows = conn.execute("""
        SELECT level, region_cd, ym, n_trades, median_price, p25_price, p75_price, mean_price, median_ppm2
          FROM agg_month
         WHERE boundary_version = %s AND size_band = %s AND ym BETWEEN %s AND %s
           AND (level, region_cd) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
         ORDER BY level, region_cd, ym""",
                        (version, band, ym_from, ym_to, [l for l, _ in items], [c for _, c in items])).fetchall()
    names = _names(conn, version, items)
    points = {key: [] for key in items}
    for r in rows:
        points[(r["level"], r["region_cd"])].append({k: r[k] for k in ("ym", *METRICS)})
    return [dict(key=f"{l}:{c}", level=l, code=c, name=names.get((l, c), c), points=points[(l, c)])
            for l, c in items]


def _window(conn, version, level, band, ym_from, ym_to, parent=None, only=None):
    """기간 합계: 거래량 합, 월별 중위가의 거래량 가중평균."""
    sql = """
        SELECT a.region_cd, SUM(a.n_trades)::int AS n,
               SUM(a.median_price * a.n_trades) / NULLIF(SUM(a.n_trades), 0) AS price,
               SUM(a.median_ppm2 * a.n_trades)
                 / NULLIF(SUM(a.n_trades) FILTER (WHERE a.median_ppm2 IS NOT NULL), 0) AS ppm2
          FROM agg_month a
          {join}
         WHERE a.boundary_version = %s AND a.level = %s AND a.size_band = %s AND a.ym BETWEEN %s AND %s
           {only}
         GROUP BY a.region_cd"""
    args = [version, level, band, ym_from, ym_to]
    join = only_sql = ""
    if parent:
        join = ("JOIN regions r ON r.boundary_version = a.boundary_version AND r.region_cd = a.region_cd "
                "AND r.parent_cd = %s")
        args.insert(0, parent)
    if only:
        only_sql = "AND a.region_cd = ANY(%s)"
        args.append(list(only))
    rows = conn.execute(sql.format(join=join, only=only_sql), args).fetchall()
    return {r["region_cd"]: r for r in rows}


def _parents(conn, version, level, parent):
    if level == "sgg":
        chain = [parent]
    elif level == "umd":
        chain = [parent[:2], parent]
    else:
        return []
    rows = {r["region_cd"]: r for r in conn.execute(
        "SELECT region_cd, level, name FROM regions WHERE boundary_version = %s AND region_cd = ANY(%s)",
        (version, chain))}
    return [dict(region_cd=c, level=rows[c]["level"], name=rows[c]["name"]) for c in chain if c in rows]


def map_values(conn, version, level, parent, band, ym_from, ym_to):
    prev_from, prev_to = shift_ym(ym_from, -12), shift_ym(ym_to, -12)
    scope = parent if level in ("sgg", "umd") else None
    cur = _window(conn, version, level, band, ym_from, ym_to, parent=scope)
    prev = _window(conn, version, level, band, prev_from, prev_to, parent=scope)
    values = []
    for r in regions(conn, version, level, scope):
        c, p = cur.get(r["region_cd"]), prev.get(r["region_cd"])
        values.append(dict(
            region_cd=r["region_cd"], name=r["name"], full_name=r["full_name"],
            n=c["n"] if c else 0, median_price=c["price"] if c else None, median_ppm2=c["ppm2"] if c else None,
            yoy_price=_pct(c["price"] if c else None, p["price"] if p else None),
            yoy_n=_pct(c["n"] if c else None, p["n"] if p else None)))
    coverage = None
    if level == "umd":
        sgg = _window(conn, version, "sgg", band, ym_from, ym_to, only=[parent]).get(parent)
        total = sgg["n"] if sgg else 0
        coverage = round(100 * sum(v["n"] for v in values) / total, 1) if total else None
    return dict(values=values, coverage=coverage, prev_from=prev_from, prev_to=prev_to,
                parents=_parents(conn, version, level, parent))


def summary(conn, version):
    conf, prov, now = confirmed_ym(), provisional_from(), codes.months_ago(0)
    first = shift_ym(conf, -21)
    nat = {r["ym"]: r for r in conn.execute("""
        SELECT ym, n_trades, median_price FROM agg_month
         WHERE boundary_version = %s AND level = 'nation' AND size_band = 'all' AND ym BETWEEN %s AND %s""",
                                            (version, shift_ym(first, -12), now))}
    cur, prev = nat.get(conf), nat.get(shift_ym(conf, -12))
    kpi = dict(ym=conf, n=cur["n_trades"] if cur else 0, median=cur["median_price"] if cur else None,
               yoy_n=_pct(cur["n_trades"] if cur else None, prev["n_trades"] if prev else None),
               yoy_median=_pct(cur["median_price"] if cur else None, prev["median_price"] if prev else None))
    series_ = [dict(ym=ym, n=nat[ym]["n_trades"] if ym in nat else None,
                    median=nat[ym]["median_price"] if ym in nat else None)
               for ym in codes.month_range(first, now)]
    w_from, w_to = shift_ym(conf, -2), conf
    a = _window(conn, version, "sgg", "all", w_from, w_to)
    b = _window(conn, version, "sgg", "all", shift_ym(w_from, -12), shift_ym(w_to, -12))
    names = {r["region_cd"]: r["full_name"] for r in regions(conn, version, "sgg")}
    movers = []
    for code, r in a.items():
        p = b.get(code)
        if p and r["n"] >= MOVER_MIN_TRADES and p["n"] >= MOVER_MIN_TRADES and p["price"]:
            movers.append(dict(region_cd=code, name=names.get(code, code), n=r["n"], median=r["price"],
                               yoy=_pct(r["price"], p["price"])))
    movers.sort(key=lambda m: m["yoy"], reverse=True)
    up = [m for m in movers if m["yoy"] >= 0][:10]
    down = [m for m in reversed(movers) if m["yoy"] < 0][:10]
    return dict(version=version, confirmed_ym=conf, provisional_from=prov, kpi=kpi, series=series_,
                movers=dict(up=up, down=down, window=[w_from, w_to], min_trades=MOVER_MIN_TRADES))


def _region_filter(region):
    if not region:
        return "", []
    if not region.isdigit() or len(region) not in (2, 5, 8):
        from analytics.params import BadParam
        raise BadParam("지역 코드는 시도 2자리·시군구 5자리·읍면동 8자리 숫자여야 합니다.")
    if len(region) == 8:
        return " AND c.region_umd_cd = %s", [region]
    if len(region) == 5:
        return " AND COALESCE(c.region_sgg_cd, c.api_sgg_cd) = %s", [region]
    return " AND left(COALESCE(c.region_sgg_cd, c.api_sgg_cd), 2) = %s", [region]


_COMPLEX_SELECT = """
    SELECT c.apt_seq, c.apt_nm, c.build_year, c.last_deal_date, c.api_umd_nm, c.jibun, c.road_nm,
           c.lon, c.lat, c.geocode_status, c.region_match, c.sgg_mismatch,
           COALESCE(ru.full_name, rs.full_name) AS region_name
      FROM complexes c
      LEFT JOIN regions ru ON ru.boundary_version = %s AND ru.region_cd = c.region_umd_cd
      LEFT JOIN regions rs ON rs.boundary_version = %s AND rs.region_cd = COALESCE(c.region_sgg_cd, c.api_sgg_cd)
"""


def search_complexes(conn, version, q=None, region=None, limit=50):
    where, args = _region_filter(region)
    if q:
        where += " AND c.apt_nm ILIKE %s"
        args.append(f"%{q}%")
    return conn.execute(_COMPLEX_SELECT + " WHERE TRUE" + where +
                        " ORDER BY c.last_deal_date DESC NULLS LAST, c.apt_nm LIMIT %s",
                        [version, version, *args, limit]).fetchall()


def complex_detail(conn, version, apt_seq):
    row = conn.execute(_COMPLEX_SELECT + " WHERE c.apt_seq = %s", (version, version, apt_seq)).fetchone()
    if not row:
        raise LookupError(apt_seq)
    trades = conn.execute("""
        SELECT deal_date, deal_amount, exclu_use_ar::float8 AS area, floor, apt_dong, dealing_gbn,
               is_cancelled, price_per_m2::float8 AS ppm2
          FROM trades WHERE apt_seq = %s
         ORDER BY deal_date DESC NULLS LAST, id DESC LIMIT 2000""", (apt_seq,)).fetchall()
    return dict(complex=row, trades=trades)
```

- [ ] **Step 6: API 구현**

`web/api.py`의 import 블록을 다음으로 바꾸고:
```python
"""JSON API. 거래 필드는 API 필드명(기존 형식), 분석 API는 snake_case."""
from flask import Blueprint, jsonify, request

import db
from analytics import params, queries
from analytics.params import BadParam
from collector import jobs, quality
from geo import complexes
from web.common import api_row, filters
```
파일 끝에 덧붙인다:
```python
CACHED = {"api.regions_", "api.agg", "api.map_", "api.summary"}


@bp.errorhandler(BadParam)
def bad_param(e):
    return jsonify(error=str(e)), 400


@bp.errorhandler(queries.NotReady)
def not_ready(e):
    return jsonify(error=str(e)), 503


@bp.after_request
def cache_headers(resp):
    if request.endpoint in CACHED and resp.status_code == 200:
        resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


@bp.route("/regions")
def regions_():
    level = params.choice(request.args.get("level"), params.LEVELS, "수준")
    with db.connection() as conn:
        version = queries.active_version(conn)
        rows = queries.regions(conn, version, level, request.args.get("parent") or None)
    return jsonify(version=version, regions=rows)


@bp.route("/agg")
def agg():
    items = params.region_items(request.args.get("regions"))
    if not items:
        raise BadParam("regions에 지역을 1개 이상 지정하세요(예: sgg:11110).")
    band = params.choice(request.args.get("band"), [b for b, _ in params.BANDS], "면적 구간", "all")
    ym_from, ym_to = params.ym_range(request.args)
    with db.connection() as conn:
        version = queries.active_version(conn)
        data = queries.series(conn, version, items, band, ym_from, ym_to)
    return jsonify(version=version, band=band, provisional_from=queries.provisional_from(),
                   series=data, **{"from": ym_from, "to": ym_to})


@bp.route("/map")
def map_():
    level = params.choice(request.args.get("level"), ("sido", "sgg", "umd"), "수준", "sido")
    parent = request.args.get("parent") or None
    if level == "sgg" and not (parent and parent.isdigit() and len(parent) == 2):
        raise BadParam("시군구 지도는 시도 코드(parent, 2자리)가 필요합니다.")
    if level == "umd" and not (parent and parent.isdigit() and len(parent) == 5):
        raise BadParam("읍면동 지도는 시군구 코드(parent, 5자리)가 필요합니다.")
    band = params.choice(request.args.get("band"), [b for b, _ in params.BANDS], "면적 구간", "all")
    if request.args.get("from") or request.args.get("to"):
        ym_from, ym_to = params.ym_range(request.args)
    else:
        ym_to = queries.confirmed_ym()
        ym_from = queries.shift_ym(ym_to, -2)
    with db.connection() as conn:
        version = queries.active_version(conn)
        data = queries.map_values(conn, version, level, parent if level != "sido" else None,
                                  band, ym_from, ym_to)
    return jsonify(version=version, level=level, parent=parent if level != "sido" else None, band=band,
                   **{"from": ym_from, "to": ym_to}, **data)


@bp.route("/summary")
def summary():
    with db.connection() as conn:
        return jsonify(queries.summary(conn, queries.active_version(conn)))


@bp.route("/complexes")
def complexes_search():
    with db.connection() as conn:
        version = queries.active_version(conn)
        rows = queries.search_complexes(conn, version, request.args.get("q") or None,
                                        request.args.get("region") or None)
    return jsonify(rows)


@bp.route("/complexes/<apt_seq>")
def complex_detail(apt_seq):
    try:
        with db.connection() as conn:
            return jsonify(queries.complex_detail(conn, queries.active_version(conn), apt_seq))
    except LookupError:
        return jsonify(error="없는 단지입니다."), 404
```

`params.ym_range`는 `to`가 없을 때 `codes.months_ago(0)`(이번 달)를 쓴다. `test_agg_default_range`의 기대값(to=202610, from=202111)과 맞는다.

- [ ] **Step 7: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체. test_analytics_api 16 passed 포함)

- [ ] **Step 8: Commit**

```bash
git add analytics/params.py analytics/queries.py web/api.py tests/analytics_fixtures.py tests/test_analytics_api.py
git commit -m "추이·지도·요약·단지 조회 API와 조회 조건 검증 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 데이터 추출(CSV·Parquet)과 코드북

**Files:**
- Create: `analytics/export.py`, `web/export.py`, `tests/test_export.py`
- Modify: `app.py`, `web/pages.py`, `requirements.txt`, `tests/test_web.py`

**Interfaces:**
- Consumes: Task 2 `params`, `queries.active_version`, 계획 1 `web.common.CODES`
- Produces:
  - `analytics.export.RAW_COLUMNS: list[(sql_expr, out_name, arrow_type)]`, `AGG_COLUMNS`, `CODEBOOK: list[(name, label, description)]`, `MAX_RAW_MONTHS = 60`
  - `export.parse_args(args) -> dict(target, region: (level, code) | None, ym_from, ym_to, band, include_cancelled)`(옛 인자 `sido`(이름)·`lawd_cd`·`year`·`ymd`도 받음)
  - `export.raw_query(p) -> (sql, args)`, `export.agg_query(version, p) -> (sql, args)`
  - `export.csv_stream(sql, args, columns) -> Iterator[bytes]`, `export.parquet_stream(sql, args, columns) -> Iterator[bytes]`, `export.codebook_csv() -> bytes`
  - 라우트: `GET /export.csv`, `GET /export.parquet`, `GET /export/codebook.csv`(블루프린트 `export`), `/download.csv` → 301 `/export.csv?...&target=raw`

- [ ] **Step 1: 의존성 추가**

`requirements.txt` 끝에 `pyarrow>=21`을 추가하고(Python 3.14 휠이 있는 버전이 설치된다) `.venv/Scripts/python -m pip install -r requirements-dev.txt`.

- [ ] **Step 2: 테스트 작성**

`tests/test_export.py`:
```python
import csv
import io
from datetime import date, datetime

import pyarrow.parquet as pq
import pytest

import settings
from analytics import export
from collector import api, store
from tests.analytics_fixtures import seed_analytics


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])


@pytest.fixture
def seeded(pg, monkeypatch):
    from analytics import aggregates
    from tests.analytics_fixtures import V
    monkeypatch.setattr(store, "AFTER_SAVE", [])   # client 픽스처의 wire()가 바꾼 것을 되돌림
    with pg.connection() as conn:
        seed_analytics(conn)
        conn.execute("UPDATE trades SET cdeal_type = 'O' WHERE apt_seq = 'B' AND deal_ymd = '202601' "
                     "AND deal_day = '4'")
        aggregates.refresh_month(conn, "202601", V)


def read_csv(resp):
    body = resp.get_data()
    assert body.startswith("﻿".encode())
    return list(csv.DictReader(io.StringIO(body.decode("utf-8-sig"))))


def test_raw_csv(client, seeded):
    resp = client.get("/export.csv?target=raw&region=sgg:11110&from=202601&to=202602")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    rows = read_csv(resp)
    assert len(rows) == 9                                # 5건 x 2개월 - 해제 1건
    assert list(rows[0])[:4] == ["lawd_cd", "deal_ymd", "sggCd", "umdCd"]
    assert {"aptSeq", "dealAmount", "dealDate", "region_sgg_cd", "region_umd_cd", "is_cancelled"} <= set(rows[0])
    assert rows[0]["dealDate"].startswith("2026-0")


def test_raw_csv_include_cancelled(client, seeded):
    rows = read_csv(client.get("/export.csv?target=raw&region=sgg:11110&from=202601&to=202602&cancelled=1"))
    assert len(rows) == 10 and sum(r["is_cancelled"] == "1" for r in rows) == 1


def test_raw_region_umd_uses_complex_region(client, seeded):
    rows = read_csv(client.get("/export.csv?target=raw&region=umd:11110102&from=202601&to=202601"))
    assert {r["aptSeq"] for r in rows} == {"B"}


def test_raw_parquet_types(client, seeded):
    resp = client.get("/export.parquet?target=raw&region=sgg:11140&from=202601&to=202603")
    table = pq.read_table(io.BytesIO(resp.get_data()))
    assert table.num_rows == 6
    schema = table.schema
    assert str(schema.field("dealAmount").type) == "int32"
    assert str(schema.field("excluUseAr").type) == "double"
    assert str(schema.field("dealDate").type) == "date32[day]"
    assert table.column("dealDate")[0].as_py() == date(2026, 1, 6)


def test_agg_csv_and_parquet(client, seeded):
    rows = read_csv(client.get("/export.csv?target=agg&region=sgg:11110&from=202601&to=202603&band=all"))
    assert {r["size_band"] for r in rows} == {"all", "le60", "gt85"}   # 지역을 고르면 면적 구간 전부
    rows = [r for r in rows if r["size_band"] == "all"]
    assert [r["ym"] for r in rows] == ["202601", "202602", "202603"]
    assert rows[0]["region_name"] == "서울특별시 종로구" and rows[0]["n_trades"] == "4"
    table = pq.read_table(io.BytesIO(client.get(
        "/export.parquet?target=agg&region=nation:00&from=202601&to=202603").get_data()))
    assert table.num_rows == 3 * 4                       # 3개월 x 면적 구간 4개(전체 포함)
    assert str(table.schema.field("n_trades").type) == "int32"


def test_export_raw_limit(client, seeded):
    resp = client.get("/export.csv?target=raw&from=202001&to=202501")
    assert resp.status_code == 400 and "60개월" in resp.get_json()["error"]


def test_export_bad_region(client, seeded):
    assert client.get("/export.csv?target=raw&region=sgg:1&from=202601&to=202601").status_code == 400


def test_legacy_download_redirects(client, seeded):
    resp = client.get("/download.csv?lawd_cd=11110&year=2026")
    assert resp.status_code == 301
    assert resp.headers["Location"].startswith("/export.csv?")
    rows = read_csv(client.get(resp.headers["Location"]))
    assert len(rows) == 9 * 5 - 1                        # 2026-01~09, 해제 1건 제외


def test_legacy_sido_name(client, seeded):
    p = export.parse_args({"sido": "서울특별시", "ymd": "202601", "target": "raw"})
    assert p["region"] == ("sido", "11") and (p["ym_from"], p["ym_to"]) == ("202601", "202601")


def test_codebook_covers_raw_columns(client):
    rows = list(csv.DictReader(io.StringIO(client.get("/export/codebook.csv").get_data().decode("utf-8-sig"))))
    names = {r["열 이름"] for r in rows}
    assert {c[1] for c in export.RAW_COLUMNS} <= names
    assert {c[1] for c in export.AGG_COLUMNS} <= names
    assert set(api.FIELDS) <= names
```

`tests/test_web.py`에서 계획 1의 `test_download_requires_filter`, `test_download_csv` 두 테스트를 지운다(옛 `/download.csv`는 이제 `/export.csv`로 넘긴다. 위 `test_legacy_download_redirects`가 대신 확인).

- [ ] **Step 3: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_export.py -v`
Expected: FAIL — `ImportError: cannot import name 'export' from 'analytics'`

- [ ] **Step 4: 추출 모듈 구현**

`analytics/export.py`:
```python
"""데이터 추출: 원본 거래·월별 집계를 CSV(utf-8-sig)·Parquet로 흘려보낸다(서버 측 커서, 메모리 일정)."""
import csv
import io

import pyarrow as pa
import pyarrow.parquet as pq

import db
from analytics import params, queries
from collector import api, codes

MAX_RAW_MONTHS = 60
BATCH = 5000

_TYPED = {"deal_amount": pa.int32(), "exclu_use_ar": pa.float64(), "floor": pa.int16(),
          "build_year": pa.int16()}
# (SQL 식, 내보낼 이름, Arrow 형)
RAW_COLUMNS = (
    [("t.lawd_cd", "lawd_cd", pa.string()), ("t.deal_ymd", "deal_ymd", pa.string())]
    + [(f"t.{c}::float8" if c == "exclu_use_ar" else f"t.{c}", api.CAMEL[c], _TYPED.get(c, pa.string()))
       for c in api.COLUMNS]
    + [("t.deal_date", "dealDate", pa.date32()), ("t.collected_at", "collected_at", pa.timestamp("s")),
       ("c.region_sgg_cd", "region_sgg_cd", pa.string()), ("c.region_umd_cd", "region_umd_cd", pa.string()),
       ("t.is_cancelled", "is_cancelled", pa.bool_())]
)
AGG_COLUMNS = [
    ("a.boundary_version", "boundary_version", pa.string()), ("a.level", "level", pa.string()),
    ("a.region_cd", "region_cd", pa.string()), ("COALESCE(r.full_name, '전국')", "region_name", pa.string()),
    ("a.ym", "ym", pa.string()), ("a.size_band", "size_band", pa.string()),
    ("a.n_trades", "n_trades", pa.int32()), ("a.median_price", "median_price", pa.float64()),
    ("a.p25_price", "p25_price", pa.float64()), ("a.p75_price", "p75_price", pa.float64()),
    ("a.mean_price", "mean_price", pa.float64()), ("a.median_ppm2", "median_ppm2", pa.float64()),
]

_FIELD_DOCS = {
    "sggCd": ("법정동 시군구코드", "거래 신고 기준 시군구 코드(5자리)"),
    "umdCd": ("법정동 읍면동코드", "시군구 안의 법정동 코드(5자리, 리 포함)"),
    "landCd": ("지번 구분", "대지·산 등 지번 구분 코드"),
    "bonbun": ("지번 본번", ""), "bubun": ("지번 부번", ""),
    "roadNm": ("도로명", ""), "roadNmSggCd": ("도로명 시군구코드", ""),
    "roadNmCd": ("도로명코드", "도로명 7자리. 시군구코드와 합쳐 위치정보요약DB와 매칭"),
    "roadNmSeq": ("도로명 일련번호", ""), "roadNmbCd": ("지상·지하 구분", "0 지상, 1 지하"),
    "roadNmBonbun": ("건물 본번", ""), "roadNmBubun": ("건물 부번", ""),
    "umdNm": ("법정동명", ""), "aptNm": ("단지명", ""), "jibun": ("지번", ""),
    "excluUseAr": ("전용면적", "㎡"), "dealYear": ("계약 연도", ""), "dealMonth": ("계약 월", ""),
    "dealDay": ("계약 일", ""), "dealAmount": ("거래금액", "만원"), "floor": ("층", "음수는 지하"),
    "buildYear": ("건축년도", ""), "aptSeq": ("단지 일련번호", "단지 식별자"),
    "cdealType": ("해제 여부", "O면 계약 해제 신고된 거래"), "cdealDay": ("해제사유 발생일", ""),
    "dealingGbn": ("거래 유형", "중개거래·직거래"), "estateAgentSggNm": ("중개사 소재지", ""),
    "rgstDate": ("등기일자", ""), "aptDong": ("동", ""), "slerGbn": ("매도자 구분", ""),
    "buyerGbn": ("매수자 구분", ""), "landLeaseholdGbn": ("토지임대부 여부", ""),
}
_EXTRA_DOCS = {
    "lawd_cd": ("수집 시군구코드", "API에 요청한 시군구 코드"), "deal_ymd": ("계약년월", "YYYYMM"),
    "dealDate": ("계약일", "YYYY-MM-DD"), "collected_at": ("수집시각", "KST"),
    "region_sgg_cd": ("최신 경계 시군구", "단지 좌표로 판정한 시군구. 좌표가 없으면 빈 값"),
    "region_umd_cd": ("최신 경계 읍면동", "단지 좌표로 판정한 읍면동 8자리. 좌표가 없으면 빈 값"),
    "is_cancelled": ("해제 거래", "1 해제, 0 정상"),
    "boundary_version": ("경계 버전", "집계에 쓴 경계 버전"),
    "level": ("지역 수준", "nation 전국 / sido 시도 / sgg 시군구 / umd 읍면동"),
    "region_cd": ("지역 코드", "전국 00, 시도 2자리, 시군구 5자리, 읍면동 8자리"),
    "region_name": ("지역 이름", ""), "ym": ("계약년월", "YYYYMM"),
    "size_band": ("면적 구간", "all 전체 / le60 60㎡ 이하 / 60_85 60~85㎡ / gt85 85㎡ 초과"),
    "n_trades": ("거래 수", "해제·금액 없음 제외, 완전 중복 1건"),
    "median_price": ("중위 거래가", "만원"), "p25_price": ("25% 거래가", "만원"),
    "p75_price": ("75% 거래가", "만원"), "mean_price": ("평균 거래가", "만원"),
    "median_ppm2": ("㎡당 중위가", "만원/㎡"),
}
CODEBOOK = [(name, *_FIELD_DOCS[name]) for name in api.FIELDS] + [(n, *d) for n, d in _EXTRA_DOCS.items()]


def parse_args(args):
    target = params.choice(args.get("target"), ("raw", "agg"), "대상", "raw")
    region = params.region_item(args["region"]) if args.get("region") else None
    # 옛 /download.csv 인자
    if not region and args.get("lawd_cd"):
        region = params.region_item(f"sgg:{args['lawd_cd']}")
    if not region and args.get("sido"):
        codes_df = codes.load_codes()
        match = codes_df.loc[codes_df["시도"] == args["sido"], "LAWD_CD"]
        if match.empty:
            raise params.BadParam(f"없는 시도입니다: {args['sido']!r}")
        region = ("sido", match.iloc[0][:2])
    rng = dict(args)
    if args.get("ymd") and not args.get("from"):
        rng["from"] = rng["to"] = args["ymd"]
    elif args.get("year") and not args.get("from"):
        rng["from"], rng["to"] = f"{args['year']}01", f"{args['year']}12"
    ym_from, ym_to = params.ym_range(rng, default_months=12,
                                     max_months=MAX_RAW_MONTHS if target == "raw" else None)
    band = params.choice(args.get("band"), [b for b, _ in params.BANDS], "면적 구간", "all")
    return dict(target=target, region=region, ym_from=ym_from, ym_to=ym_to, band=band,
                include_cancelled=args.get("cancelled") in ("1", "true", "on"))


def raw_query(p):
    where = ["t.deal_ymd BETWEEN %s AND %s"]
    args = [p["ym_from"], p["ym_to"]]
    if not p["include_cancelled"]:
        where.append("NOT t.is_cancelled")
    if p["region"]:
        level, code = p["region"]
        expr = {"sido": "left(COALESCE(c.region_sgg_cd, t.lawd_cd), 2)",
                "sgg": "COALESCE(c.region_sgg_cd, t.lawd_cd)", "umd": "c.region_umd_cd"}.get(level)
        if expr:
            where.append(f"{expr} = %s")
            args.append(code)
    if p["band"] != "all":
        where.append({"le60": "t.exclu_use_ar <= 60", "60_85": "t.exclu_use_ar > 60 AND t.exclu_use_ar <= 85",
                      "gt85": "t.exclu_use_ar > 85"}[p["band"]])
    cols = ", ".join(f"{expr} AS \"{name}\"" for expr, name, _ in RAW_COLUMNS)
    sql = (f"SELECT {cols} FROM trades t LEFT JOIN complexes c ON c.apt_seq = btrim(t.apt_seq) "
           f"WHERE {' AND '.join(where)} ORDER BY t.deal_date, t.lawd_cd, t.id")
    return sql, args


def agg_query(version, p):
    where = ["a.boundary_version = %s", "a.ym BETWEEN %s AND %s"]
    args = [version, p["ym_from"], p["ym_to"]]
    if p["region"]:
        where += ["a.level = %s", "a.region_cd = %s"]
        args += list(p["region"])
    if p["band"] != "all" or not p["region"]:
        where.append("a.size_band = %s")
        args.append(p["band"])
    cols = ", ".join(f"{expr} AS \"{name}\"" for expr, name, _ in AGG_COLUMNS)
    sql = (f"SELECT {cols} FROM agg_month a LEFT JOIN regions r ON r.boundary_version = a.boundary_version "
           f"AND r.region_cd = a.region_cd WHERE {' AND '.join(where)} ORDER BY a.level, a.region_cd, a.ym, a.size_band")
    return sql, args


def _rows(sql, args):
    with db.connection() as conn, conn.transaction(), conn.cursor(name="export") as cur:
        cur.execute(sql, args)
        while batch := cur.fetchmany(BATCH):
            yield batch


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    return v


def csv_stream(sql, args, columns):
    names = [name for _, name, _ in columns]
    buf = io.StringIO()
    writer = csv.writer(buf)
    yield "﻿".encode("utf-8")
    writer.writerow(names)
    for batch in _rows(sql, args):
        for row in batch:
            writer.writerow([_cell(row[n]) for n in names])
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate()
    if buf.tell():
        yield buf.getvalue().encode("utf-8")


class _Sink(io.RawIOBase):
    """ParquetWriter가 쓰는 바이트를 모아 두었다가 조금씩 내보낸다."""

    def __init__(self):
        self.chunks, self.pos = [], 0

    def writable(self):
        return True

    def write(self, b):
        self.chunks.append(bytes(b))
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def drain(self):
        out = b"".join(self.chunks)
        self.chunks.clear()
        return out


def parquet_stream(sql, args, columns):
    schema = pa.schema([(name, typ) for _, name, typ in columns])
    sink = _Sink()
    writer = pq.ParquetWriter(sink, schema, compression="zstd")
    for batch in _rows(sql, args):
        writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        yield sink.drain()
    writer.close()
    yield sink.drain()


def codebook_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["열 이름", "설명", "단위·값"])
    writer.writerows(CODEBOOK)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def plan(args):
    """요청 인자 → (sql, args, columns, 파일 이름 앞부분)."""
    p = parse_args(args)
    region = f"_{p['region'][1]}" if p["region"] else ""
    stem = f"apt_{p['target']}{region}_{p['ym_from']}_{p['ym_to']}"
    if p["target"] == "raw":
        sql, qargs = raw_query(p)
        return sql, qargs, RAW_COLUMNS, stem
    with db.connection() as conn:
        version = queries.active_version(conn)
    sql, qargs = agg_query(version, p)
    return sql, qargs, AGG_COLUMNS, stem
```

`agg_query`는 지역을 고르지 않으면 면적 구간 조건을 항상 붙이고(전 지역 x 4구간은 너무 큼), 지역을 고르고 `band=all`이면 그 지역의 4구간을 모두 내보낸다(종로구는 59.9㎡·114.8㎡뿐이라 60_85 행은 없다).

`web/export.py`:
```python
"""데이터 추출 라우트."""
from flask import Blueprint, Response, jsonify, request

from analytics import export, queries
from analytics.params import BadParam

bp = Blueprint("export", __name__)


@bp.errorhandler(BadParam)
def bad_param(e):
    return jsonify(error=str(e)), 400


@bp.errorhandler(queries.NotReady)
def not_ready(e):
    return jsonify(error=str(e)), 503


def _download(fmt):
    sql, args, columns, stem = export.plan(request.args)
    if fmt == "csv":
        body, mimetype = export.csv_stream(sql, args, columns), "text/csv"
    else:
        body, mimetype = export.parquet_stream(sql, args, columns), "application/vnd.apache.parquet"
    return Response(body, mimetype=mimetype,
                    headers={"Content-Disposition": f"attachment; filename={stem}.{fmt}"})


@bp.route("/export.csv")
def export_csv():
    return _download("csv")


@bp.route("/export.parquet")
def export_parquet():
    return _download("parquet")


@bp.route("/export/codebook.csv")
def codebook():
    return Response(export.codebook_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=apt_codebook.csv"})
```

`app.py`의 `create_app()`에서 블루프린트 등록에 추가(`from web import api, auth, export, pages`):
```python
    flask_app.register_blueprint(export.bp)
```

`web/pages.py`의 `download()` 함수 전체를 다음으로 바꾸고, 더 이상 쓰지 않는 `import csv`, `import io`, `Response`, `LABELS`를 import에서 지운다(`redirect`, `url_for` 추가):
```python
@bp.route("/download.csv")
def download():
    """옛 주소: 같은 조건으로 /export.csv(원본)로 보낸다."""
    return redirect(url_for("export.export_csv", **request.args.to_dict(), target="raw"), code=301)
```

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체. test_export 10 passed 포함)

- [ ] **Step 6: Commit**

```bash
git add analytics/export.py web/export.py web/pages.py app.py requirements.txt tests/test_export.py tests/test_web.py
git commit -m "원본·집계 CSV/Parquet 스트리밍 추출과 코드북 추가, 옛 다운로드 주소 연결

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 화면 골격, 공통 차트 도우미, 대시보드, 개발용 데이터

**Files:**
- Create: `templates/_charts.html`, `templates/dashboard.html`, `static/js/common.js`, `static/js/dashboard.js`, `scripts/seed_dev.py`, `tests/test_pages.py`
- Modify: `templates/base.html`, `web/pages.py`, `tests/test_web.py`, `.gitignore`
- Rename: `templates/index.html` → `templates/trades.html`

**Interfaces:**
- Consumes: Task 2 `/api/summary`
- Produces:
  - 라우트 `pages.dashboard`(`/`), `pages.trades`(`/trades`, 옛 거래 목록)
  - CSS 토큰: `--series-1`~`--series-8`, `--seq-100`~`--seq-700`(7단계: 100, 200, 300, 400, 500, 600, 700), `--div-neg-2`, `--div-neg-1`, `--div-mid`, `--div-pos-1`, `--div-pos-2`, `--grid`, `--axis`, `--provisional`; 클래스 `.chart`, `.chart.small`, `.cols`, `.chips`, `.chip`
  - `static/js/common.js`의 전역 `App`: `css(name)`, `api(path, params)`, `fmt.{int, eok, ppm2, pct, ym}`, `shiftYm(ym, n)`, `monthRange(from, to)`, `toMonthInput(ym)`, `fromMonthInput(v)`, `readState(defaults)`, `writeState(state)`, `seriesColor(slot)`, `chart(el)`, `baseOption()`, `provisionalArea(fromYm, toYm, rangeFrom)`, `escapeHtml(s)`, `message(el, text, cls)`
  - `templates/_charts.html`: ECharts(cdnjs)와 `common.js`를 불러오는 조각
  - `scripts/seed_dev.py` → 경계 버전 `0000-dev`(저장소에서 제외)

- [ ] **Step 1: 테스트 작성**

`tests/test_pages.py`:
```python
from datetime import datetime

import pytest

import settings
from collector import store
from tests.analytics_fixtures import seed_analytics


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])


@pytest.fixture
def seeded(pg, monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [])   # client 픽스처의 wire()가 바꾼 것을 되돌림
    with pg.connection() as conn:
        seed_analytics(conn)


def test_dashboard(client, seeded):
    html = client.get("/").get_data(as_text=True)
    assert 'id="kpis"' in html and "echarts.min.js" in html and "js/dashboard.js" in html
    assert 'href="/trends"' in html and 'href="/map"' in html and 'href="/export"' in html


def test_trades_list_moved(client, seeded):
    html = client.get("/trades?lawd_cd=11140").get_data(as_text=True)
    assert "무교타워" in html and "청운아파트" not in html
```

`tests/test_web.py`에서 계획 1 테스트의 `client.get("/")`·`client.get("/?...")`를 모두 `client.get("/trades")`·`client.get("/trades?...")`로 바꾼다(`test_index_lists_trades`, `test_index_filters`).

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py tests/test_web.py -v`
Expected: FAIL — `/trades` 404, 대시보드에 `id="kpis"` 없음

- [ ] **Step 3: 기본 틀과 색 토큰**

`git mv templates/index.html templates/trades.html`

`templates/base.html`의 `<style>` 안 첫 `:root { ... }` 줄 다음에 추가(차트 색: 검증된 기본 팔레트):
```css
    :root { --series-1:#2a78d6; --series-2:#eb6834; --series-3:#1baf7a; --series-4:#eda100; --series-5:#e87ba4; --series-6:#008300; --series-7:#4a3aa7; --series-8:#e34948;
            --seq-100:#cde2fb; --seq-200:#9ec5f4; --seq-300:#6da7ec; --seq-400:#3987e5; --seq-500:#256abf; --seq-600:#184f95; --seq-700:#0d366b;
            --div-neg-2:#1c5cab; --div-neg-1:#86b6ef; --div-mid:#f0efec; --div-pos-1:#f2a3a2; --div-pos-2:#c23a39;
            --grid:#e1e0d9; --axis:#c3c2b7; --provisional:rgba(137,135,129,0.14); }
```
그리고 기존 `@media (prefers-color-scheme: dark) { :root { ... } }` 블록 안 `:root` 줄 다음에 추가:
```css
      :root { --series-1:#3987e5; --series-2:#d95926; --series-3:#199e70; --series-4:#c98500; --series-5:#d55181; --series-6:#008300; --series-7:#9085e9; --series-8:#e66767;
              --div-mid:#383835; --grid:#2c2c2a; --axis:#383835; --provisional:rgba(137,135,129,0.20); }
```
`ul.notes { ... }` 줄 다음에 추가:
```css
    .chart { height: 340px; background: var(--card); border:1px solid var(--line); border-radius:12px; padding:8px; }
    .chart.small { height: 220px; }
    .cols { display:grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap:12px; }
    .chips { display:flex; flex-wrap:wrap; gap:6px; margin: 0 0 12px; }
    .chip { display:inline-flex; gap:6px; align-items:center; padding:4px 8px 4px 6px; border:1px solid var(--line); border-left:4px solid var(--c); border-radius:8px; background:var(--card); font-size:14px; }
    .chip button { padding:0 6px; border:none; background:none; color:var(--muted); }
    .tile .d { font-size: 13px; margin-top: 2px; }
```
`<nav>` 안의 기존 두 링크(`거래 목록`, `수집 현황·누락 점검`)를 다음으로 바꾼다:
```html
    <a href="/" class="{{ 'on' if request.path == '/' }}">대시보드</a>
    <a href="/trends" class="{{ 'on' if request.path == '/trends' }}">추이</a>
    <a href="/map" class="{{ 'on' if request.path == '/map' }}">지도</a>
    <a href="/complexes" class="{{ 'on' if request.path.startswith('/complexes') }}">단지</a>
    <a href="/export" class="{{ 'on' if request.path == '/export' }}">추출</a>
    <a href="/trades" class="{{ 'on' if request.path == '/trades' }}">거래 목록</a>
    <a href="/status" class="{{ 'on' if request.path == '/status' }}">수집 현황</a>
```
`</main>` 다음 줄에 추가:
```html
{% block scripts %}{% endblock %}
```

`templates/_charts.html`:
```html
<script src="https://cdnjs.cloudflare.com/ajax/libs/echarts/5.6.0/echarts.min.js"></script>
<script src="{{ url_for('static', filename='js/common.js') }}"></script>
```

- [ ] **Step 4: 공통 도우미 작성**

`static/js/common.js`:
```javascript
// 공통 도우미: API 호출, 숫자 형식, URL 상태, 차트 기본값(색은 CSS 변수에서 읽어 밝은·어두운 테마를 따른다)
const App = (() => {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  async function api(path, params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') qs.set(k, v);
    const resp = await fetch(qs.toString() ? `${path}?${qs}` : path, { headers: { Accept: 'application/json' } });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `요청 실패 (HTTP ${resp.status})`);
    return data;
  }

  const fmt = {
    int: (v) => (v == null ? '-' : Math.round(v).toLocaleString('ko-KR')),
    eok: (v) => (v == null ? '-' : `${(v / 10000).toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}억`),
    ppm2: (v) => (v == null ? '-' : `${Math.round(v).toLocaleString('ko-KR')}만/㎡`),
    pct: (v) => (v == null ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`),
    ym: (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}`,
  };

  function shiftYm(ym, n) {
    let y = Number(ym.slice(0, 4));
    let m = Number(ym.slice(4)) + n;
    y += Math.floor((m - 1) / 12);
    m = (((m - 1) % 12) + 12) % 12 + 1;
    return `${y}${String(m).padStart(2, '0')}`;
  }
  function monthRange(from, to) {
    const out = [];
    for (let m = from; m <= to; m = shiftYm(m, 1)) out.push(m);
    return out;
  }
  const toMonthInput = (ym) => (ym ? `${ym.slice(0, 4)}-${ym.slice(4)}` : '');
  const fromMonthInput = (v) => (v || '').replace('-', '');

  function readState(defaults) {
    const p = new URLSearchParams(location.search);
    const out = { ...defaults };
    for (const k of Object.keys(defaults)) if (p.has(k)) out[k] = p.get(k);
    return out;
  }
  function writeState(state) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(state)) if (v !== '' && v != null) p.set(k, v);
    history.replaceState(null, '', `${location.pathname}?${p}`);
  }

  // 범주 색은 고정 순서 8개. slot은 지역에 붙는다(순위가 아니라).
  const seriesColor = (slot) => css(`--series-${(slot % 8) + 1}`);

  function chart(el) {
    const c = echarts.init(el);
    window.addEventListener('resize', () => c.resize());
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => location.reload());
    return c;
  }

  function baseOption() {
    return {
      backgroundColor: 'transparent',
      textStyle: { color: css('--muted'), fontFamily: 'inherit' },
      grid: { left: 72, right: 24, top: 40, bottom: 36 },
      tooltip: {
        trigger: 'axis', backgroundColor: css('--card'), borderColor: css('--line'),
        textStyle: { color: css('--text') }, axisPointer: { type: 'line', lineStyle: { color: css('--axis') } },
      },
      legend: { top: 4, textStyle: { color: css('--text') }, icon: 'roundRect', itemWidth: 14, itemHeight: 4 },
      xAxis: {
        type: 'category', axisLine: { lineStyle: { color: css('--axis') } }, axisTick: { show: false },
        axisLabel: { color: css('--muted') },
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: css('--grid') } }, axisLabel: { color: css('--muted') } },
    };
  }

  // 잠정 구간 음영(카테고리 축 라벨 'YYYY-MM' 기준)
  function provisionalArea(fromYm, toYm, rangeFrom) {
    if (!fromYm || !toYm || toYm < fromYm) return undefined;
    const start = rangeFrom && rangeFrom > fromYm ? rangeFrom : fromYm;
    return {
      silent: true, itemStyle: { color: css('--provisional') },
      label: { show: true, position: 'insideTop', color: css('--muted'), formatter: '잠정' },
      data: [[{ xAxis: fmt.ym(start) }, { xAxis: fmt.ym(toYm) }]],
    };
  }

  const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  function message(el, text, cls = 'err') {
    el.className = `meta ${text ? cls : ''}`;
    el.textContent = text || '';
  }

  return { css, api, fmt, shiftYm, monthRange, toMonthInput, fromMonthInput, readState, writeState, seriesColor, chart, baseOption, provisionalArea, escapeHtml, message };
})();
```

- [ ] **Step 5: 대시보드 구현**

`web/pages.py`의 `@bp.route("/")` 데코레이터를 `@bp.route("/trades")`로, 함수 이름 `index`를 `trades`로, `render_template("index.html", ...)`를 `render_template("trades.html", ...)`로 바꾼다. 그리고 추가:
```python
@bp.route("/")
def dashboard():
    return render_template("dashboard.html", p=jobs.progress())
```

`templates/trades.html`에서 페이지 이동·필터 폼이 `/`를 가리키는 곳이 있으면 `/trades`로 바꾼다(`<form class="bar" method="get">`은 action이 없어 현재 경로로 보내므로 그대로 둔다. "초기화" 같은 `href="/"` 링크가 있으면 `href="/trades"`로).

`templates/dashboard.html`:
```html
{% extends "base.html" %}
{% block title %}대시보드 · 아파트 실거래가{% endblock %}
{% block body %}
<p id="msg" class="meta"></p>
<div id="kpis" class="tiles"></div>
<h2>전국 월별 거래량</h2>
<div id="vol" class="chart"></div>
<h2>전국 월별 중위 거래가</h2>
<div id="price" class="chart"></div>
<p class="meta">음영 구간은 신고기한(30일)이 지나지 않은 잠정 자료입니다. 해제 거래는 제외했습니다.</p>
<h2>중위가 전년 대비 상승·하락 시군구 <span id="mover-note" class="muted"></span></h2>
<div class="cols">
  <div class="wrap"><table id="up"></table></div>
  <div class="wrap"><table id="down"></table></div>
</div>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script src="{{ url_for('static', filename='js/dashboard.js') }}"></script>{% endblock %}
```

`static/js/dashboard.js`:
```javascript
// 대시보드: 전국 요약, 24개월 거래량·중위가(차트 2개, 축을 섞지 않음), 상승·하락 시군구
(async () => {
  const el = (id) => document.getElementById(id);
  let data;
  try {
    data = await App.api('/api/summary');
  } catch (e) {
    App.message(el('msg'), e.message);
    return;
  }
  const { kpi, series, movers } = data;
  const delta = (v) => `<div class="d ${v == null ? 'muted' : v >= 0 ? 'err' : 'ok'}">전년 동월 대비 ${App.fmt.pct(v)}</div>`;
  el('kpis').innerHTML = `
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 거래량 (확정)</div><div class="v">${App.fmt.int(kpi.n)}건</div>${delta(kpi.yoy_n)}</div>
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 전국 중위 거래가</div><div class="v">${App.fmt.eok(kpi.median)}</div>${delta(kpi.yoy_median)}</div>
    <div class="tile"><div class="k">경계 버전</div><div class="v">${App.escapeHtml(data.version)}</div></div>`;

  const labels = series.map((s) => App.fmt.ym(s.ym));
  const last = series[series.length - 1].ym;
  const prov = App.provisionalArea(data.provisional_from, last, series[0].ym);
  const color = App.seriesColor(0);

  const vol = App.chart(el('vol'));
  vol.setOption({
    ...App.baseOption(),
    legend: { show: false },
    xAxis: { ...App.baseOption().xAxis, data: labels },
    yAxis: { ...App.baseOption().yAxis, axisLabel: { color: App.css('--muted'), formatter: App.fmt.int } },
    tooltip: { ...App.baseOption().tooltip, valueFormatter: (v) => `${App.fmt.int(v)}건` },
    series: [{ name: '거래량', type: 'bar', data: series.map((s) => s.n), itemStyle: { color, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 18, markArea: prov }],
  });

  const price = App.chart(el('price'));
  price.setOption({
    ...App.baseOption(),
    legend: { show: false },
    xAxis: { ...App.baseOption().xAxis, data: labels },
    yAxis: { ...App.baseOption().yAxis, scale: true, axisLabel: { color: App.css('--muted'), formatter: App.fmt.eok } },
    tooltip: { ...App.baseOption().tooltip, valueFormatter: App.fmt.eok },
    series: [{ name: '중위 거래가', type: 'line', data: series.map((s) => s.median), color, symbol: 'none', lineStyle: { width: 2 }, markArea: prov }],
  });

  el('mover-note').textContent = `(${App.fmt.ym(movers.window[0])}~${App.fmt.ym(movers.window[1])} vs 전년 같은 기간, 두 기간 모두 ${movers.min_trades}건 이상)`;
  const table = (rows, title) => `<thead><tr><th>${title}</th><th class="num">전년 대비</th><th class="num">중위가</th><th class="num">거래</th></tr></thead><tbody>${
    rows.length ? rows.map((m) => `<tr><td><a href="/trends?regions=sgg:${m.region_cd}">${App.escapeHtml(m.name)}</a></td><td class="num">${App.fmt.pct(m.yoy)}</td><td class="num">${App.fmt.eok(m.median)}</td><td class="num">${App.fmt.int(m.n)}</td></tr>`).join('')
      : '<tr><td colspan="4" class="muted">해당 시군구 없음</td></tr>'}</tbody>`;
  el('up').innerHTML = table(movers.up, '상승 상위');
  el('down').innerHTML = table(movers.down, '하락 상위');
})();
```
(증감 글자색: 한국 관례에 따라 상승을 `err`(빨강), 하락을 `ok`(초록 계열)로 쓴다. 글자는 상태 색 토큰을 쓰고 계열 색을 쓰지 않는다.)

- [ ] **Step 6: 개발용 데이터 스크립트**

`.gitignore` 끝에 추가:
```
# 개발용 가짜 경계(scripts/seed_dev.py)
static/geo/0000-dev/
geo_data/0000-dev/
```

`scripts/seed_dev.py`:
```python
"""[로컬 개발용] 화면 확인용 가짜 데이터를 개발 DB에 만든다. 운영 DB에는 쓰지 않는다.

사용: python scripts/seed_dev.py [--reset]
- 경계 버전 0000-dev(서울 3개·부산 2개 시군구, 각 3개 읍면동 사각형)를 static/geo, geo_data에 쓴다(저장소 제외).
- 2023-01 ~ 이번 달 거래를 단지마다 무작위로 만들고, 단지 좌표·지역 판정·집계까지 만든다.
"""
import argparse
import csv
import gzip
import json
import random
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402
import settings  # noqa: E402
import wiring  # noqa: E402
from collector import codes, store  # noqa: E402
from geo import versions  # noqa: E402
from shapely.geometry import box, mapping  # noqa: E402
from shapely.ops import unary_union  # noqa: E402

VERSION = "0000-dev"
SGG = {"11110": (126.95, 37.58), "11140": (126.98, 37.58), "11170": (126.95, 37.55),
       "26110": (129.02, 35.10), "26140": (128.99, 35.10)}
SIZE = 0.03   # 시군구 한 변(도)
BASE_PRICE = {"11": 90000, "26": 40000}


def squares():
    """읍면동 코드 → (lon0, lat0, lon1, lat1). 시군구 사각형을 세로 3칸으로 나눈다."""
    out = {}
    for sgg, (x, y) in SGG.items():
        for i in range(3):
            out[f"{sgg}{101 + i:03d}"] = (x + i * SIZE / 3, y, x + (i + 1) * SIZE / 3, y + SIZE)
    return out


def fc(features):
    return {"type": "FeatureCollection", "features": features}


def write_boundary():
    names = {r.LAWD_CD: (r.시도, r.시군구) for r in codes.load_codes().itertuples()}
    sq = squares()
    umd = {c: box(*b) for c, b in sq.items()}
    sgg = {s: unary_union([g for c, g in umd.items() if c[:5] == s]) for s in SGG}
    sido = {d: unary_union([g for s, g in sgg.items() if s[:2] == d]) for d in {s[:2] for s in SGG}}
    feat = lambda geom, **props: {"type": "Feature", "properties": props, "geometry": mapping(geom)}  # noqa: E731
    web = settings.BASE_DIR / "static" / "geo" / VERSION
    data = settings.BASE_DIR / "geo_data" / VERSION
    web.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    (web / "sido.json").write_text(json.dumps(fc([feat(g, region_cd=d, name=names[next(s for s in SGG if s[:2] == d)][0])
                                                  for d, g in sido.items()]), ensure_ascii=False), encoding="utf-8")
    (web / "sgg.json").write_text(json.dumps(fc([feat(g, region_cd=s, name=names[s][1], sido_cd=s[:2])
                                                 for s, g in sgg.items()]), ensure_ascii=False), encoding="utf-8")
    for d in sido:
        (web / f"umd_{d}.json").write_text(json.dumps(fc([feat(g, region_cd=c, name=f"{c[-1]}동", sgg_cd=c[:5])
                                                          for c, g in umd.items() if c[:2] == d]),
                                                      ensure_ascii=False), encoding="utf-8")
    (data / "umd_assign.geojson.gz").write_bytes(gzip.compress(json.dumps(fc(
        [feat(g, region_cd=c, sgg_cd=c[:5]) for c, g in umd.items()])).encode()))
    with open(data / "regions.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["region_cd", "level", "name", "full_name", "parent_cd"])
        for d in sorted(sido):
            sname = names[next(s for s in SGG if s[:2] == d)][0]
            w.writerow([d, "sido", sname, sname, ""])
        for s in SGG:
            w.writerow([s, "sgg", names[s][1], f"{names[s][0]} {names[s][1]}", s[:2]])
        for c in sq:
            w.writerow([c, "umd", f"{c[-1]}동", f"{names[c[:5]][0]} {names[c[:5]][1]} {c[-1]}동", c[:5]])
    (data / "meta.json").write_text(json.dumps({"version": VERSION, "source": "seed_dev"}), encoding="utf-8")
    return sq


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="기존 데이터를 지우고 다시 만든다")
    args = parser.parse_args(argv)
    if urlparse(settings.require("DATABASE_URL")).hostname not in ("localhost", "127.0.0.1"):
        print("seed_dev는 로컬 DB에만 씁니다.")
        return 1
    db.migrate()
    wiring.wire()
    rnd = random.Random(42)
    sq = write_boundary()
    with db.connection() as conn:
        if conn.execute("SELECT EXISTS (SELECT 1 FROM trades) AS e").fetchone()["e"]:
            if not args.reset:
                print("이미 거래가 있습니다. 지우고 다시 만들려면 --reset")
                return 1
            conn.execute("TRUNCATE trades, jobs, changes, api_usage, complexes, address_points, agg_month, "
                         "agg_dirty, regions, boundary_versions RESTART IDENTITY")
        complexes_rows = []
        for code, (x0, y0, x1, y1) in sq.items():
            for k in range(2):
                seq = f"{code[:5]}-{code[5:]}{k}"
                complexes_rows.append((seq, f"{code[-1]}동{k + 1}단지", code[:5], (x0 + x1) / 2, (y0 + y1) / 2,
                                       rnd.choice([1995, 2003, 2012, 2019])))
        with conn.cursor() as cur:
            cur.executemany("INSERT INTO complexes (apt_seq, apt_nm, api_sgg_cd, lon, lat, build_year, "
                            "geocode_status, geocode_source) VALUES (%s, %s, %s, %s, %s, %s, 'ok', 'manual')",
                            complexes_rows)
        months = codes.month_range("202301", codes.months_ago(0))
        for i, ym in enumerate(months):
            for sgg in SGG:
                items = []
                for seq, name, s, *_ in complexes_rows:
                    if s != sgg:
                        continue
                    level = BASE_PRICE[sgg[:2]] * (1 + 0.004 * i) * (0.8 + 0.4 * (sum(map(ord, seq)) % 7) / 6)
                    for _ in range(rnd.randint(0 if ym >= codes.months_ago(1) else 1, 5)):
                        area = rnd.choice([49.9, 59.9, 84.9, 114.8])
                        items.append(dict(
                            sggCd=sgg, umdCd="10100", umdNm=f"{seq[-2]}동", aptNm=name, aptSeq=seq,
                            dealYear=ym[:4], dealMonth=str(int(ym[4:])), dealDay=str(rnd.randint(1, 28)),
                            dealAmount=f"{int(level * area / 84.9 * rnd.uniform(0.9, 1.1)):,}",
                            excluUseAr=str(area), floor=str(rnd.randint(1, 25)), buildYear="2005",
                            cdealType="O" if rnd.random() < 0.03 else ""))
                conn.execute("INSERT INTO jobs (lawd_cd, deal_ymd) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                             (sgg, ym))
                store.save_job(conn, sgg, ym, items, len(items))
        versions.register(conn, VERSION)
        versions.switch(conn, VERSION)          # 단지 지역 판정 + 집계(BEFORE_ACTIVATE)
        n = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
    print(f"경계 {VERSION}, 거래 {n:,}건, 단지 {len(complexes_rows)}개를 만들었습니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 7: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 8: 화면 확인**

```bash
.venv/Scripts/python scripts/seed_dev.py --reset
```
Expected: `경계 0000-dev, 거래 …건, 단지 30개를 만들었습니다.`

preview_start `{name: "molit-web"}` → `/login`(`.env`의 `APP_PASSWORD`) → `/`에서 확인한다:
- 요약 타일 3개, 거래량 막대·중위가 선 차트가 따로 그려지고 마지막 2개월에 "잠정" 음영
- 막대·선에 마우스를 올리면 툴팁(건수 / 억 단위)
- 상승·하락 표(가짜 데이터라 상승만 있을 수 있음), 시군구 이름을 누르면 `/trends?regions=sgg:...`
- read_console_messages로 오류가 없는지, resize_window `colorScheme: "dark"`로 어두운 테마에서도 글자·격자가 보이는지
스크린샷을 남긴다.

- [ ] **Step 9: Commit**

```bash
git add templates/ static/js/common.js static/js/dashboard.js web/pages.py scripts/seed_dev.py .gitignore tests/test_pages.py tests/test_web.py
git commit -m "분석 화면 골격·차트 색 토큰·대시보드와 개발용 데이터 스크립트 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 추이 화면

**Files:**
- Create: `templates/trends.html`, `static/js/trends.js`
- Modify: `web/pages.py`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 2 `/api/regions`, `/api/agg`; Task 4 `App`
- Produces: 라우트 `pages.trends_page`(`/trends`). URL 상태 `regions`, `metric`(`median_price`|`median_ppm2`|`n_trades`|`range`), `band`, `from`, `to`, `ma`(1), `idx`(1)

- [ ] **Step 1: 테스트 작성**

`tests/test_pages.py`에 추가:
```python
def test_trends_page(client, seeded):
    html = client.get("/trends").get_data(as_text=True)
    assert 'id="chart"' in html and "js/trends.js" in html
    assert '<option value="le60">60㎡ 이하</option>' in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py::test_trends_page -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

`web/pages.py`에 추가(import에 `from analytics import params`):
```python
@bp.route("/trends")
def trends_page():
    return render_template("trends.html", p=jobs.progress(), bands=params.BANDS)
```

`templates/trends.html`:
```html
{% extends "base.html" %}
{% block title %}추이 · 아파트 실거래가{% endblock %}
{% block body %}
<div class="bar">
  <select id="sel-sido" aria-label="시도"></select>
  <select id="sel-sgg" aria-label="시군구"></select>
  <select id="sel-umd" aria-label="읍면동"></select>
  <button id="add" class="primary">지역 추가</button>
  <span class="muted">최대 8개 · 아무것도 고르지 않고 추가하면 전국</span>
</div>
<div id="chips" class="chips"></div>
<div class="bar">
  <select id="metric" aria-label="지표">
    <option value="median_price">중위 거래가</option>
    <option value="median_ppm2">㎡당 중위가</option>
    <option value="n_trades">거래량</option>
    <option value="range">25~75% 범위 (지역 1개)</option>
  </select>
  <select id="band" aria-label="면적 구간">{% for v, l in bands %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select>
  <input type="month" id="from" aria-label="시작월"> ~ <input type="month" id="to" aria-label="종료월">
  <label class="muted"><input type="checkbox" id="ma"> 3개월 이동평균</label>
  <label class="muted"><input type="checkbox" id="idx"> 시작월=100 지수</label>
  <button id="toggle-table">표 보기</button>
  <button id="png">PNG 저장</button>
</div>
<p id="msg" class="meta"></p>
<div id="chart" class="chart"></div>
<p class="meta">음영 구간은 신고기한(30일)이 지나지 않은 잠정 자료입니다. 해제 거래는 제외했고, 거래가 없는 달은 선이 끊깁니다.</p>
<div id="table" class="wrap" hidden></div>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script src="{{ url_for('static', filename='js/trends.js') }}"></script>{% endblock %}
```

`static/js/trends.js`:
```javascript
// 추이: 지역 최대 8개(수준 혼합), 지표·면적·기간, 3개월 이동평균, 시작월=100 지수, 표 보기, PNG 저장
(async () => {
  const MAX = 8;
  const el = (id) => document.getElementById(id);
  const METRICS = {
    median_price: ['중위 거래가', App.fmt.eok],
    median_ppm2: ['㎡당 중위가', App.fmt.ppm2],
    n_trades: ['거래량', (v) => `${App.fmt.int(v)}건`],
    range: ['25~75% 범위', App.fmt.eok],
  };
  const state = App.readState({ regions: 'nation:00', metric: 'median_price', band: 'all', from: '', to: '', ma: '', idx: '' });
  const chart = App.chart(el('chart'));
  let picked = state.regions.split(',').filter(Boolean).slice(0, MAX)
    .map((key, slot) => ({ key, name: key, slot }));
  let last = null;

  // 컨트롤 초기값
  el('metric').value = state.metric;
  el('band').value = state.band;
  el('ma').checked = state.ma === '1';
  el('idx').checked = state.idx === '1';

  async function fill(sel, level, parent, placeholder) {
    sel.innerHTML = '';
    sel.add(new Option(placeholder, ''));
    if (level !== 'sido' && !parent) return;
    try {
      const data = await App.api('/api/regions', { level, parent });
      for (const r of data.regions) sel.add(new Option(r.name, r.region_cd));
    } catch (e) { App.message(el('msg'), e.message); }
  }
  await fill(el('sel-sido'), 'sido', null, '시도 선택');
  await fill(el('sel-sgg'), 'sgg', null, '시군구 전체');
  await fill(el('sel-umd'), 'umd', null, '읍면동 전체');
  el('sel-sido').onchange = () => { fill(el('sel-sgg'), 'sgg', el('sel-sido').value, '시군구 전체'); fill(el('sel-umd'), 'umd', null, '읍면동 전체'); };
  el('sel-sgg').onchange = () => fill(el('sel-umd'), 'umd', el('sel-sgg').value, '읍면동 전체');

  el('add').onclick = () => {
    const umd = el('sel-umd').value, sgg = el('sel-sgg').value, sido = el('sel-sido').value;
    const key = umd ? `umd:${umd}` : sgg ? `sgg:${sgg}` : sido ? `sido:${sido}` : 'nation:00';
    if (picked.some((p) => p.key === key)) return;
    if (picked.length >= MAX) { App.message(el('msg'), `지역은 최대 ${MAX}개까지 비교할 수 있습니다.`); return; }
    const used = new Set(picked.map((p) => p.slot));
    const slot = [...Array(MAX).keys()].find((i) => !used.has(i));   // 남은 지역의 색은 그대로
    picked.push({ key, name: key, slot });
    render();
  };

  function drawChips() {
    el('chips').innerHTML = picked.map((p) => `<span class="chip" style="--c:${App.seriesColor(p.slot)}">${App.escapeHtml(p.name)}<button data-key="${App.escapeHtml(p.key)}" aria-label="빼기">×</button></span>`).join('');
    el('chips').querySelectorAll('button').forEach((b) => { b.onclick = () => { picked = picked.filter((p) => p.key !== b.dataset.key); render(); }; });
  }

  for (const id of ['metric', 'band', 'ma', 'idx', 'from', 'to']) el(id).onchange = () => render();

  function movingAvg(vals, n) {
    return vals.map((_, i) => {
      if (i < n - 1) return null;
      const w = vals.slice(i - n + 1, i + 1);
      return w.some((v) => v == null) ? null : w.reduce((a, b) => a + b, 0) / n;
    });
  }

  function readControls() {
    state.metric = el('metric').value;
    state.band = el('band').value;
    state.ma = el('ma').checked ? '1' : '';
    state.idx = el('idx').checked ? '1' : '';
    state.from = App.fromMonthInput(el('from').value) || state.from;
    state.to = App.fromMonthInput(el('to').value) || state.to;
    state.regions = picked.map((p) => p.key).join(',');
  }

  async function render() {
    readControls();
    App.writeState(state);
    drawChips();
    App.message(el('msg'), '');
    if (!picked.length) { chart.clear(); el('table').innerHTML = ''; return; }
    if (state.metric === 'range' && picked.length > 1) {
      App.message(el('msg'), '25~75% 범위는 지역을 1개만 골랐을 때 볼 수 있습니다.', 'warn');
      chart.clear();
      return;
    }
    try {
      last = await App.api('/api/agg', { regions: state.regions, band: state.band, from: state.from, to: state.to });
    } catch (e) { App.message(el('msg'), e.message); return; }
    state.from = last.from; state.to = last.to;
    el('from').value = App.toMonthInput(last.from);
    el('to').value = App.toMonthInput(last.to);
    App.writeState(state);
    for (const s of last.series) { const p = picked.find((x) => x.key === s.key); if (p) p.name = s.name; }
    drawChips();
    draw();
  }

  function valuesFor(s, months) {
    const by = Object.fromEntries(s.points.map((pt) => [pt.ym, pt]));
    let vals = months.map((m) => by[m]?.[state.metric] ?? null);
    if (state.ma) vals = movingAvg(vals, 3);
    if (state.idx) {
      const base = vals.find((v) => v != null);
      vals = vals.map((v) => (v == null || !base ? null : (v / base) * 100));
    }
    return { by, vals };
  }

  function draw() {
    const months = App.monthRange(last.from, last.to);
    const labels = months.map(App.fmt.ym);
    const [title, f] = METRICS[state.metric];
    const yfmt = state.idx ? (v) => v.toFixed(1) : f;
    const base = App.baseOption();
    const series = [];
    let tooltip = { ...base.tooltip, valueFormatter: (v) => (v == null ? '-' : yfmt(v)) };

    if (state.metric === 'range') {
      const s = last.series[0];
      const p = picked.find((x) => x.key === s.key);
      const color = App.seriesColor(p.slot);
      const by = Object.fromEntries(s.points.map((pt) => [pt.ym, pt]));
      const lo = months.map((m) => by[m]?.p25_price ?? null);
      const hi = months.map((m) => by[m]?.p75_price ?? null);
      series.push(
        { name: '25%', type: 'line', data: lo, stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, silent: true },
        { name: '25~75%', type: 'line', data: hi.map((h, i) => (h == null || lo[i] == null ? null : h - lo[i])), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color, opacity: 0.18 }, silent: true },
        { name: s.name, type: 'line', data: months.map((m) => by[m]?.median_price ?? null), color, symbol: 'none', lineStyle: { width: 2 } },
      );
      tooltip = { ...base.tooltip, formatter: (ps) => {
        const m = months[ps[0].dataIndex];
        const pt = by[m];
        return pt ? `${App.fmt.ym(m)}<br>75%: ${App.fmt.eok(pt.p75_price)}<br>중위: <b>${App.fmt.eok(pt.median_price)}</b><br>25%: ${App.fmt.eok(pt.p25_price)}<br>${App.fmt.int(pt.n_trades)}건` : `${App.fmt.ym(m)}<br>거래 없음`;
      } };
    } else {
      for (const s of last.series) {
        const p = picked.find((x) => x.key === s.key);
        series.push({ name: s.name, type: 'line', data: valuesFor(s, months).vals, color: App.seriesColor(p.slot), symbol: 'none', lineStyle: { width: 2 }, emphasis: { focus: 'series' }, connectNulls: false });
      }
    }
    if (series.length) series[series.length - 1].markArea = App.provisionalArea(last.provisional_from, last.to, last.from);

    chart.setOption({
      ...base,
      legend: { ...base.legend, show: state.metric !== 'range' && last.series.length > 1 },
      xAxis: { ...base.xAxis, data: labels },
      yAxis: { ...base.yAxis, scale: state.metric !== 'n_trades', name: state.idx ? `${title} (시작월=100)` : title, nameTextStyle: { color: App.css('--muted') }, axisLabel: { color: App.css('--muted'), formatter: yfmt } },
      tooltip,
      series,
    }, true);
    drawTable(months, yfmt);
  }

  function drawTable(months, yfmt) {
    if (state.metric === 'range') {
      const s = last.series[0];
      const by = Object.fromEntries(s.points.map((pt) => [pt.ym, pt]));
      el('table').innerHTML = `<table><thead><tr><th>계약월</th><th class="num">25%</th><th class="num">중위</th><th class="num">75%</th><th class="num">거래</th></tr></thead><tbody>${
        months.map((m) => `<tr><td>${App.fmt.ym(m)}</td><td class="num">${App.fmt.eok(by[m]?.p25_price)}</td><td class="num">${App.fmt.eok(by[m]?.median_price)}</td><td class="num">${App.fmt.eok(by[m]?.p75_price)}</td><td class="num">${App.fmt.int(by[m]?.n_trades)}</td></tr>`).join('')}</tbody></table>`;
      return;
    }
    const cols = last.series.map((s) => valuesFor(s, months).vals);
    el('table').innerHTML = `<table><thead><tr><th>계약월</th>${last.series.map((s) => `<th class="num">${App.escapeHtml(s.name)}</th>`).join('')}</tr></thead><tbody>${
      months.map((m, i) => `<tr><td>${App.fmt.ym(m)}</td>${cols.map((c) => `<td class="num">${c[i] == null ? '-' : yfmt(c[i])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }

  el('toggle-table').onclick = () => {
    el('table').hidden = !el('table').hidden;
    el('toggle-table').textContent = el('table').hidden ? '표 보기' : '표 숨기기';
  };
  el('png').onclick = () => {
    const a = document.createElement('a');
    a.href = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: App.css('--card') });
    a.download = `trends_${state.from}_${state.to}.png`;
    a.click();
  };

  render();
})();
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 5: 화면 확인**

`/trends`에서 확인하고 스크린샷을 남긴다:
- 처음 열면 전국 1개 선. 서울특별시 → 종로구를 골라 추가, 부산 → 중구를 추가하면 선 3개·범례·칩 3개(칩 왼쪽 띠 색 = 선 색)
- 가운데 칩(종로구)을 빼도 나머지 선 색이 바뀌지 않는다
- 9번째 추가 시 "최대 8개" 메시지
- 면적 60㎡ 이하·지표 거래량·3개월 이동평균·지수 토글이 반영되고, URL이 바뀌며 새로고침해도 같은 화면
- 거래 없는 달이 있으면 선이 끊긴다(가짜 데이터의 이번 달은 일부 단지 0건)
- 범위 지표 + 지역 2개 → 안내 메시지, 지역 1개 → 음영 띠와 중위선
- 표 보기·PNG 저장 동작, read_console_messages 오류 없음, 어두운 테마 확인

- [ ] **Step 6: Commit**

```bash
git add templates/trends.html static/js/trends.js web/pages.py tests/test_pages.py
git commit -m "추이 화면 추가(지역 비교·이동평균·지수·표·PNG)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 지도 화면

**Files:**
- Create: `templates/map.html`, `static/js/map.js`
- Modify: `web/pages.py`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 2 `/api/map`, `/api/agg`; 계획 2 `static/geo/{version}/sido.json|sgg.json|umd_{sido}.json`(속성 `region_cd`, `name`, `sido_cd`/`sgg_cd`)
- Produces: 라우트 `pages.map_page`(`/map`). URL 상태 `level`, `parent`, `metric`(`median_price`|`median_ppm2`|`n_trades`|`yoy_price`|`yoy_n`), `band`, `from`, `to`

- [ ] **Step 1: 테스트 작성**

`tests/test_pages.py`에 추가:
```python
def test_map_page(client, seeded):
    html = client.get("/map").get_data(as_text=True)
    assert 'id="map"' in html and 'id="ranking"' in html and "js/map.js" in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py::test_map_page -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

`web/pages.py`에 추가:
```python
@bp.route("/map")
def map_page():
    return render_template("map.html", p=jobs.progress(), bands=params.BANDS)
```

`templates/map.html`:
```html
{% extends "base.html" %}
{% block title %}지도 · 아파트 실거래가{% endblock %}
{% block body %}
<div class="bar">
  <span id="crumbs"></span>
  <select id="metric" aria-label="지표">
    <option value="median_price">중위 거래가</option>
    <option value="median_ppm2">㎡당 중위가</option>
    <option value="n_trades">거래량</option>
    <option value="yoy_price">중위가 전년 대비</option>
    <option value="yoy_n">거래량 전년 대비</option>
  </select>
  <select id="band" aria-label="면적 구간">{% for v, l in bands %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select>
  <input type="month" id="from" aria-label="시작월"> ~ <input type="month" id="to" aria-label="종료월">
</div>
<p id="msg" class="meta"></p>
<div class="cols">
  <div id="map" class="chart" style="height:520px"></div>
  <div>
    <div id="side" class="tile" hidden>
      <div class="k" id="side-name"></div>
      <div id="mini" class="chart small"></div>
      <a id="side-link" href="#">추이에서 보기</a>
    </div>
    <div class="wrap" style="max-height:520px;overflow:auto;margin-top:12px"><table id="ranking"></table></div>
  </div>
</div>
<p class="meta" id="note"></p>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script src="{{ url_for('static', filename='js/map.js') }}"></script>{% endblock %}
```

`static/js/map.js`:
```javascript
// 지도: 시도 → 시군구 → 읍면동 클릭 드릴다운, 단계구분도(크기=단일 색조 순차, 증감=파랑↔빨강 발산), 순위표, 미니 추이
(async () => {
  const el = (id) => document.getElementById(id);
  const METRICS = {
    median_price: ['중위 거래가', App.fmt.eok, 'seq'],
    median_ppm2: ['㎡당 중위가', App.fmt.ppm2, 'seq'],
    n_trades: ['거래량', (v) => `${App.fmt.int(v)}건`, 'seq'],
    yoy_price: ['중위가 전년 대비', App.fmt.pct, 'div'],
    yoy_n: ['거래량 전년 대비', App.fmt.pct, 'div'],
  };
  const NEXT = { sido: 'sgg', sgg: 'umd' };
  const state = App.readState({ level: 'sido', parent: '', metric: 'median_price', band: 'all', from: '', to: '' });
  const chart = App.chart(el('map'));
  const mini = App.chart(el('mini'));
  const geoCache = {};
  let data = null;

  el('metric').value = state.metric;
  el('band').value = state.band;

  async function geo(version, level, parent) {
    const file = level === 'sido' ? 'sido' : level === 'sgg' ? 'sgg' : `umd_${parent.slice(0, 2)}`;
    const url = `/static/geo/${encodeURIComponent(version)}/${file}.json`;
    if (!geoCache[url]) {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error('경계 파일을 불러오지 못했습니다.');
      geoCache[url] = await resp.json();
    }
    const fc = geoCache[url];
    if (level === 'sido') return fc;
    const key = level === 'sgg' ? 'sido_cd' : 'sgg_cd';
    return { type: 'FeatureCollection', features: fc.features.filter((f) => f.properties[key] === parent) };
  }

  function visualMap(kind, nums, f) {
    const common = { type: 'continuous', calculable: true, orient: 'horizontal', left: 'center', bottom: 8, itemHeight: 160, textStyle: { color: App.css('--muted') }, formatter: (v) => f(v) };
    if (!nums.length) return { ...common, show: false, min: 0, max: 1, inRange: { color: [App.css('--track')] } };
    if (kind === 'div') {
      const m = Math.max(...nums.map(Math.abs)) || 1;
      return { ...common, min: -m, max: m, text: ['상승', '하락'], inRange: { color: ['--div-neg-2', '--div-neg-1', '--div-mid', '--div-pos-1', '--div-pos-2'].map(App.css) } };
    }
    return { ...common, min: Math.min(...nums), max: Math.max(...nums), text: ['높음', '낮음'], inRange: { color: ['--seq-100', '--seq-300', '--seq-500', '--seq-700'].map(App.css) } };
  }

  async function render() {
    state.metric = el('metric').value;
    state.band = el('band').value;
    state.from = App.fromMonthInput(el('from').value) || state.from;
    state.to = App.fromMonthInput(el('to').value) || state.to;
    App.writeState(state);
    App.message(el('msg'), '');
    try {
      data = await App.api('/api/map', { level: state.level, parent: state.parent, band: state.band, from: state.from, to: state.to });
      state.from = data.from; state.to = data.to;
      el('from').value = App.toMonthInput(data.from);
      el('to').value = App.toMonthInput(data.to);
      App.writeState(state);
      const fc = await geo(data.version, data.level, data.parent);
      const name = `${data.version}:${data.level}:${data.parent || ''}`;
      echarts.registerMap(name, fc);
      const [title, f, kind] = METRICS[state.metric];
      const vals = data.values.map((v) => ({ name: v.region_cd, value: v[state.metric], raw: v }));
      const nums = vals.map((v) => v.value).filter((v) => v != null);
      chart.setOption({
        tooltip: {
          trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
          formatter: (p) => {
            const v = p.data?.raw;
            if (!v) return '';
            return `<b>${App.escapeHtml(v.full_name)}</b><br>${title}: ${f(p.data.value)}<br>거래 ${App.fmt.int(v.n)}건${state.metric.startsWith('yoy') ? '' : `<br>전년 대비 ${App.fmt.pct(v.yoy_price)}`}`;
          },
        },
        visualMap: visualMap(kind, nums, f),
        series: [{
          type: 'map', map: name, nameProperty: 'region_cd', data: vals, roam: true, selectedMode: false,
          itemStyle: { areaColor: App.css('--track'), borderColor: App.css('--card'), borderWidth: 1 },
          emphasis: { label: { show: true, color: App.css('--text'), formatter: (p) => p.data?.raw?.name ?? '' }, itemStyle: { borderColor: App.css('--text'), borderWidth: 2 } },
        }],
      }, true);
      drawCrumbs();
      drawRanking(title, f);
      el('note').textContent = `${App.fmt.ym(data.from)}~${App.fmt.ym(data.to)} · 가격은 월별 중위가의 거래량 가중평균 · 전년 대비는 ${App.fmt.ym(data.prev_from)}~${App.fmt.ym(data.prev_to)}와 비교`
        + (data.coverage == null ? '' : ` · 읍면동 커버리지 ${data.coverage}% (좌표가 있는 단지의 거래 비율)`);
    } catch (e) { App.message(el('msg'), e.message); }
  }

  function drawCrumbs() {
    const parts = [{ level: 'sido', parent: '', name: '전국' }];
    for (const p of data.parents) parts.push({ level: NEXT[p.level], parent: p.region_cd, name: p.name });
    el('crumbs').innerHTML = parts.map((p, i) => (i === parts.length - 1 ? `<b>${App.escapeHtml(p.name)}</b>`
      : `<a href="#" data-level="${p.level}" data-parent="${p.parent}">${App.escapeHtml(p.name)}</a>`)).join(' › ');
    el('crumbs').querySelectorAll('a').forEach((a) => { a.onclick = (ev) => { ev.preventDefault(); state.level = a.dataset.level; state.parent = a.dataset.parent; render(); }; });
  }

  function drawRanking(title, f) {
    const rows = [...data.values].sort((a, b) => (b[state.metric] ?? -Infinity) - (a[state.metric] ?? -Infinity));
    el('ranking').innerHTML = `<thead><tr><th>#</th><th>지역</th><th class="num">${title}</th><th class="num">거래</th></tr></thead><tbody>${
      rows.map((v, i) => `<tr><td class="muted">${i + 1}</td><td><a href="#" data-cd="${v.region_cd}">${App.escapeHtml(v.name)}</a></td><td class="num">${f(v[state.metric])}</td><td class="num">${App.fmt.int(v.n)}</td></tr>`).join('')}</tbody>`;
    el('ranking').querySelectorAll('a').forEach((a) => { a.onclick = (ev) => { ev.preventDefault(); showMini(data.values.find((v) => v.region_cd === a.dataset.cd)); }; });
  }

  async function showMini(v) {
    if (!v) return;
    const key = `${data.level}:${v.region_cd}`;
    el('side').hidden = false;
    el('side-name').textContent = `${v.full_name} · 중위 거래가 최근 36개월`;
    el('side-link').href = `/trends?regions=${encodeURIComponent(key)}&band=${state.band}`;
    try {
      const to = data.to;
      const agg = await App.api('/api/agg', { regions: key, band: state.band, from: App.shiftYm(to, -35), to });
      const months = App.monthRange(agg.from, agg.to);
      const by = Object.fromEntries(agg.series[0].points.map((p) => [p.ym, p]));
      const base = App.baseOption();
      mini.setOption({
        ...base, legend: { show: false }, grid: { left: 56, right: 12, top: 12, bottom: 24 },
        xAxis: { ...base.xAxis, data: months.map(App.fmt.ym) },
        yAxis: { ...base.yAxis, scale: true, axisLabel: { color: App.css('--muted'), formatter: App.fmt.eok } },
        tooltip: { ...base.tooltip, valueFormatter: App.fmt.eok },
        series: [{ name: '중위 거래가', type: 'line', data: months.map((m) => by[m]?.median_price ?? null), color: App.seriesColor(0), symbol: 'none', lineStyle: { width: 2 } }],
      }, true);
    } catch (e) { App.message(el('msg'), e.message); }
  }

  chart.on('click', (p) => {
    const v = p.data?.raw;
    if (!v) return;
    showMini(v);
    if (NEXT[state.level]) { state.parent = v.region_cd; state.level = NEXT[state.level]; render(); }
  });
  for (const id of ['metric', 'band', 'from', 'to']) el(id).onchange = () => render();
  render();
})();
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 5: 화면 확인**

`/map`에서 확인하고 스크린샷을 남긴다:
- 시도 2개(서울·부산)가 파랑 순차색으로 칠해지고 아래 색 막대, 오른쪽 순위표
- 서울 클릭 → 시군구 3개, 경로 "전국 › 서울특별시", 미니 추이 표시. 종로구 클릭 → 읍면동 3개, 노트에 읍면동 커버리지 100%
- 경로의 "전국" 클릭으로 돌아감, 지표를 "중위가 전년 대비"로 바꾸면 파랑↔빨강 발산색(가운데 회색)
- 마우스를 올리면 툴팁, 기간·면적 변경 반영, URL 상태 유지, 콘솔 오류 없음, 어두운 테마 확인

- [ ] **Step 6: Commit**

```bash
git add templates/map.html static/js/map.js web/pages.py tests/test_pages.py
git commit -m "지도 화면 추가(드릴다운 단계구분도·순위표·미니 추이)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 단지 검색·상세 화면

**Files:**
- Create: `templates/complexes.html`, `templates/complex.html`, `static/js/complex.js`
- Modify: `web/pages.py`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 2 `queries.search_complexes`, `queries.complex_detail`, `/api/complexes/<seq>`
- Produces: 라우트 `pages.complexes_page`(`/complexes?q=&region=`), `pages.complex_page`(`/complexes/<apt_seq>`, 없으면 404)

- [ ] **Step 1: 테스트 작성**

`tests/test_pages.py`에 추가:
```python
def test_complexes_search_page(client, seeded):
    html = client.get("/complexes?q=청운").get_data(as_text=True)
    assert "청운아파트" in html and 'href="/complexes/A"' in html and "무교타워" not in html


def test_complexes_bad_region(client, seeded):
    html = client.get("/complexes?region=1").get_data(as_text=True)
    assert "지역 코드는" in html


def test_complex_page(client, seeded):
    html = client.get("/complexes/A").get_data(as_text=True)
    assert "청운아파트" in html and "서울특별시 종로구 청운동" in html and 'id="scatter"' in html
    assert client.get("/complexes/ZZZ").status_code == 404
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -k complex -v`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

`web/pages.py`에 추가(import에 `from flask import abort`, `from analytics import queries`, `from analytics.params import BadParam`):
```python
@bp.route("/complexes")
def complexes_page():
    q, region = request.args.get("q", "").strip(), request.args.get("region", "").strip()
    rows, error = [], None
    try:
        with db.connection() as conn:
            version = queries.active_version(conn)
            rows = queries.search_complexes(conn, version, q or None, region or None)
    except (BadParam, queries.NotReady) as e:
        error = str(e)
    return render_template("complexes.html", p=jobs.progress(), rows=rows, q=q, region=region, error=error)


@bp.route("/complexes/<apt_seq>")
def complex_page(apt_seq):
    try:
        with db.connection() as conn:
            detail = queries.complex_detail(conn, queries.active_version(conn), apt_seq)
    except LookupError:
        abort(404)
    return render_template("complex.html", p=jobs.progress(), c=detail["complex"], trades=detail["trades"][:200])
```

`templates/complexes.html`:
```html
{% extends "base.html" %}
{% block title %}단지 · 아파트 실거래가{% endblock %}
{% block body %}
<form class="bar" method="get">
  <input type="text" name="q" value="{{ q }}" placeholder="단지명">
  <input type="text" name="region" value="{{ region }}" placeholder="지역 코드(시도 2·시군구 5·읍면동 8자리)" style="flex:0 1 260px">
  <button class="primary">검색</button>
</form>
{% if error %}<p class="err">{{ error }}</p>{% endif %}
<div class="wrap"><table>
  <thead><tr><th>단지</th><th>지역(최신 경계)</th><th>법정동(신고)</th><th class="num">건축년도</th><th>최근 계약일</th></tr></thead>
  <tbody>
  {% for r in rows %}
    <tr><td><a href="/complexes/{{ r.apt_seq|urlencode }}">{{ r.apt_nm or r.apt_seq }}</a></td>
      <td>{{ r.region_name or '' }}</td><td>{{ r.api_umd_nm or '' }}</td>
      <td class="num">{{ r.build_year or '' }}</td><td>{{ r.last_deal_date or '' }}</td></tr>
  {% else %}
    <tr><td colspan="5" class="muted">검색 결과가 없습니다.</td></tr>
  {% endfor %}
  </tbody>
</table></div>
<p class="meta">최근 거래 순 최대 50개</p>
{% endblock %}
```

`templates/complex.html`:
```html
{% extends "base.html" %}
{% block title %}{{ c.apt_nm }} · 아파트 실거래가{% endblock %}
{% block body %}
<h2 style="margin-top:8px">{{ c.apt_nm }}</h2>
<p class="meta">{{ c.region_name or '지역 미판정' }} · 신고 법정동 {{ c.api_umd_nm or '-' }} · 지번 {{ c.jibun or '-' }} · {{ c.road_nm or '' }}
  · 건축 {{ c.build_year or '-' }}년 · 좌표
  {% if c.lon is not none %}{{ '%.5f'|format(c.lon) }}, {{ '%.5f'|format(c.lat) }} ({{ c.geocode_status }}{% if c.region_match %}, {{ c.region_match }}{% endif %}){% else %}없음 ({{ c.geocode_status }}){% endif %}
  {% if c.sgg_mismatch %}<span class="warn">· 신고 시군구와 좌표 시군구가 다름</span>{% endif %}</p>
<p id="msg" class="meta"></p>
<h2>거래 가격 (해제 제외)</h2>
<div id="scatter" class="chart"></div>
<div class="cols" style="margin-top:12px">
  <div><h2>면적 구간별 거래 수</h2><div id="areas" class="chart small"></div></div>
  <div><h2>층별 거래 수</h2><div id="floors" class="chart small"></div></div>
</div>
<h2>최근 거래 {{ trades|length }}건</h2>
<div class="wrap"><table>
  <thead><tr><th>계약일</th><th class="num">거래금액(만원)</th><th class="num">전용(㎡)</th><th class="num">㎡당(만원)</th><th class="num">층</th><th>동</th><th>거래유형</th></tr></thead>
  <tbody>
  {% for t in trades %}
    <tr class="{{ 'cancel' if t.is_cancelled }}"><td>{{ t.deal_date or '' }}</td><td class="num">{{ t.deal_amount|comma }}</td>
      <td class="num">{{ t.area if t.area is not none else '' }}</td><td class="num">{{ '%.0f'|format(t.ppm2) if t.ppm2 is not none else '' }}</td>
      <td class="num">{{ t.floor if t.floor is not none else '' }}</td><td>{{ t.apt_dong or '' }}</td><td>{{ t.dealing_gbn or '' }}</td></tr>
  {% endfor %}
  </tbody>
</table></div>
{% endblock %}
{% block scripts %}{% include "_charts.html" %}<script>window.APT_SEQ = {{ c.apt_seq|tojson }};</script>
<script src="{{ url_for('static', filename='js/complex.js') }}"></script>{% endblock %}
```

`static/js/complex.js`:
```javascript
// 단지 상세: 계약일 x 가격 산점도(면적 구간 3색), 면적 구간별·층별 거래 수
(async () => {
  const el = (id) => document.getElementById(id);
  const BANDS = [['le60', '60㎡ 이하', (a) => a <= 60], ['60_85', '60~85㎡', (a) => a > 60 && a <= 85], ['gt85', '85㎡ 초과', (a) => a > 85]];
  let data;
  try {
    data = await App.api(`/api/complexes/${encodeURIComponent(window.APT_SEQ)}`);
  } catch (e) { App.message(el('msg'), e.message); return; }
  const trades = data.trades.filter((t) => !t.is_cancelled && t.deal_date && t.deal_amount != null);
  const base = App.baseOption();

  const scatter = App.chart(el('scatter'));
  scatter.setOption({
    ...base,
    tooltip: {
      trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
      formatter: (p) => { const t = p.data.t; return `${t.deal_date}<br><b>${App.fmt.eok(t.deal_amount)}</b> (${App.fmt.int(t.deal_amount)}만원)<br>전용 ${t.area ?? '-'}㎡ · ${t.floor ?? '-'}층${t.apt_dong ? ` · ${App.escapeHtml(t.apt_dong)}동` : ''}`; },
    },
    xAxis: { ...base.xAxis, type: 'time' },
    yAxis: { ...base.yAxis, scale: true, axisLabel: { color: App.css('--muted'), formatter: App.fmt.eok } },
    series: BANDS.map(([, label, test], i) => ({
      name: label, type: 'scatter', symbolSize: 8, color: App.seriesColor(i),
      itemStyle: { borderColor: App.css('--card'), borderWidth: 1 },   // 겹치는 점 구분용 테두리
      data: trades.filter((t) => t.area != null && test(t.area)).map((t) => ({ value: [t.deal_date, t.deal_amount], t })),
    })),
  });

  const bar = (node, labels, counts, unit) => {
    const c = App.chart(node);
    c.setOption({
      ...base, legend: { show: false }, grid: { left: 48, right: 12, top: 16, bottom: 28 },
      xAxis: { ...base.xAxis, data: labels },
      tooltip: { ...base.tooltip, valueFormatter: (v) => `${App.fmt.int(v)}${unit}` },
      series: [{ name: '거래 수', type: 'bar', data: counts, color: App.seriesColor(0), barMaxWidth: 28, itemStyle: { borderRadius: [4, 4, 0, 0] } }],
    });
  };
  bar(el('areas'), BANDS.map((b) => b[1]), BANDS.map(([, , test]) => trades.filter((t) => t.area != null && test(t.area)).length), '건');
  const FLOORS = [['지하', (f) => f < 1], ['1~5', (f) => f >= 1 && f <= 5], ['6~10', (f) => f >= 6 && f <= 10], ['11~15', (f) => f >= 11 && f <= 15], ['16~20', (f) => f >= 16 && f <= 20], ['21+', (f) => f >= 21]];
  bar(el('floors'), FLOORS.map((f) => f[0]), FLOORS.map(([, test]) => trades.filter((t) => t.floor != null && test(t.floor)).length), '건');
})();
```

- [ ] **Step 4: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/ -v`
Expected: PASS (전체)

- [ ] **Step 5: 화면 확인**

`/complexes?q=단지`에서 목록 → 아무 단지 상세: 산점도 3색(범례 3개)·툴팁, 면적·층 막대, 거래 표(해제 거래는 취소선), 콘솔 오류 없음, 어두운 테마. 스크린샷을 남긴다.

- [ ] **Step 6: Commit**

```bash
git add templates/complexes.html templates/complex.html static/js/complex.js web/pages.py tests/test_pages.py
git commit -m "단지 검색·상세 화면 추가(거래 산점도·면적·층 분포)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 추출 화면, 수집 현황의 집계 표시, 문서

**Files:**
- Create: `templates/export.html`, `static/js/export.js`
- Modify: `web/pages.py`, `templates/status.html`, `README.md`, `tests/test_pages.py`

**Interfaces:**
- Consumes: Task 1 `aggregates.status`, Task 3 추출 라우트, Task 2 `/api/regions`
- Produces: 라우트 `pages.export_page`(`/export`)

- [ ] **Step 1: 테스트 작성**

`tests/test_pages.py`에 추가:
```python
def test_export_page(client, seeded):
    html = client.get("/export").get_data(as_text=True)
    assert 'id="export-form"' in html and "/export/codebook.csv" in html


def test_status_shows_aggregates(client, seeded):
    html = client.get("/status").get_data(as_text=True)
    assert "집계 대기 계약월" in html
```

- [ ] **Step 2: 테스트 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -k "export_page or status_shows" -v`
Expected: FAIL

- [ ] **Step 3: 구현**

`web/pages.py`에 추가하고 `status()`의 `with db.connection() as conn:` 블록 안에 `agg = aggregates.status(conn)`을 넣어 `render_template(..., agg=agg)`로 넘긴다(import에 `from analytics import aggregates, params, queries`):
```python
@bp.route("/export")
def export_page():
    return render_template("export.html", p=jobs.progress(), bands=params.BANDS)
```

`templates/export.html`:
```html
{% extends "base.html" %}
{% block title %}추출 · 아파트 실거래가{% endblock %}
{% block body %}
<form id="export-form" method="get" action="/export.csv">
  <div class="bar">
    <select name="target" aria-label="대상"><option value="raw">원본 거래</option><option value="agg">월별 집계</option></select>
    <select id="format" aria-label="형식"><option value="csv">CSV (엑셀·Stata)</option><option value="parquet">Parquet (Python·R)</option></select>
    <select name="band" aria-label="면적 구간">{% for v, l in bands %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select>
    <label class="muted"><input type="checkbox" name="cancelled" value="1"> 해제 거래 포함(원본)</label>
  </div>
  <div class="bar">
    <select id="sel-sido" aria-label="시도"></select><select id="sel-sgg" aria-label="시군구"></select><select id="sel-umd" aria-label="읍면동"></select>
    <input type="hidden" name="region" id="region">
    <input type="month" id="from" aria-label="시작월"> ~ <input type="month" id="to" aria-label="종료월">
    <input type="hidden" name="from" id="from-v"><input type="hidden" name="to" id="to-v">
    <button class="primary">내려받기</button>
  </div>
</form>
<p id="msg" class="meta"></p>
<ul class="notes">
  <li>원본은 한 번에 최대 60개월까지 받을 수 있습니다. 전국 전체 기간은 연도별로 나눠 받으세요.</li>
  <li>지역은 최신 경계 기준입니다(단지 좌표로 판정, 좌표가 없으면 시군구는 신고 코드). 지역을 고르지 않으면 전국입니다.</li>
  <li>원본의 열 이름은 국토부 API 필드명입니다. <a href="/export/codebook.csv">코드북(열 설명) 내려받기</a></li>
  <li>CSV는 UTF-8(BOM 포함)이라 엑셀에서 한글이 깨지지 않습니다. Parquet는 숫자·날짜 형식이 보존되고 크기가 작습니다.</li>
</ul>
{% endblock %}
{% block scripts %}<script src="{{ url_for('static', filename='js/common.js') }}"></script>
<script src="{{ url_for('static', filename='js/export.js') }}"></script>{% endblock %}
```

`static/js/export.js`:
```javascript
// 추출 폼: 지역 선택(시도→시군구→읍면동), 기간(월), 형식에 따라 /export.csv 또는 /export.parquet
(async () => {
  const el = (id) => document.getElementById(id);
  const form = el('export-form');
  async function fill(sel, level, parent, placeholder) {
    sel.innerHTML = '';
    sel.add(new Option(placeholder, ''));
    if (level !== 'sido' && !parent) return;
    try {
      const data = await App.api('/api/regions', { level, parent });
      for (const r of data.regions) sel.add(new Option(r.name, r.region_cd));
    } catch (e) { App.message(el('msg'), e.message); }
  }
  await fill(el('sel-sido'), 'sido', null, '전국');
  await fill(el('sel-sgg'), 'sgg', null, '시군구 전체');
  await fill(el('sel-umd'), 'umd', null, '읍면동 전체');
  el('sel-sido').onchange = () => { fill(el('sel-sgg'), 'sgg', el('sel-sido').value, '시군구 전체'); fill(el('sel-umd'), 'umd', null, '읍면동 전체'); };
  el('sel-sgg').onchange = () => fill(el('sel-umd'), 'umd', el('sel-sgg').value, '읍면동 전체');

  const now = new Date();
  const thisYm = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}`;
  el('to').value = App.toMonthInput(thisYm);
  el('from').value = App.toMonthInput(App.shiftYm(thisYm, -11));

  form.onsubmit = () => {
    const umd = el('sel-umd').value, sgg = el('sel-sgg').value, sido = el('sel-sido').value;
    el('region').value = umd ? `umd:${umd}` : sgg ? `sgg:${sgg}` : sido ? `sido:${sido}` : '';
    el('region').disabled = !el('region').value;
    el('from-v').value = App.fromMonthInput(el('from').value);
    el('to-v').value = App.fromMonthInput(el('to').value);
    form.action = el('format').value === 'parquet' ? '/export.parquet' : '/export.csv';
    App.message(el('msg'), '파일을 만드는 중입니다. 범위가 크면 시간이 걸립니다.', 'muted');
    return true;
  };
})();
```

`templates/status.html`의 `<h2>단지 좌표·지역 판정</h2>` 줄 바로 앞에 넣는다:
```html
<h2>월별 집계</h2>
<div class="tiles">
  <div class="tile"><div class="k">집계 대기 계약월</div><div class="v {{ 'warn' if agg.dirty > 24 }}">{{ agg.dirty|comma }}</div></div>
  <div class="tile"><div class="k">집계 행 수</div><div class="v">{{ agg.rows|comma }}</div></div>
  <div class="tile"><div class="k">집계된 최근 계약월</div><div class="v">{{ agg.latest_ym or '-' }}</div></div>
</div>
```

- [ ] **Step 4: README 갱신**

`README.md`의 `## 엔드포인트` 표를 다음으로 교체:
```markdown
| 경로 | 설명 |
|---|---|
| `/` | 대시보드: 전국 요약, 24개월 거래량·중위가, 전년 대비 상승·하락 시군구 |
| `/trends` | 추이: 지역 최대 8개 비교, 지표·면적·기간, 이동평균·지수, 표·PNG |
| `/map` | 지도: 시도 → 시군구 → 읍면동 단계구분도, 순위표 |
| `/complexes`, `/complexes/<aptSeq>` | 단지 검색·상세(거래 산점도, 면적·층 분포) |
| `/export` | 원본·월별 집계 CSV / Parquet 추출, 코드북 |
| `/trades` | 거래 목록(시도·시군구·연도·계약월·검색 필터) |
| `/status` | 수집 현황·누락 점검·좌표·집계 현황 |
| `/api/regions`, `/api/agg`, `/api/map`, `/api/summary`, `/api/complexes` | 분석 JSON |
| `/export.csv`, `/export.parquet`, `/export/codebook.csv` | 추출 파일 (`/download.csv`는 `/export.csv`로 이동) |
| `/api/trades`, `/api/status`, `/api/quality` | 기존 JSON |

집계 규칙: 해제 거래·금액 없는 거래 제외, 완전 중복 1건, 지역은 최신 경계(단지 좌표 판정) 기준, 최근 2개월은 잠정.
```
`## 로컬 실행` 절 끝에 추가:
````markdown
화면을 가짜 데이터로 확인하려면(로컬 DB 전용):
```bash
.venv/Scripts/python scripts/seed_dev.py --reset
```
````

- [ ] **Step 5: 테스트 통과 확인**

Run: `.venv/Scripts/python -m pytest -v`
Expected: PASS (전체)

- [ ] **Step 6: 화면 확인**

`/export`에서 서울특별시 → 종로구, 최근 12개월, CSV로 내려받기 → 파일이 내려오는지(read_network_requests로 200·`Content-Disposition` 확인), Parquet·월별 집계도 같은 방식, 61개월 원본 요청은 400 메시지. `/status`에 월별 집계 타일. 스크린샷을 남긴다.

- [ ] **Step 7: Commit**

```bash
git add templates/export.html static/js/export.js templates/status.html web/pages.py README.md tests/test_pages.py
git commit -m "추출 화면과 수집 현황의 집계 표시 추가, README 갱신

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

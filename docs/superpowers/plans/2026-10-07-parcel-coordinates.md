# 연속지적도 단지 좌표·법정동 코드 판정·단지 위치 지도 구현 계획

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 연속지적도(CC BY)로 단지 좌표를 만드는 로컬 도구, 좌표가 없을 때의 법정동 코드 판정, 지도 읍면동 단계·단지 상세의 단지 위치 점 표시를 구현하고 위치정보요약DB 관련 코드를 지운다.

**Architecture:**
- 좌표는 이 PC의 로컬 도구(`geo/parcel_points.py`, geopandas)가 만들어 `complexes.lon/lat`에 넣는다. 서버는 geopandas 없이 동작한다.
- 지리 처리(`geo/pipeline.py`)는 좌표가 있으면 경계로, 없으면 `geo/code_map.csv`로 바꾼 법정동 코드로 판정한다.
- 화면은 ECharts `geo` 컴포넌트 위에 단계구분도 색과 `scatter` 점을 함께 그린다.

**Tech Stack:** Python 3.14, Flask, psycopg3, Postgres, shapely(서버), geopandas·pyogrio·pyproj(로컬 도구·테스트), ECharts 5.6, vanilla JS, pytest

**Spec:** `docs/superpowers/specs/2026-10-07-parcel-coordinates-design.md`

## Global Constraints
- 화면·메시지·주석·커밋 메시지는 한국어. 커밋 메시지 끝에 `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 서버 코드(`app.py`가 불러오는 모듈, `geo/pipeline.py`·`geo/assign.py`·`geo/versions.py`·`geo/code_map.py`·`analytics/*`·`web/*`)는 geopandas·pyogrio·pyproj를 불러오지 않는다(`requirements.txt`에 없음). 로컬 도구와 테스트만 쓴다.
- 화면 글자에 좌표 숫자(경도·위도)를 표시하지 않는다. 지도 점을 그리기 위한 API 응답에는 좌표가 들어가도 된다.
- PNU 19자리 = `sgg_cd`(5) + `umd_cd`(5) + 대지구분(1: `land_cd`가 `'2'`면 `'2'`, 그 밖은 `'1'`) + 본번(4자리 0채움) + 부번(4자리 0채움, 비면 `0000`).
- 코드 판정 읍면동 = `api_sgg_cd + api_umd_cd[:3]`를 `geo/code_map.csv`(`old_emd_cd → new_emd_cd`)로 바꾼 값이 활성 경계의 `regions`(level=umd)에 있을 때만.
- `region_match` 값: `within` / `nearest` / `code` / `none`.
- 출처 문구: `단지 위치: 국토교통부 연속지적도형정보(CC BY)`.
- 로컬 도구는 로컬이 아닌 DB에 `--yes` 없이 쓰지 않는다. 시작할 때 `대상 DB: host:port/dbname`을 출력한다(비밀번호 없음).
- 테스트는 `.venv/Scripts/python -m pytest`로 돌린다(로컬 Postgres `TEST_DATABASE_URL`). 전체 테스트는 몇 분 걸리므로 백그라운드로 돌려도 된다.
- 파일은 UTF-8(BOM 없음)로 쓴다. 기존 파일의 줄바꿈을 바꾸지 않는다.

## Review Focus
- 본번·부번이 비거나 `0`, 5자리 이상, 숫자가 아닌 거래 → PNU를 만들지 않고 오류 없이 넘어간다(Task 1 테스트).
- 한 PNU가 여러 조각(다른 행)으로 들어 있는 필지 → 합쳐서 대표점 하나, 대표점은 필지 안(Task 4 테스트).
- 도구가 계산하는 동안 수집기가 단지를 새로 넣거나 사람이 수동 좌표를 넣음 → 저장할 때 상태가 바뀐 단지는 건너뛴다(Task 4 테스트).
- 좌표 있는 단지가 하나도 없는 시군구의 읍면동 지도 → 점 API가 빈 목록을 돌려주고 화면이 오류 없이 경계만 그린다(Task 5 테스트 + Task 6 확인).
- 경계 버전은 그대로이고 `code_map.csv`만 바뀐 배포 → 코드 판정 단지가 다시 판정된다(Task 3 테스트).

---

## 파일 구조
| 파일 | 책임 |
|---|---|
| `geo/keys.py` (수정) | `pnu()` 추가(SQL `pnu()`와 같은 규칙) |
| `migrations/005_pnu.sql` (새) | SQL `pnu()`, `complexes(boundary_version)` 인덱스 |
| `migrations/006_drop_address_points.sql` (새) | `address_points` 테이블 삭제 |
| `geo/code_map.py` (새) | 대응표 읽기·해시(표준 라이브러리만) |
| `geo/assign.py` (수정) | 경계 판정 + 코드 판정 규칙, 판정 대상 계산·반영 |
| `geo/versions.py` (수정) | 경계 전환 시 같은 규칙으로 전체 재판정 |
| `geo/pipeline.py` (수정) | 좌표 연결 단계 삭제, 대응표 변경 확인 단계 추가 |
| `geo/parcel_points.py` (새) | [로컬] 연속지적도 → 단지 좌표 저장 도구 |
| `geo/address_points.py`, `geo/locate.py`, `tests/test_geo_locate.py` (삭제) | 위치정보요약DB |
| `geo/complexes.py` (수정) | `summary()` 집계 항목 |
| `analytics/queries.py` (수정) | `complex_points()`, `nearby()`, `_COMPLEX_SELECT` |
| `web/api.py` (수정) | `/api/map/complexes`, `/api/complexes/<seq>/nearby` |
| `static/js/map.js`, `templates/map.html` (수정) | geo 컴포넌트 + 단지 점, "단지 표시" 알약 |
| `static/js/complex.js`, `templates/complex.html` (수정) | 위치 카드, 배지 |
| `templates/status.html`, `templates/base.html`, `analytics/export.py` (수정) | 집계 표시·출처·코드북 |
| `docs/runbooks/geo-data.md`, `README.md`, `scripts/seed_dev.py` (수정) | 문서·개발 데이터 |

---

### Task 1: PNU 키 (Python·SQL)

**Files:**
- Modify: `geo/keys.py`
- Create: `migrations/005_pnu.sql`
- Test: `tests/test_geo_pnu.py` (새)

**Interfaces:**
- Produces: `geo.keys.pnu(sgg, umd, land, bon, bu) -> str | None`. SQL 함수 `pnu(sgg TEXT, umd TEXT, land TEXT, bon TEXT, bu TEXT) RETURNS TEXT`(IMMUTABLE). 인덱스 `ix_complexes_version ON complexes (boundary_version)`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_geo_pnu.py`

```python
import pytest

from geo.keys import pnu

CASES = [
    (("11110", "10100", "1", "0001", "0000"), "1111010100100010000"),
    (("11110", "10100", "1", "12", ""), "1111010100100120000"),
    (("11110", "10100", "2", "0071", "0003"), "1111010100200710003"),     # 산
    ((" 11110 ", "10100", None, "71", "3"), "1111010100100710003"),        # 공백·대지구분 없음 → 일반
    (("11110", "10100", "1", "00071", "00003"), "1111010100100710003"),    # 5자리 0채움 입력
    (("11110", "10100", "1", "0", "0"), None),                            # 본번 0
    (("11110", "10100", "1", "", "0"), None),                             # 본번 없음
    (("11110", "10100", "1", "abc", "0"), None),
    (("11110", "10100", "1", "10000", "0"), None),                        # 본번 4자리 초과
    (("11110", "10100", "1", "1", "x"), None),                            # 부번 숫자 아님
    (("11110", "10100", "1", "1", "10000"), None),                        # 부번 4자리 초과
    (("1111", "10100", "1", "1", "0"), None),                             # 시군구 자릿수
    (("11110", "101", "1", "1", "0"), None),                              # 법정동 자릿수
    ((None, None, None, None, None), None),
]


@pytest.mark.parametrize("args, expected", CASES)
def test_pnu(args, expected):
    assert pnu(*args) == expected


def test_sql_pnu_matches_python(pg):
    with pg.connection() as conn:
        for args, expected in CASES:
            got = conn.execute("SELECT pnu(%s, %s, %s, %s, %s) AS p", args).fetchone()["p"]
            assert got == expected, args
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_pnu.py -q`
Expected: FAIL (`ImportError: cannot import name 'pnu'`)

- [ ] **Step 3: 구현** — `geo/keys.py` 첫 줄 문서 문자열을 바꾸고 끝에 함수를 추가한다.

```python
"""도로명주소 키·필지고유번호(PNU). migrations의 road_key()·pnu()와 같은 규칙이어야 한다."""
```

```python
def _num4(v):
    """0채움 포함 1~9자리 숫자이고 값이 0~9999면 int, 아니면 None."""
    return int(v) if _DIGITS.fullmatch(v) and int(v) <= 9999 else None


def pnu(sgg, umd, land, bon, bu):
    """필지고유번호 19자리 = 시군구5 + 법정동5 + 대지구분1(산 2, 그 밖 1) + 본번4 + 부번4. 형식이 안 맞으면 None."""
    sgg, umd, land, bon, bu = (str(v if v is not None else "").strip(" ") for v in (sgg, umd, land, bon, bu))
    if not (_D5.fullmatch(sgg) and _D5.fullmatch(umd)):
        return None
    main = _num4(bon)
    sub = 0 if bu == "" else _num4(bu)
    if not main or sub is None:
        return None
    return f"{sgg}{umd}{'2' if land == '2' else '1'}{main:04d}{sub:04d}"
```

`migrations/005_pnu.sql`:

```sql
-- 필지고유번호(PNU) 19자리: 시군구5 + 법정동5 + 대지구분1(산 2, 그 밖 1) + 본번4 + 부번4. geo/keys.py pnu()와 같은 규칙.
-- 숫자 변환은 형식 검사를 통과한 뒤에만 하도록 CASE를 겹쳐 쓴다(AND는 평가 순서를 보장하지 않는다).
CREATE FUNCTION pnu(sgg TEXT, umd TEXT, land TEXT, bon TEXT, bu TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN s ~ '^[0-9]{5}$' AND u ~ '^[0-9]{5}$' AND b ~ '^[0-9]{1,9}$' AND (x = '' OR x ~ '^[0-9]{1,9}$') THEN
            CASE WHEN b::bigint BETWEEN 1 AND 9999
                  AND (CASE WHEN x = '' THEN 0 ELSE x::bigint END) <= 9999
                 THEN s || u || CASE WHEN l = '2' THEN '2' ELSE '1' END
                      || lpad((b::bigint)::text, 4, '0')
                      || lpad((CASE WHEN x = '' THEN 0 ELSE x::bigint END)::text, 4, '0')
            END
    END
      FROM (SELECT btrim(COALESCE(sgg, '')) AS s, btrim(COALESCE(umd, '')) AS u,
                   btrim(COALESCE(land, '')) AS l, btrim(COALESCE(bon, '')) AS b,
                   btrim(COALESCE(bu, '')) AS x) v
$$;

-- 판정 대상(boundary_version IS DISTINCT FROM 활성 버전) 조회용
CREATE INDEX ix_complexes_version ON complexes (boundary_version);

COMMENT ON COLUMN complexes.region_match IS 'within / nearest / code / none';
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_pnu.py -q`
Expected: PASS (15 passed)

- [ ] **Step 5: 커밋**

```bash
git add geo/keys.py migrations/005_pnu.sql tests/test_geo_pnu.py
git commit -m "필지고유번호(PNU) 키 함수(Python·SQL)와 판정 대상 인덱스 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 위치정보요약DB 코드 정리

**Files:**
- Delete: `geo/address_points.py`, `geo/locate.py`, `tests/test_geo_locate.py`
- Create: `migrations/006_drop_address_points.sql`
- Modify: `geo/pipeline.py`, `geo/complexes.py:96-116`(summary), `templates/status.html:140-141`, `scripts/seed_dev.py:97`, `tests/test_geo_lock.py`, `tests/test_geo_versions.py`(test_pipeline_run_end_to_end)

**Interfaces:**
- Produces: `pipeline.state["last_result"]` 키가 `switched, added, assigned`로 바뀐다(`located`·`failed` 없음). `complexes.summary()`에서 `address_points`·`address_month` 키가 없어진다.

- [ ] **Step 1: 테스트부터 고친다**

`tests/test_geo_lock.py`:
- import 줄을 `from geo import assign, complexes, hooks, pipeline, versions`로 바꾼다(`locate` 삭제).
- `test_locate_pending_takes_lock`, `test_address_points_load_takes_lock` 두 함수를 지운다.

`tests/test_geo_versions.py`의 `test_pipeline_run_end_to_end`를 다음으로 바꾼다(좌표는 수동 입력으로 넣는다):

```python
def test_pipeline_run_end_to_end(pg, tmp_path, monkeypatch):
    from collector import store
    from geo import complexes
    from tests.helpers import add_job, item

    make_version(tmp_path, "2026-10")
    ran = []
    pipeline.AFTER_RUN.append(lambda conn: ran.append(True))
    with pg.connection() as conn:
        add_job(conn, "11110", "202601")
        monkeypatch.setattr(store, "AFTER_SAVE", [])        # 단지는 pipeline의 bootstrap이 만든다
        store.save_job(conn, "11110", "202601", [item(aptSeq="A")], 1)
    pipeline.run()                                          # 단지 생성
    with pg.connection() as conn:
        complexes.set_manual(conn, "A", 126.955, 37.575)
    pipeline.run()                                          # 좌표로 판정
    with pg.connection() as conn:
        a = conn.execute("SELECT * FROM complexes").fetchone()
    assert (a["geocode_status"], a["region_umd_cd"], a["region_match"]) == ("manual", "11110101", "within")
    assert pipeline.state["last_error"] is None
    assert pipeline.state["last_result"]["assigned"] == 1
    assert "located" not in pipeline.state["last_result"]
    assert ran == [True, True]
```

`tests/test_geo_web.py`에 추가:

```python
def test_summary_has_no_address_points(pg, seeded):
    with pg.connection() as conn:
        s = complexes.summary(conn)
        assert "address_points" not in s
        assert conn.execute("SELECT to_regclass('address_points') AS t").fetchone()["t"] is None
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_versions.py tests/test_geo_web.py tests/test_geo_lock.py -q`
Expected: FAIL (`test_pipeline_run_end_to_end`: located 키 있음, `test_summary_has_no_address_points`: 테이블 있음)

- [ ] **Step 3: 구현**

```bash
git rm geo/address_points.py geo/locate.py tests/test_geo_locate.py
```

`migrations/006_drop_address_points.sql`:

```sql
-- 위치정보요약DB(주소정보누리집)는 국외 반출 금지 조건 때문에 쓰지 않는다. 단지 좌표는 연속지적도로 만든다.
DROP TABLE IF EXISTS address_points;
```

`geo/pipeline.py`:
- 모듈 문서 문자열 둘째 줄을 `경계 버전 동기화 → (처음 한 번) 기존 거래로 단지 만들기 → 지역 판정 → AFTER_RUN(집계 등)`으로 바꾼다.
- import를 `from geo import assign, complexes, versions`로 바꾼다.
- `run()` 안을 다음으로 바꾼다.

```python
        with db.connection() as conn:
            switched = _sync(conn)
            added = _bootstrap(conn)
            version = versions.active(conn)
            assigned = assign.assign_pending(conn, version) if version else 0
            for step in AFTER_RUN:
                step(conn)
        state.update(last_run=settings.now_str(), last_error=None, last_result=dict(
            switched=switched, added=added, assigned=assigned))
```

`geo/complexes.py` `summary()`에서 `points = ...address_points...` 줄을 지우고, return의 `address_points=points["n"], address_month=points["month"],`를 지운다.

`templates/status.html` 140~141줄의 문장을 다음으로 바꾼다.

```html
<p class="meta">활성 경계 {{ g.boundary_version or '없음' }} · 마지막 처리 {{ g.pipeline.last_run or '-' }}
```

`scripts/seed_dev.py` 97줄 TRUNCATE 목록에서 `address_points, `를 지운다.

`grep -rn "address_points\|geo.locate\|from geo import.*locate" --include=*.py --include=*.html .`(`.venv` 제외)로 남은 참조가 없는지 확인한다. `docs/`·`README.md`는 Task 8에서 고친다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_versions.py tests/test_geo_web.py tests/test_geo_lock.py tests/test_geo_assign.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add -A geo migrations/006_drop_address_points.sql templates/status.html scripts/seed_dev.py tests
git commit -m "위치정보요약DB 적재 도구·좌표 연결·테이블 삭제(국외 반출 금지 조건)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 법정동 코드 판정과 대응표 변경 감지

**Files:**
- Create: `geo/code_map.py`
- Modify: `geo/assign.py`, `geo/versions.py:51-82`(switch), `geo/pipeline.py`
- Test: `tests/test_geo_assign.py`, `tests/test_geo_versions.py`

**Interfaces:**
- Consumes: Task 2의 `pipeline.run()` 구조.
- Produces:
  - `geo.code_map.read(path=None) -> dict[str, str]`, `geo.code_map.digest(path=None) -> str`, `geo.code_map.PATH`
  - `assign.code_region(sgg, umd, valid: set[str], mapping: dict) -> str | None`
  - `assign.assign_row(boundary, row, version, valid=frozenset(), mapping=None) -> dict`(키: `apt_seq, umd, sgg, match, version, lon, lat, mismatch`). `boundary`는 None이어도 된다.
  - `assign.compute(conn, version, boundary=None, data_dir=None, pending_only=True) -> list[dict]`
  - `assign.compute_pending(conn, version, boundary=None)`(= `compute`), `assign.apply_results(conn, results)`, `assign.assign_pending(conn, version, boundary=None) -> int`
  - `pipeline.check_code_map(conn) -> bool`(대응표가 바뀌어 재판정을 걸었으면 True), `pipeline.state["code_map_reset"]`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_geo_assign.py`의 `seed_complexes`와 `test_assign_pending`, `test_null_coords_with_ok_status_skipped`를 다음으로 바꾸고, 아래 테스트들을 추가한다. 파일 위쪽 import에 `from geo import assign, code_map, hooks`를 쓴다.

```python
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
```

`test_stale_result_not_applied`는 그대로 둔다(좌표가 바뀐 단지는 반영하지 않는다).

`tests/test_geo_versions.py`에 추가(파일 위쪽 `setup` 픽스처에 `monkeypatch.setattr(code_map, "PATH", tmp_path / "code_map.csv")`를 넣고 import에 `code_map`을 추가한다).

```python
def test_switch_reassigns_code_complexes(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    with pg.connection() as conn:
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, api_umd_cd, geocode_status) VALUES
            ('K', '11110', '10100', 'pending')""")
        versions.sync(conn)
        k = conn.execute("SELECT * FROM complexes").fetchone()
        assert (k["region_umd_cd"], k["region_match"], k["boundary_version"]) == ("11110101", "code", "2026-10")
        make_version(tmp_path, "2027-01", {"11110103": (126.95, 37.57, 126.96, 37.58)})
        versions.sync(conn)
        k = conn.execute("SELECT * FROM complexes").fetchone()
    assert (k["region_umd_cd"], k["region_match"], k["boundary_version"]) == (None, "none", "2027-01")


def test_pipeline_reassigns_when_code_map_changes(pg, tmp_path):
    make_version(tmp_path, "2026-10")
    (tmp_path / "code_map.csv").write_text("old_emd_cd,new_emd_cd\n", encoding="utf-8")
    with pg.connection() as conn:
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, api_umd_cd, geocode_status) VALUES
            ('K', '11999', '10100', 'pending')""")
    pipeline.run()
    with pg.connection() as conn:
        assert conn.execute("SELECT region_match FROM complexes").fetchone()["region_match"] == "none"
    pipeline.run()                                             # 대응표 그대로 → 재판정 없음
    assert pipeline.state["code_map_reset"] is False
    (tmp_path / "code_map.csv").write_text("old_emd_cd,new_emd_cd\n11999101,11140101\n", encoding="utf-8")
    pipeline.run()
    assert pipeline.state["code_map_reset"] is True
    with pg.connection() as conn:
        k = conn.execute("SELECT * FROM complexes").fetchone()
        flags = [r["name"] for r in conn.execute("SELECT name FROM app_flags WHERE name LIKE 'code_map:%'")]
    assert (k["region_umd_cd"], k["region_match"]) == ("11140101", "code")
    assert len(flags) == 1
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_assign.py tests/test_geo_versions.py -q`
Expected: FAIL (`ModuleNotFoundError: geo.code_map` 등)

- [ ] **Step 3: 구현**

`geo/code_map.py`(새):

```python
"""읍면동 코드 개편 대응표(geo/code_map.csv: old_emd_cd,new_emd_cd[,old_name,new_name]).

서버(지리 처리)도 쓰므로 표준 라이브러리만 쓴다. geo/boundaries.py(로컬 경계 도구)도 같은 파일을 읽는다.
"""
import csv
import hashlib

import settings

PATH = settings.BASE_DIR / "geo" / "code_map.csv"


def read(path=None):
    """옛 읍면동 코드 8자리 → 새 코드 8자리. 파일이 없으면 빈 dict."""
    p = path or PATH
    if not p.exists():
        return {}
    with open(p, encoding="utf-8-sig", newline="") as f:
        out = {}
        for r in csv.DictReader(f):
            old, new = (r.get("old_emd_cd") or "").strip(), (r.get("new_emd_cd") or "").strip()
            if old and new:
                out[old.zfill(8)] = new.zfill(8)
        return out


def digest(path=None):
    """파일 내용이 바뀌었는지 보는 짧은 해시. 파일이 없으면 'none'."""
    p = path or PATH
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p.exists() else "none"
```

`geo/assign.py`:
- 모듈 문서 문자열을 다음으로 바꾼다.

```python
"""단지가 최신 읍면동 중 어디에 속하는지 정한다.

좌표(ok·manual)가 있으면 경계(shapely STRtree)로 판정하고(within / nearest), 좌표가 없거나 경계 밖이면
거래의 법정동 코드(api_sgg_cd + api_umd_cd 앞 3자리)를 대응표(geo/code_map.csv)로 바꿔 판정한다(code / none).
geo_data/{version}/umd_assign.geojson.gz는 좌표 있는 단지를 판정할 때만 읽고, 끝나면 버린다(서버 메모리 절약).
"""
```

- import에 `import re`와 `from geo import code_map, complexes, hooks`를 쓴다.
- `NEAREST_M` 아래에 상수를 추가한다.

```python
LOCATED = ("ok", "manual")
_D5 = re.compile(r"[0-9]{5}")
_D3 = re.compile(r"[0-9]{3}")
```

- `assign_row`부터 파일 끝까지를 다음으로 바꾼다(`Boundary` 클래스와 `_meters`는 그대로).

```python
def code_region(sgg, umd, valid, mapping):
    """법정동 코드 → 활성 경계의 읍면동 코드(대응표 반영). 형식이 틀리거나 경계에 없으면 None."""
    sgg, umd = (sgg or "").strip(), (umd or "").strip()
    if not (_D5.fullmatch(sgg) and _D3.fullmatch(umd[:3])):
        return None
    emd = sgg + umd[:3]
    emd = mapping.get(emd, emd)
    return emd if emd in valid else None


def assign_row(boundary, row, version, valid=frozenset(), mapping=None):
    lon, lat = row.get("lon"), row.get("lat")
    umd, match, mismatch = None, "none", False
    if (boundary is not None and row.get("geocode_status", "ok") in LOCATED
            and lon is not None and lat is not None):
        x, y = float(lon), float(lat)
        if math.isfinite(x) and math.isfinite(y):
            umd, match = boundary.locate(x, y)
            mismatch = bool(umd and row.get("api_sgg_cd") and umd[:5] != row["api_sgg_cd"])
    if umd is None:
        umd = code_region(row.get("api_sgg_cd"), row.get("api_umd_cd"), valid, mapping or {})
        match = "code" if umd else "none"
    return dict(apt_seq=row["apt_seq"], umd=umd, sgg=umd[:5] if umd else None, match=match,
                version=version, lon=lon, lat=lat, mismatch=mismatch)


# 계산 뒤 좌표가 바뀐 단지는 건드리지 않는다(다음 실행에서 다시 판정)
UPDATE = """
UPDATE complexes SET region_umd_cd = %(umd)s, region_sgg_cd = %(sgg)s, region_match = %(match)s,
       boundary_version = %(version)s, sgg_mismatch = %(mismatch)s
 WHERE apt_seq = %(apt_seq)s
   AND lon IS NOT DISTINCT FROM %(lon)s::float8 AND lat IS NOT DISTINCT FROM %(lat)s::float8
"""


def valid_umd(conn, version):
    return {r["region_cd"] for r in conn.execute(
        "SELECT region_cd FROM regions WHERE boundary_version = %s AND level = 'umd'", (version,))}


def compute(conn, version, boundary=None, data_dir=None, pending_only=True):
    """판정 결과 목록(잠금 없이 계산). pending_only면 이 버전으로 아직 판정하지 않은 단지만."""
    sql = "SELECT apt_seq, lon, lat, api_sgg_cd, api_umd_cd, geocode_status FROM complexes"
    rows = conn.execute(sql + " WHERE boundary_version IS DISTINCT FROM %s", (version,)).fetchall() \
        if pending_only else conn.execute(sql).fetchall()
    if not rows:
        return []
    if boundary is None and any(r["geocode_status"] in LOCATED and r["lon"] is not None and r["lat"] is not None
                                for r in rows):
        boundary = Boundary.load(version, data_dir)
    valid, mapping = valid_umd(conn, version), code_map.read()
    return [assign_row(boundary, r, version, valid, mapping) for r in rows]


def compute_pending(conn, version, boundary=None):
    return compute(conn, version, boundary)


def apply_results(conn, results):
    """판정 결과를 반영한다. 계산 뒤 좌표가 바뀐 단지는 건드리지 않아 다음 실행에서 다시 판정된다."""
    seqs = [r["apt_seq"] for r in results]
    with conn.transaction():
        complexes.lock_complexes(conn)
        for hook in hooks.ON_REGION_CHANGE:   # 바뀌기 전 지역
            hook(conn, seqs)
        with conn.cursor() as cur:
            cur.executemany(UPDATE, results)
        for hook in hooks.ON_REGION_CHANGE:   # 바뀐 뒤 지역
            hook(conn, seqs)
    return len(results)


def assign_pending(conn, version, boundary=None):
    """→ 판정한 단지 수"""
    results = compute_pending(conn, version, boundary)
    return apply_results(conn, results) if results else 0
```

`geo/versions.py` `switch()`:
- 문서 문자열 위 모듈 설명의 "모든 단지를 새 경계로 재판정"은 그대로 둔다.
- `boundary = ...`와 `rows = ...`, `results = [...]` 세 줄을 다음 한 줄로 바꾼다.

```python
    results = assign.compute(conn, version, data_dir=data_dir or assign.GEO_DATA, pending_only=False)
```

- staged_regions 반영 UPDATE의 WHERE를 다음으로 바꾼다.

```python
             WHERE c.apt_seq = s.apt_seq
               AND c.lon IS NOT DISTINCT FROM s.lon AND c.lat IS NOT DISTINCT FROM s.lat""")   # 계산 뒤 좌표가 바뀐 단지는 건너뛰고 다음 판정에 맡긴다
```

`geo/pipeline.py`:
- 문서 문자열 둘째 줄: `경계 버전 동기화 → (처음 한 번) 기존 거래로 단지 만들기 → 대응표 변경 확인 → 지역 판정 → AFTER_RUN(집계 등)`
- import에 `code_map`을 추가한다: `from geo import assign, code_map, complexes, versions`
- `state` 초기값에 `"code_map_reset": None`을 추가한다.
- `_bootstrap` 아래에 함수를 추가한다.

```python
def check_code_map(conn):
    """대응표(code_map.csv)가 지난 판정 때와 다르면 코드로 판정한 단지를 다시 판정하게 한다. → 재판정을 걸었으면 True"""
    flag = f"code_map:{code_map.digest()}"
    if conn.execute("SELECT 1 FROM app_flags WHERE name = %s", (flag,)).fetchone():
        return False
    with conn.transaction():
        complexes.lock_complexes(conn)
        conn.execute("UPDATE complexes SET boundary_version = NULL WHERE region_match IN ('code', 'none')")
        conn.execute("DELETE FROM app_flags WHERE name LIKE 'code_map:%'")
        conn.execute("INSERT INTO app_flags (name, set_at) VALUES (%s, %s)", (flag, settings.now_ts()))
    return True
```

- `run()`의 `added = _bootstrap(conn)` 다음 줄에 `state["code_map_reset"] = check_code_map(conn)`를 넣는다.

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_assign.py tests/test_geo_versions.py tests/test_geo_lock.py tests/test_geo_web.py -q`
Expected: PASS

그다음 전체 테스트(분석 집계가 `region_*`를 쓰므로)를 돌린다.

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add geo/code_map.py geo/assign.py geo/versions.py geo/pipeline.py tests/test_geo_assign.py tests/test_geo_versions.py
git commit -m "좌표가 없거나 경계 밖인 단지를 법정동 코드(대응표 반영)로 판정, 대응표 변경 시 재판정

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: 연속지적도 단지 좌표 도구 `geo/parcel_points.py`

**Files:**
- Create: `geo/parcel_points.py`
- Test: `tests/test_geo_parcel_points.py` (새)

**Interfaces:**
- Consumes: SQL `pnu()`(Task 1), `complexes.lock_complexes`, `hooks.ON_REGION_CHANGE`, `wiring.wire()`
- Produces:
  - `targets(conn) -> (dict[apt_seq, pnu], int)`: 대표 PNU, 대상 단지 수(pending+failed)
  - `pnu_column(src) -> str`
  - `read_points(paths, wanted: set[str]) -> (dict[pnu, (lon, lat)], dict)`: 통계 키 `files, parcels, matched, dropped`
  - `save(conn, found: dict[apt_seq, (lon, lat)], target_seqs) -> dict`: 키 `located, failed, skipped`
  - `main(argv=None) -> int`

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_geo_parcel_points.py`

```python
import zipfile
from datetime import datetime

import geopandas as gpd
import pytest
from pyproj import Transformer
from shapely.geometry import Point, Polygon, box

import settings
from collector import store
from geo import hooks, parcel_points
from tests.helpers import add_job, item

X0, Y0 = 197_000, 551_000   # EPSG:5186, 서울 종로 부근


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 7, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])


def make_zip(tmp_path, name, rows, crs="EPSG:5186"):
    """rows: [(pnu, geometry)] → 연속지적도 형식(A0 일련번호, A1 PNU, A2 법정동코드) SHP를 담은 zip"""
    gdf = gpd.GeoDataFrame({"A0": [str(i) for i in range(len(rows))], "A1": [p for p, _ in rows],
                            "A2": [p[:10] for p, _ in rows]}, geometry=[g for _, g in rows], crs=crs)
    d = tmp_path / name
    d.mkdir()
    gdf.to_file(d / f"{name}.shp", encoding="cp949")
    zp = tmp_path / f"{name}.zip"
    with zipfile.ZipFile(zp, "w") as z:
        for f in d.iterdir():
            z.write(f, f.name)
    return zp


L_SHAPE = Polygon([(X0, Y0), (X0 + 100, Y0), (X0 + 100, Y0 + 10), (X0 + 10, Y0 + 10),
                   (X0 + 10, Y0 + 100), (X0, Y0 + 100)])   # 무게중심이 밖에 있는 ㄱ자 필지
P1 = "1111010100100010000"
P2 = "1111010100100020000"
P3 = "1111010100100030000"


def seed(conn):
    add_job(conn, "11110", "202601")
    store.save_job(conn, "11110", "202601", [
        item(aptSeq="A", landCd="1", bonbun="0001", bubun="0000", dealDay="1"),
        item(aptSeq="A", landCd="1", bonbun="0001", bubun="0000", dealDay="2"),
        item(aptSeq="A", landCd="1", bonbun="0009", bubun="0000", dealDay="3"),   # 소수 지번
        item(aptSeq="B", landCd="1", bonbun="0002", bubun="0000"),
        item(aptSeq="C", landCd="1", bonbun="0007", bubun="0000"),               # 필지 없음
        item(aptSeq="M", landCd="1", bonbun="0003", bubun="0000"),               # 수동 좌표 단지
        item(aptSeq="Z", landCd="1", bonbun="", bubun=""),                       # PNU 못 만듦
    ], 7)
    conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, geocode_status) VALUES
        ('A', '11110', 'pending'), ('B', '11110', 'failed'), ('C', '11110', 'pending'),
        ('Z', '11110', 'pending')""")
    conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, lon, lat, geocode_status, geocode_source)
        VALUES ('M', '11110', 127.0, 37.5, 'manual', 'manual')""")


def test_targets_pick_most_common_pnu(pg):
    with pg.connection() as conn:
        seed(conn)
        got, n = parcel_points.targets(conn)
    assert got == {"A": P1, "B": P2, "C": "1111010100100070000"}
    assert n == 4                                              # A·B·C·Z (M은 수동)


def test_read_points(tmp_path):
    z1 = make_zip(tmp_path, "AL_D002_11", [(P1, L_SHAPE), (P2, box(X0 + 200, Y0, X0 + 300, Y0 + 100)),
                                           (P2, box(X0 + 300, Y0, X0 + 400, Y0 + 100)),   # 같은 PNU 두 조각
                                           ("1111010100100990000", box(X0, Y0 + 500, X0 + 10, Y0 + 510))])
    pts, stats = parcel_points.read_points([z1], {P1, P2, P3})
    assert set(pts) == {P1, P2}
    assert stats == {"files": 1, "parcels": 4, "matched": 2, "dropped": 0}
    back = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
    x, y = back.transform(*pts[P1])
    assert L_SHAPE.buffer(0.5).contains(Point(x, y))        # 대표점은 필지 안
    x, y = back.transform(*pts[P2])
    assert X0 + 200 <= x <= X0 + 400                          # 두 조각을 합친 필지 안
    lon, lat = pts[P1]
    assert 126.9 < lon < 127.1 and 37.5 < lat < 37.6
    assert lon == round(lon, 6)


def test_pnu_column_requires_19_digit_field(tmp_path):
    gdf = gpd.GeoDataFrame({"A0": ["1"], "NAME": ["가"]}, geometry=[box(X0, Y0, X0 + 1, Y0 + 1)], crs="EPSG:5186")
    d = tmp_path / "bad"
    d.mkdir()
    gdf.to_file(d / "bad.shp")
    with pytest.raises(ValueError, match="PNU"):
        parcel_points.pnu_column(str(d / "bad.shp"))


def test_save_updates_skips_changed_and_marks_failed(pg):
    seen = []
    hooks.ON_REGION_CHANGE.append(lambda conn, seqs: seen.append(sorted(seqs)))
    with pg.connection() as conn:
        seed(conn)
        conn.execute("UPDATE complexes SET boundary_version = '2026-10', region_umd_cd = '11110101' WHERE apt_seq = 'A'")
        conn.execute("UPDATE complexes SET lon = 126.9, lat = 37.5, geocode_status = 'manual' WHERE apt_seq = 'B'")  # 계산 뒤 수동 입력
        result = parcel_points.save(conn, {"A": (126.966, 37.5585), "B": (126.967, 37.559)}, ["A", "B", "C"])
        rows = {r["apt_seq"]: r for r in conn.execute("SELECT * FROM complexes")}
    assert result == {"located": 1, "failed": 1, "skipped": 1}
    a = rows["A"]
    assert (a["lon"], a["lat"], a["geocode_status"], a["geocode_source"]) == (126.966, 37.5585, "ok", "parcel")
    assert a["boundary_version"] is None and a["region_umd_cd"] is None      # 다음 지리 처리에서 경계로 재판정
    assert rows["B"]["geocode_status"] == "manual" and rows["B"]["lon"] == 126.9
    assert rows["C"]["geocode_status"] == "failed"
    assert rows["Z"]["geocode_status"] == "pending"                          # 대상 목록 밖은 그대로
    assert seen == [["A"], ["A"]]


def test_main_dry_run_and_yes(pg, tmp_path, capsys, monkeypatch):
    import wiring
    monkeypatch.setattr(wiring, "wire", lambda: None)   # 다른 테스트로 훅 설정이 새지 않게
    with pg.connection() as conn:
        seed(conn)
    make_zip(tmp_path, "AL_D002_11", [(P1, L_SHAPE)])
    assert parcel_points.main(["--dir", str(tmp_path), "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "대상 DB: localhost" in out or "대상 DB: 127.0.0.1" in out
    assert "찾음 1" in out and "저장하지 않습니다" in out
    with pg.connection() as conn:
        assert conn.execute("SELECT geocode_status FROM complexes WHERE apt_seq = 'A'").fetchone()["geocode_status"] == "pending"
    assert parcel_points.main(["--dir", str(tmp_path)]) == 0
    with pg.connection() as conn:
        assert conn.execute("SELECT geocode_status FROM complexes WHERE apt_seq = 'A'").fetchone()["geocode_status"] == "ok"
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@db.example.com:5432/x")
    assert parcel_points.main(["--dir", str(tmp_path)]) == 1
    out = capsys.readouterr().out
    assert "--yes" in out and "secret" not in out


def test_main_stops_without_zip(pg, tmp_path, capsys, monkeypatch):
    import wiring
    monkeypatch.setattr(wiring, "wire", lambda: None)
    assert parcel_points.main(["--dir", str(tmp_path)]) == 1
    assert "zip" in capsys.readouterr().out
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_parcel_points.py -q`
Expected: FAIL (`ImportError: cannot import name 'parcel_points'`)

- [ ] **Step 3: 구현** — `geo/parcel_points.py`

```python
"""[로컬 실행] 국토교통부 연속지적도형정보(브이월드, CC BY)로 단지 좌표를 만든다.

사용:
  python -m geo.parcel_points --dir <시도별 AL_D002_*.zip 폴더> [--database-url URL] [--dry-run] [--yes]

- 대상: 좌표가 없는 단지(geocode_status pending·failed). 수동 좌표(manual)와 이미 찾은 단지(ok)는 건드리지 않는다.
- 단지마다 거래에 가장 많이 나온 필지고유번호(PNU)를 고르고, 그 필지 안쪽의 대표점(representative_point)을
  경위도(EPSG:4326, 소수 6자리)로 바꿔 저장한다. 같은 PNU가 여러 조각이면 합친다.
- 저장하면 지역 판정을 지워 다음 지리 처리(10분 주기)가 경계로 다시 판정한다. 필지를 못 찾은 단지는 failed.
- 시작할 때 대상 DB(host:port/dbname)를 출력한다. 로컬이 아닌 DB는 --yes가 있어야 진행한다.
- 이 도구는 마이그레이션을 실행하지 않는다(운영 스키마는 배포된 앱이 관리).
"""
import argparse
import math
import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlparse

SRC_CRS = "EPSG:5186"          # 파일에 .prj가 없을 때
LON_RANGE = (124.0, 132.0)
LAT_RANGE = (33.0, 39.0)
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_PNU = re.compile(r"[0-9]{19}")

TARGETS = """
SELECT DISTINCT ON (k.apt_seq) k.apt_seq, k.p
  FROM (SELECT t.apt_seq, pnu(t.sgg_cd, t.umd_cd, t.land_cd, t.bonbun, t.bubun) AS p, COUNT(*) AS n
          FROM trades t JOIN complexes c ON c.apt_seq = t.apt_seq
         WHERE c.geocode_status IN ('pending', 'failed')
         GROUP BY 1, 2) k
 WHERE k.p IS NOT NULL
 ORDER BY k.apt_seq, k.n DESC, k.p
"""


def targets(conn):
    """→ ({단지: 대표 PNU}, 대상 단지 수). PNU를 만들 수 없는 단지는 dict에 없다."""
    got = {r["apt_seq"]: r["p"] for r in conn.execute(TARGETS)}
    n = conn.execute("SELECT COUNT(*) AS n FROM complexes WHERE geocode_status IN ('pending', 'failed')").fetchone()["n"]
    return got, n


def shp_source(path):
    """zip 안의 .shp를 GDAL 가상 경로로. .shp 파일이면 그대로."""
    path = Path(path)
    if path.suffix.lower() != ".zip":
        return str(path)
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(".shp")]
    if len(names) != 1:
        raise ValueError(f"{path.name}: zip 안에 .shp가 하나여야 합니다({len(names)}개)")
    return f"/vsizip/{path.as_posix()}/{names[0]}"


def pnu_column(src):
    """첫 행들에서 값이 모두 19자리 숫자인 속성 열(연속지적도는 A1)."""
    import pyogrio

    head = pyogrio.read_dataframe(src, read_geometry=False, max_features=50)
    for col in head.columns:
        vals = head[col].dropna().astype(str)
        if len(vals) and vals.map(lambda v: bool(_PNU.fullmatch(v))).all():
            return col
    raise ValueError(f"{src}: PNU(19자리 숫자) 열을 찾지 못했습니다. 연속지적도형정보 파일인지 확인하세요.")


def read_points(paths, wanted):
    """→ ({PNU: (lon, lat)}, 통계). 파일마다 PNU 열만 읽어 거른 뒤 필요한 행의 도형만 읽는다(메모리 절약)."""
    import geopandas as gpd
    import pyogrio

    out, stats = {}, {"files": 0, "parcels": 0, "matched": 0, "dropped": 0}
    for path in paths:
        src = shp_source(path)
        col = pnu_column(src)
        ids = pyogrio.read_dataframe(src, columns=[col], read_geometry=False, fid_as_index=True)
        stats["files"] += 1
        stats["parcels"] += len(ids)
        fids = ids.index[ids[col].isin(wanted)].tolist()
        if not fids:
            continue
        gdf = pyogrio.read_dataframe(src, columns=[col], fids=fids)
        if gdf.crs is None:
            gdf = gdf.set_crs(SRC_CRS)
        merged = gdf.dissolve(by=col)
        pts = gpd.GeoSeries(merged.geometry.representative_point(), crs=gdf.crs).to_crs("EPSG:4326")
        for p, geom in pts.items():
            lon, lat = geom.x, geom.y
            if (math.isfinite(lon) and math.isfinite(lat)
                    and LON_RANGE[0] <= lon <= LON_RANGE[1] and LAT_RANGE[0] <= lat <= LAT_RANGE[1]):
                if p not in out:
                    stats["matched"] += 1
                out[p] = (round(lon, 6), round(lat, 6))
            else:
                stats["dropped"] += 1
    return out, stats


def save(conn, found, target_seqs):
    """found: {단지: (lon, lat)}. 대상(target_seqs) 중 못 찾은 단지는 failed. 저장 시점에 상태가 바뀐 단지는 건너뛴다.
    → {"located", "failed", "skipped"}"""
    import settings
    from geo import complexes, hooks

    with conn.transaction():
        complexes.lock_complexes(conn)
        still = {r["apt_seq"] for r in conn.execute(
            "SELECT apt_seq FROM complexes WHERE apt_seq = ANY(%s) AND geocode_status IN ('pending', 'failed') "
            "FOR UPDATE", (list(target_seqs),))}
        seqs = sorted(s for s in found if s in still)
        if seqs:
            for hook in hooks.ON_REGION_CHANGE:   # 바뀌기 전 지역
                hook(conn, seqs)
            now = settings.now_ts()
            with conn.cursor() as cur:
                cur.executemany("""
                    UPDATE complexes SET lon = %s, lat = %s, geocode_status = 'ok', geocode_source = 'parcel',
                           geocoded_at = %s, region_sgg_cd = NULL, region_umd_cd = NULL, region_match = NULL,
                           boundary_version = NULL, sgg_mismatch = false
                     WHERE apt_seq = %s AND geocode_status IN ('pending', 'failed')""",
                                [(found[s][0], found[s][1], now, s) for s in seqs])
            for hook in hooks.ON_REGION_CHANGE:   # 바뀐 뒤 지역
                hook(conn, seqs)
        missing = sorted(still - set(seqs))
        failed = conn.execute("UPDATE complexes SET geocode_status = 'failed' "
                              "WHERE apt_seq = ANY(%s) AND geocode_status = 'pending'", (missing,)).rowcount
    skipped = len([s for s in target_seqs if s not in still])
    return {"located": len(seqs), "failed": failed, "skipped": skipped}


def describe_target(url):
    """비밀번호 없이 host:port/dbname만. → (표시 문자열, host)"""
    u = urlparse(url)
    return f"{u.hostname}:{u.port or 5432}/{u.path.lstrip('/')}", u.hostname


def main(argv=None):
    parser = argparse.ArgumentParser(description="연속지적도형정보 → 단지 좌표")
    parser.add_argument("--dir", required=True, help="시도별 연속지적도 zip(AL_D002_*.zip)이 있는 폴더")
    parser.add_argument("--database-url", help="대상 DB(기본: DATABASE_URL)")
    parser.add_argument("--dry-run", action="store_true", help="저장하지 않고 통계만 출력")
    parser.add_argument("--yes", action="store_true", help="로컬이 아닌 DB에 저장하는 것을 확인함")
    args = parser.parse_args(argv)
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    import settings

    url = settings.env("DATABASE_URL")
    if not url:
        print("DATABASE_URL이 없습니다. --database-url을 주거나 .env를 설정하세요.")
        return 1
    shown, host = describe_target(url)
    print(f"대상 DB: {shown}")
    if host not in LOCAL_HOSTS and not args.yes and not args.dry_run:
        print("로컬이 아닌 DB입니다. 확인했다면 --yes를 붙여 다시 실행하세요.")
        return 1

    paths = sorted(Path(args.dir).glob("*.zip"))
    if not paths:
        print(f"{args.dir}에 zip 파일이 없습니다.")
        return 1

    import db
    import wiring

    db.close_pool()      # 환경변수를 바꿨을 수 있으므로 새 풀로 연결한다
    wiring.wire()        # 좌표가 바뀐 단지의 집계 대기열 훅
    with db.connection() as conn:
        wanted_by_seq, n_targets = targets(conn)
    db.close_pool()      # 오래 걸리는 파일 읽기 동안 유휴 연결을 잡고 있지 않는다

    points, stats = read_points(paths, set(wanted_by_seq.values()))
    found = {s: points[p] for s, p in wanted_by_seq.items() if p in points}
    pct = 100 * len(found) / n_targets if n_targets else 0.0
    print(f"zip {stats['files']}개 · 필지 {stats['parcels']:,}개 읽음 · 대상 단지 {n_targets:,} · "
          f"PNU 없음 {n_targets - len(wanted_by_seq):,} · 찾음 {len(found):,}({pct:.1f}%) · "
          f"못 찾음 {len(wanted_by_seq) - len(found):,} · 범위 밖 {stats['dropped']:,}")
    if args.dry_run:
        print("--dry-run: 저장하지 않습니다.")
        return 0
    if not found:
        print("찾은 단지가 없습니다. 저장하지 않습니다.")
        return 1
    with db.connection() as conn:
        result = save(conn, found, list(wanted_by_seq))   # 대상 = PNU를 만든 단지(PNU 없는 단지는 pending으로 남는다)
    print(f"저장: 좌표 {result['located']:,} · failed {result['failed']:,} · 상태가 바뀌어 건너뜀 {result['skipped']:,}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_parcel_points.py -q`
Expected: PASS (6 passed)

실제 자료로 로컬 시험(로컬 DB, 저장 없음). 로컬 DB에 거래가 있으면 종로구 단지가 대부분 찾아진다.

Run: `.venv/Scripts/python -m geo.parcel_points --dir D:\geo_src\연속지적도형정보 --dry-run`
Expected: `대상 DB: localhost:…` 다음에 통계 한 줄, `--dry-run: 저장하지 않습니다.` 이 폴더에는 서울 zip이 월별로 11개 있으므로, 실행 전 최신본 하나만 있는 임시 폴더를 만들어 쓴다(같은 필지가 여러 파일에 있어도 나중 파일이 덮어쓸 뿐 오류는 아니다).

- [ ] **Step 5: 커밋**

```bash
git add geo/parcel_points.py tests/test_geo_parcel_points.py
git commit -m "연속지적도형정보로 단지 좌표를 만드는 로컬 도구 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 단지 점 API (`/api/map/complexes`, `/api/complexes/<seq>/nearby`)

**Files:**
- Modify: `analytics/queries.py`, `web/api.py`
- Test: `tests/test_analytics_api.py`

**Interfaces:**
- Consumes: `complexes.lon/lat/geocode_status/region_sgg_cd/region_umd_cd`
- Produces:
  - `queries.complex_points(conn, sgg, band, ym_from, ym_to) -> list[dict]`(키 `apt_seq, apt_nm, lon, lat, n, median_price, median_ppm2`)
  - `queries.nearby(conn, version, apt_seq, limit=500) -> dict`(키 `version, umd_cd, umd_name, complexes[{apt_seq, apt_nm, lon, lat, is_self}]`), 없는 단지는 `LookupError`
  - `GET /api/map/complexes?parent=&band=&from=&to=` → `{version, parent, band, from, to, complexes}`
  - `GET /api/complexes/<apt_seq>/nearby` → 위 dict, 없는 단지 404

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_analytics_api.py` 끝에 추가(이 파일의 기존 `seeded` 픽스처를 쓴다. 시드: 종로구 A(청운동 좌표 있음)·B(신교동 좌표 있음), 중구 C(좌표 없음)).

```python
def test_map_complexes(client, seeded):
    r = client.get("/api/map/complexes?parent=11110&from=202607&to=202609").get_json()
    assert r["parent"] == "11110" and r["from"] == "202607" and r["to"] == "202609"
    by = {c["apt_seq"]: c for c in r["complexes"]}
    assert set(by) == {"A", "B"}
    assert by["A"]["n"] == 9 and by["A"]["lon"] == 126.955 and by["A"]["apt_nm"] == "청운아파트"
    assert by["B"]["n"] == 6 and by["B"]["median_price"] is not None and by["B"]["median_ppm2"] is not None


def test_map_complexes_band_and_empty(client, seeded):
    r = client.get("/api/map/complexes?parent=11110&band=le60&from=202607&to=202609").get_json()
    by = {c["apt_seq"]: c for c in r["complexes"]}
    assert by["A"]["n"] == 9 and by["B"]["n"] == 0 and by["B"]["median_price"] is None   # B는 114.8㎡
    assert client.get("/api/map/complexes?parent=11140").get_json()["complexes"] == []   # 좌표 있는 단지 없음


@pytest.mark.parametrize("q", ["", "parent=111", "parent=abcde", "parent=11110&band=x"])
def test_map_complexes_rejects(client, seeded, q):
    assert client.get(f"/api/map/complexes?{q}").status_code == 400


def test_nearby(client, seeded):
    r = client.get("/api/complexes/A/nearby").get_json()
    assert r["umd_cd"] == "11110101" and r["umd_name"] == "서울특별시 종로구 청운동"
    assert [(c["apt_seq"], c["is_self"]) for c in r["complexes"]] == [("A", True)]
    c = client.get("/api/complexes/C/nearby").get_json()
    assert c["umd_cd"] is None and c["complexes"] == []
    assert client.get("/api/complexes/ZZZ/nearby").status_code == 404
```

파일 위쪽에 `import pytest`가 없으면 추가한다. 기간 기본값 확인은 하지 않는다(`/api/map`과 같은 규칙).

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_analytics_api.py -q -k "map_complexes or nearby"`
Expected: FAIL (404)

- [ ] **Step 3: 구현**

`analytics/queries.py` 끝에 추가:

```python
_BAND_SQL = {
    "all": "TRUE",
    "le60": "t.exclu_use_ar <= 60",
    "60_85": "t.exclu_use_ar > 60 AND t.exclu_use_ar <= 85",
    "gt85": "t.exclu_use_ar > 85",
}


def complex_points(conn, sgg, band, ym_from, ym_to):
    """시군구 안 좌표 있는 단지와 기간 거래(해제 제외) 건수·중위가·㎡당 중위가. 거래가 없으면 n=0, 값 None."""
    return conn.execute(f"""
        SELECT c.apt_seq, c.apt_nm, c.lon, c.lat, COUNT(t.id)::int AS n,
               percentile_cont(0.5) WITHIN GROUP (ORDER BY t.deal_amount::float8) AS median_price,
               percentile_cont(0.5) WITHIN GROUP (
                   ORDER BY CASE WHEN t.exclu_use_ar > 0 THEN t.deal_amount::float8 / t.exclu_use_ar::float8 END)
                   AS median_ppm2
          FROM complexes c
          LEFT JOIN trades t ON t.apt_seq = c.apt_seq AND t.deal_ymd BETWEEN %s AND %s
               AND NOT t.is_cancelled AND t.deal_amount IS NOT NULL AND ({_BAND_SQL[band]})
         WHERE c.region_sgg_cd = %s AND c.geocode_status IN ('ok', 'manual')
           AND c.lon IS NOT NULL AND c.lat IS NOT NULL
         GROUP BY c.apt_seq, c.apt_nm, c.lon, c.lat
         ORDER BY c.apt_seq""", (ym_from, ym_to, sgg)).fetchall()


def nearby(conn, version, apt_seq, limit=500):
    """같은 읍면동의 좌표 있는 단지(자기 자신 먼저). 지역 미판정이면 빈 목록."""
    me = conn.execute("SELECT region_umd_cd FROM complexes WHERE apt_seq = %s", (apt_seq,)).fetchone()
    if not me:
        raise LookupError(apt_seq)
    umd = me["region_umd_cd"]
    if not umd:
        return dict(version=version, umd_cd=None, umd_name=None, complexes=[])
    name = conn.execute("SELECT full_name FROM regions WHERE boundary_version = %s AND region_cd = %s",
                        (version, umd)).fetchone()
    rows = conn.execute("""
        SELECT apt_seq, apt_nm, lon, lat, apt_seq = %s AS is_self FROM complexes
         WHERE region_umd_cd = %s AND geocode_status IN ('ok', 'manual') AND lon IS NOT NULL AND lat IS NOT NULL
         ORDER BY (apt_seq = %s) DESC, last_deal_date DESC NULLS LAST, apt_seq LIMIT %s""",
                        (apt_seq, umd, apt_seq, limit)).fetchall()
    return dict(version=version, umd_cd=umd, umd_name=name["full_name"] if name else None, complexes=rows)
```

`web/api.py`:
- `CACHED`에 `"api.map_complexes"`를 추가한다.
- `/map` 라우트 아래에 추가한다. 기간 처리는 `map_()`와 같아야 하므로 두 라우트가 함께 쓰는 도우미로 뺀다.

```python
def _map_period():
    """지도 기간: from/to가 있으면 검증, 없으면 확정 최근 3개월."""
    if request.args.get("from") or request.args.get("to"):
        return params.ym_range(request.args)
    ym_to = queries.confirmed_ym()
    return queries.shift_ym(ym_to, -2), ym_to
```

`map_()` 안의 `if request.args.get("from") ... ym_from = queries.shift_ym(ym_to, -2)` 다섯 줄을 `ym_from, ym_to = _map_period()`로 바꾼다.

```python
@bp.route("/map/complexes")
def map_complexes():
    parent = request.args.get("parent") or ""
    if not (parent.isdigit() and len(parent) == 5):
        raise BadParam("단지 지도는 시군구 코드(parent, 5자리)가 필요합니다.")
    band = params.choice(request.args.get("band"), [b for b, _ in params.BANDS], "면적 구간", "all")
    ym_from, ym_to = _map_period()
    with db.connection() as conn:
        version = queries.active_version(conn)
        rows = queries.complex_points(conn, parent, band, ym_from, ym_to)
    return jsonify(version=version, parent=parent, band=band, complexes=rows, **{"from": ym_from, "to": ym_to})
```

파일 끝에 추가:

```python
@bp.route("/complexes/<apt_seq>/nearby")
def complex_nearby(apt_seq):
    try:
        with db.connection() as conn:
            return jsonify(queries.nearby(conn, queries.active_version(conn), apt_seq))
    except LookupError:
        return jsonify(error="없는 단지입니다."), 404
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_analytics_api.py -q`
Expected: PASS

- [ ] **Step 5: 커밋**

```bash
git add analytics/queries.py web/api.py tests/test_analytics_api.py
git commit -m "단지 위치 점 API(시군구 단지 지표, 같은 읍면동 단지) 추가

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 지도 화면 읍면동 단계 단지 점

**Files:**
- Modify: `static/js/map.js`, `templates/map.html`
- Test: `tests/test_pages.py`(test_map_page)

**Interfaces:**
- Consumes: `GET /api/map/complexes`(Task 5)
- Produces: URL 상태 `pts`(`'1'` 표시, `'0'` 숨김). 템플릿 알약 `id="pts"`.

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_pages.py`의 `test_map_page` 끝에 추가:

```python
    assert 'id="pts" role="group"' in html and 'data-value="1" aria-pressed="true"' in html
    assert "단지 표시" in html
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -q -k map_page`
Expected: FAIL

- [ ] **Step 3: 구현**

`templates/map.html`의 면적 줄 아래에 한 줄을 추가한다.

```html
  <div class="filter-row"><span class="filter-label">단지</span>{{ ui.pills('pts', [('1', '표시'), ('0', '숨김')], '1', '단지 표시') }}
    <span class="meta">읍면동 단계에서 단지 위치를 점으로 보여 줍니다(크기 거래량, 색 지표).</span></div>
```

`static/js/map.js`:

1. 맨 위 설명 주석 아래에 한 줄 추가: `// 읍면동 단계에서는 단지 위치 점(scatter, geo 좌표계)을 겹친다. 크기 = 거래량, 색 = 지표(전년 대비 지표는 단지 값이 없어 중립색).`
2. `state` 기본값에 `pts: '1'`을 추가한다: `App.readState({ level: 'sido', parent: '', metric: 'median_price', band: 'all', from: '', to: '', pts: '1' })`
3. `bandPills` 아래에 추가한다.

```js
  const ptsPills = App.pills(el('pts'), () => render());
  if (!ptsPills.has(state.pts)) state.pts = '1';
  ptsPills.value = state.pts;
  // 지표 → 단지 점 색에 쓸 /api/map/complexes 필드(전년 대비는 없음)
  const POINT_FIELD = { median_price: 'median_price', median_ppm2: 'median_ppm2', n_trades: 'n' };
  let points = [];
```

4. `render()`:
- 맨 앞 상태 읽기 줄들 옆에 `state.pts = ptsPills.value;`를 추가한다.
- `const fc = await geo(...)`, `if (my !== seq) return;` 다음에 추가한다.

```js
      const pts = apiData.level === 'umd' && state.pts === '1'
        ? (await App.api('/api/map/complexes', { parent: apiData.parent, band: state.band, from: apiData.from, to: apiData.to })).complexes
        : [];
      if (my !== seq) return;
      points = pts;
```

5. `drawMap(name)`을 통째로 다음으로 바꾼다. 단계구분도를 `series: map`에서 `geo` 컴포넌트로 옮겨 점과 같은 좌표계를 쓴다(`geo`의 `regions`로 지역별 색을 준다).

```js
  function pointSize(n) { return n ? Math.max(4, Math.min(14, 3 + Math.sqrt(n) * 1.5)) : 3; }

  function pointSeries() {
    const pf = POINT_FIELD[state.metric];
    const vals = pf ? points.map((c) => c[pf]).filter((v) => v != null).sort((a, b) => a - b) : [];
    // 단지 값의 분위수로 7단계(지도와 같은 남색 단계)
    const color = (v) => {
      if (!pf || v == null || !vals.length) return App.css('--muted');
      let lo = 0, hi = vals.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (vals[mid] < v) lo = mid + 1; else hi = mid; }
      return App.css(RAMP.seq[Math.min(RAMP.seq.length - 1, Math.floor((lo / vals.length) * RAMP.seq.length))]);
    };
    return {
      type: 'scatter', coordinateSystem: 'geo', geoIndex: 0, z: 3,
      data: points.map((c) => ({
        value: [c.lon, c.lat], c, symbolSize: pointSize(c.n),
        itemStyle: { color: c.n ? color(pf ? c[pf] : null) : App.css('--nodata'), borderColor: App.css('--card'), borderWidth: 1 },
      })),
      emphasis: { scale: 1.5, itemStyle: { borderColor: App.css('--ink'), borderWidth: 1.5 } },
    };
  }

  function drawMap(name) {
    const [title, field, f, kind] = METRICS[state.metric];
    const sc = scaleOf(kind, field);
    const byCd = Object.fromEntries(data.values.map((v) => [v.region_cd, v]));
    const regionTip = (v) => (v ? `<b>${esc(v.full_name)}</b><br>${title}: ${f(v[field])}<br>거래 ${cnt(v.n)}`
      + `${state.metric.startsWith('yoy') ? '' : `<br>중위가 전년 대비 ${App.fmt.pct(v.yoy_price)}`}` : '');
    const pointTip = (c) => (c ? `<b>${esc(c.apt_nm || c.apt_seq)}</b><br>거래 ${cnt(c.n)}<br>중위가 ${App.fmt.eok(c.median_price)}`
      + `<br>㎡당 ${App.fmt.ppm2(c.median_ppm2)}<br><small>누르면 단지 상세</small>` : '');
    const nameLabel = (p) => byCd[p.name]?.name ?? '';
    chart.setOption({
      tooltip: {
        trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
        formatter: (p) => (p.componentType === 'geo' ? regionTip(byCd[p.name]) : pointTip(p.data?.c)),
      },
      geo: {
        map: name, nameProperty: 'region_cd', roam: true, selectedMode: 'single',
        top: 12, bottom: 12, left: 8, right: 8, tooltip: { show: true },
        regions: data.values.map((v) => {
          const c = colorFor(v[field], kind, sc);
          return { name: v.region_cd, itemStyle: { areaColor: c }, emphasis: { itemStyle: { areaColor: c } }, select: { itemStyle: { areaColor: c } } };
        }),
        itemStyle: { areaColor: App.css('--nodata'), borderColor: App.css('--card'), borderWidth: 1 },
        emphasis: { label: { show: true, color: App.css('--text'), formatter: nameLabel },
          itemStyle: { borderColor: App.css('--ink'), borderWidth: 2 } },
        select: { label: { show: true, color: App.css('--text'), formatter: nameLabel },
          itemStyle: { borderColor: App.css('--ink'), borderWidth: 2.5 } },
      },
      series: points.length ? [pointSeries()] : [],
    }, true);
    if (selected) chart.dispatchAction({ type: 'geoSelect', geoIndex: 0, name: selected });
  }
```

6. `select(code)` 안의 `chart.dispatchAction({ type: 'select', seriesIndex: 0, name: code });`를 `chart.dispatchAction({ type: 'geoSelect', geoIndex: 0, name: code });`로 바꾼다.
7. `chart.on('click', ...)`을 다음으로 바꾼다.

```js
  chart.on('click', (p) => {
    if (p.componentType === 'series' && p.seriesType === 'scatter') {
      const c = p.data?.c;
      if (c) location.href = `/complexes/${encodeURIComponent(c.apt_seq)}`;
      return;
    }
    if (p.componentType !== 'geo') return;
    const v = data.values.find((x) => x.region_cd === p.name);
    if (!v) return;
    if (NEXT[state.level]) {
      showMini(v);
      state.parent = v.region_cd; state.level = NEXT[state.level];
      selected = null; el('rank-q').value = '';
      render();
    } else {
      select(v.region_cd);
    }
  });
```

8. `drawNote()`의 커버리지 문구 `(좌표가 있는 단지의 거래 비율)`를 `(읍면동이 판정된 단지의 거래 비율)`로 바꾸고, 끝에 단지 점 수를 붙인다.

```js
      + (data.level === 'umd' && state.pts === '1' ? ` · 단지 점 ${points.length}개(좌표가 있는 단지)` : '');
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py tests/test_ui.py -q`
Expected: PASS

브라우저 확인(필수): 로컬 개발 서버(`.claude/launch.json`의 개발 서버, 로컬 DB에 좌표 있는 단지가 있어야 함. 없으면 Task 4 도구를 로컬 DB에 저장 실행하거나 `scripts/seed_dev.py`를 쓰는 별도 DB 사용 — 실제 수집 자료가 든 `molit_dev`에 `--reset`을 쓰지 않는다)에서:
- 시도 → 시군구 → 읍면동으로 들어가 점이 경계 위 제자리에 그려지는지, 확대·이동(roam) 때 점이 경계와 함께 움직이는지
- 점 툴팁·클릭(단지 상세로 이동), 읍면동 클릭(미니 추이), 순위 클릭(선택 테두리)
- 지표 바꾸기(전년 대비면 회색 점), "숨김"이면 점 없음, 좌표 있는 단지가 없는 시군구에서 오류 없음
- 밝은·어두운 테마, 콘솔 오류 없음

- [ ] **Step 5: 커밋**

```bash
git add static/js/map.js templates/map.html tests/test_pages.py
git commit -m "지도 읍면동 단계에 단지 위치 점 표시(크기 거래량, 색 지표, 클릭 시 단지 상세)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: 단지 상세 위치 카드와 배지

**Files:**
- Modify: `analytics/queries.py`(`_COMPLEX_SELECT`), `templates/complex.html`, `static/js/complex.js`
- Test: `tests/test_pages.py`(test_complex_page)

**Interfaces:**
- Consumes: `GET /api/complexes/<seq>/nearby`(Task 5), 경계 파일 `/static/geo/<version>/umd_<시도2자리>.json`(feature `properties.region_cd`)
- Produces: 템플릿 `id="loc"`(좌표 있을 때만), 배지 문구

- [ ] **Step 1: 실패하는 테스트 작성** — `tests/test_pages.py`의 `test_complex_page`를 다음으로 바꾼다.

```python
def test_complex_page(client, seeded):
    html = client.get("/complexes/A").get_data(as_text=True)
    assert "청운아파트" in html and "서울특별시 종로구 청운동" in html and 'id="scatter"' in html
    assert "<h1>청운아파트</h1>" in html and 'class="data-table"' in html and 'class="badge' in html
    assert 'id="loc"' in html and "지역 판정: 경계" in html and "위치: 수동" not in html
    assert "126.955" not in html and "37.575" not in html          # 좌표 숫자를 화면에 쓰지 않는다
    c = client.get("/complexes/C").get_data(as_text=True)
    assert 'id="loc"' not in c and "위치 정보 없음" in c and "지역 판정: 미판정" in c
    assert client.get("/complexes/ZZZ").status_code == 404
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py -q -k complex_page`
Expected: FAIL

- [ ] **Step 3: 구현**

`analytics/queries.py` `_COMPLEX_SELECT`의 둘째 줄을 다음으로 바꾼다(`geocode_source`, `region_umd_cd` 추가).

```python
           c.lon, c.lat, c.geocode_status, c.geocode_source, c.region_match, c.region_umd_cd, c.sgg_mismatch,
```

`templates/complex.html`:
- 좌표 배지 줄(`<span class="badge">좌표 ...</span>`)을 다음 두 줄로 바꾼다.

```html
  {% set match_label = {'within': '경계', 'nearest': '경계(근접)', 'code': '법정동 코드'} %}
  <span class="badge">지역 판정: {{ match_label.get(c.region_match, '미판정') }}</span>
  <span class="badge">위치: {% if c.geocode_status == 'manual' %}수동{% elif c.geocode_status == 'ok' and c.lon is not none %}필지{% else %}없음{% endif %}</span>
```

- `{% if c.sgg_mismatch %}` 줄은 그대로 둔다.
- 거래 가격 카드(`<section class="card"><h2 class="card-title">거래 가격`) 바로 앞에 위치 카드를 넣는다.

```html
<section class="card">
  <h2 class="card-title">위치</h2>
  {% if c.lon is not none and c.geocode_status in ('ok', 'manual') %}
  <div id="loc" class="chart small"></div>
  <p class="table-foot">같은 읍면동 단지(회색)와 이 단지(강조). 점을 누르면 그 단지로 갑니다. 단지 위치는 필지 대표점입니다.</p>
  {% else %}
  <p class="meta">위치 정보 없음(좌표 미확보)</p>
  {% endif %}
</section>
```

`static/js/complex.js` 끝(마지막 `})();` 앞)에 추가한다.

```js
  // 위치: 같은 읍면동 경계 위에 주변 단지(회색)와 이 단지(강조)
  if (el('loc')) {
    try {
      const nb = await App.api(`/api/complexes/${encodeURIComponent(window.APT_SEQ)}/nearby`);
      if (!nb.umd_cd || !nb.complexes.length) {
        el('loc').outerHTML = '<p class="meta">읍면동이 판정되지 않아 위치 지도를 그리지 않습니다.</p>';
      } else {
        const resp = await fetch(`/static/geo/${encodeURIComponent(nb.version)}/umd_${nb.umd_cd.slice(0, 2)}.json`);
        if (!resp.ok) throw new Error('경계 파일을 불러오지 못했습니다.');
        const fc = await resp.json();
        const feature = fc.features.filter((f) => f.properties.region_cd === nb.umd_cd);
        const mapName = `loc:${nb.version}:${nb.umd_cd}`;
        echarts.registerMap(mapName, { type: 'FeatureCollection', features: feature });
        const others = nb.complexes.filter((c) => !c.is_self);
        const self = nb.complexes.filter((c) => c.is_self);
        const pt = (c) => ({ value: [c.lon, c.lat], c });
        const loc = App.chart(el('loc'));
        loc.setOption({
          tooltip: { trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
            formatter: (p) => (p.data?.c ? App.escapeHtml(p.data.c.apt_nm || p.data.c.apt_seq) : App.escapeHtml(nb.umd_name || '')) },
          geo: { map: mapName, roam: true, top: 8, bottom: 8, left: 8, right: 8, silent: true,
            itemStyle: { areaColor: App.css('--soft'), borderColor: App.css('--line'), borderWidth: 1 } },
          series: [
            { type: 'scatter', coordinateSystem: 'geo', data: others.map(pt), symbolSize: 7,
              itemStyle: { color: App.css('--muted'), borderColor: App.css('--card'), borderWidth: 1 } },
            { type: 'scatter', coordinateSystem: 'geo', data: self.map(pt), symbolSize: 16, z: 3,
              itemStyle: { color: App.css('--accent'), borderColor: App.css('--card'), borderWidth: 2 } },
          ],
        });
        loc.on('click', (p) => { const c = p.data?.c; if (c && !c.is_self) location.href = `/complexes/${encodeURIComponent(c.apt_seq)}`; });
      }
    } catch (e) { App.message(el('msg'), e.message); }
  }
```

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tests/test_pages.py tests/test_analytics_api.py -q`
Expected: PASS

브라우저 확인(필수): 좌표 있는 단지 상세에서 위치 카드에 읍면동 경계와 점이 그려지는지, 주변 점 클릭 이동, 좌표 없는 단지는 "위치 정보 없음", 화면 어디에도 좌표 숫자가 없는지, 밝은·어두운 테마.

- [ ] **Step 5: 커밋**

```bash
git add analytics/queries.py templates/complex.html static/js/complex.js tests/test_pages.py
git commit -m "단지 상세에 위치 지도와 지역 판정·위치 출처 배지 추가, 좌표 숫자 표시 제거

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: 수집 현황 집계·출처·코드북·문서

**Files:**
- Modify: `geo/complexes.py`(summary), `templates/status.html:127-139`, `templates/base.html:50`, `analytics/export.py:59-60`, `docs/runbooks/geo-data.md`, `README.md`
- Test: `tests/test_geo_web.py`, `tests/test_ui.py`

**Interfaces:**
- Consumes: `region_match` 값(Task 3), `geocode_source='parcel'`(Task 4)
- Produces: `complexes.summary()` 키 `total, located, located_pct, parcel, manual, pending, failed, by_boundary, nearest, by_code, unassigned, mismatch, by_status, boundary_version`

- [ ] **Step 1: 실패하는 테스트 작성**

`tests/test_geo_web.py`의 `seeded`와 `test_summary_and_failed`를 다음으로 바꾼다.

```python
@pytest.fixture
def seeded(pg):
    with pg.connection() as conn:
        conn.execute("""INSERT INTO complexes (apt_seq, apt_nm, api_sgg_cd, geocode_status, geocode_source, lon, lat,
                            region_sgg_cd, region_umd_cd, region_match, boundary_version) VALUES
            ('A', '가단지', '11110', 'ok', 'parcel', 126.95, 37.57, '11110', '11110101', 'within', '2026-10'),
            ('B', '나단지', '11110', 'failed', NULL, NULL, NULL, '11110', '11110102', 'code', '2026-10'),
            ('P', '다단지', '11110', 'pending', NULL, NULL, NULL, NULL, NULL, 'none', '2026-10')""")


def test_summary_and_failed(pg, seeded):
    with pg.connection() as conn:
        s = complexes.summary(conn)
        f = complexes.failed(conn)
    assert s["total"] == 3 and s["by_status"] == {"ok": 1, "failed": 1, "pending": 1}
    assert (s["parcel"], s["manual"], s["pending"], s["failed"]) == (1, 0, 1, 1)
    assert (s["by_boundary"], s["by_code"], s["unassigned"]) == (1, 1, 1)
    assert s["located"] == 1 and s["located_pct"] == 33.3
    assert [r["apt_seq"] for r in f] == ["B"] and f[0]["n_trades"] == 0


def test_status_page_shows_geo(client, seeded):
    html = client.get("/status").get_data(as_text=True)
    assert "단지 좌표·지역 판정" in html and "나단지" in html
    assert "법정동 코드" in html and "런북" in html and "위치정보요약DB" not in html
```

(`test_set_manual`은 `seeded`의 A가 이제 좌표가 있으므로 그대로 통과해야 한다. 실패하면 기대값이 좌표를 덮어쓴 값인지 확인만 한다.)

`tests/test_ui.py` `test_base_uses_self_hosted_assets` 끝에 추가:

```python
    assert "단지 위치: 국토교통부 연속지적도형정보(CC BY)" in html and "위치정보요약DB" not in html
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tests/test_geo_web.py tests/test_ui.py -q`
Expected: FAIL

- [ ] **Step 3: 구현**

`geo/complexes.py` `summary()`를 다음으로 바꾼다.

```python
def summary(conn):
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE geocode_status IN ('ok', 'manual')) AS located,
               COUNT(*) FILTER (WHERE geocode_status = 'ok' AND geocode_source = 'parcel') AS parcel,
               COUNT(*) FILTER (WHERE geocode_status = 'manual') AS manual,
               COUNT(*) FILTER (WHERE geocode_status = 'pending') AS pending,
               COUNT(*) FILTER (WHERE geocode_status = 'failed') AS failed,
               COUNT(*) FILTER (WHERE region_match IN ('within', 'nearest')) AS by_boundary,
               COUNT(*) FILTER (WHERE region_match = 'nearest') AS nearest,
               COUNT(*) FILTER (WHERE region_match = 'code') AS by_code,
               COUNT(*) FILTER (WHERE region_match = 'none' OR region_match IS NULL) AS unassigned,
               COUNT(*) FILTER (WHERE sgg_mismatch) AS mismatch
          FROM complexes""").fetchone()
    by_status = {r["geocode_status"]: r["n"] for r in conn.execute(
        "SELECT geocode_status, COUNT(*) AS n FROM complexes GROUP BY 1")}
    version = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchone()
    total = row["total"]
    return dict(row, by_status=by_status,
                located_pct=round(100 * row["located"] / total, 1) if total else 0.0,
                boundary_version=version["version"] if version else None)
```

`templates/status.html`의 `<h2>단지 좌표·지역 판정</h2>`부터 `<p class="meta">활성 경계 …` 단락 끝까지를 다음으로 바꾼다(오류 표시 세 줄은 유지).

```html
<h2>단지 좌표·지역 판정</h2>
<div class="tiles">
  <div class="tile"><div class="k">좌표 확보 단지 (필지 · 수동)</div>
    <div class="v">{{ g.located|comma }} / {{ g.total|comma }}</div>
    <div class="meter"><i style="width:{{ g.located_pct }}%"></i></div>
    <div class="k">필지 {{ g.parcel|comma }} · 수동 {{ g.manual|comma }}</div></div>
  <div class="tile"><div class="k">좌표 없음 (대기 · 못 찾음)</div>
    <div class="v {{ 'warn' if g.pending or g.failed }}">{{ g.pending|comma }} · {{ g.failed|comma }}</div></div>
  <div class="tile"><div class="k">지역 판정 (경계 · 법정동 코드 · 미판정)</div>
    <div class="v">{{ g.by_boundary|comma }} · {{ g.by_code|comma }} · <span class="{{ 'warn' if g.unassigned }}">{{ g.unassigned|comma }}</span></div>
    <div class="k">경계 중 근접 배정 {{ g.nearest|comma }}</div></div>
  <div class="tile"><div class="k">신고 시군구 ≠ 좌표 시군구</div>
    <div class="v {{ 'warn' if g.mismatch }}">{{ g.mismatch|comma }}</div></div>
</div>
<p class="meta">좌표가 없는 단지가 쌓이면 이 PC에서 연속지적도 좌표 도구를 실행하세요(런북 docs/runbooks/geo-data.md A).
  좌표가 없어도 법정동 코드로 읍면동을 판정하므로 집계는 빠지지 않습니다.</p>
<p class="meta">활성 경계 {{ g.boundary_version or '없음' }} · 마지막 처리 {{ g.pipeline.last_run or '-' }}
```

`templates/base.html` 50줄의 `단지 좌표: 주소정보누리집 위치정보요약DB`를 `단지 위치: 국토교통부 연속지적도형정보(CC BY)`로 바꾼다.

`analytics/export.py` `_EXTRA_DOCS`:

```python
    "region_sgg_cd": ("최신 경계 시군구", "단지 위치(필지 대표점)나 법정동 코드로 판정한 시군구. 판정하지 못하면 빈 값"),
    "region_umd_cd": ("최신 경계 읍면동", "단지 위치(필지 대표점)나 법정동 코드로 판정한 읍면동 8자리. 판정하지 못하면 빈 값"),
```

`docs/runbooks/geo-data.md`:
- 첫 문단 둘째 줄 아래에 추가: `주소정보누리집 위치정보요약DB는 받을 때 "국외 반출 금지" 조건이 붙어 쓰지 않는다(운영 서버가 해외).`
- "처음 한 번" 목록을 다음으로 바꾼다.

```markdown
1. **코드 배포** — 마이그레이션이 적용되고 `/login`이 열리는지 확인한다.
2. **첫 경계 버전 커밋·배포(B)** — 경계 폴더가 배포에 포함되어야 서버가 본다. 이때부터 모든 단지가 법정동 코드로 판정된다.
3. **연속지적도로 단지 좌표 만들기(A)**
4. 10분 안에 파이프라인이 좌표 있는 단지를 경계로 다시 판정한다. `/status`의 "좌표 확보 단지"와 "지역 판정"으로 확인한다.
```

- `## A. 위치정보요약DB (월 1회)` 절 전체를 다음으로 바꾼다.

````markdown
## A. 연속지적도로 단지 좌표 만들기 (필요할 때)
`/status`의 "좌표 없음(대기)"이 쌓이면 실행한다. 좌표가 없어도 읍면동 집계는 법정동 코드로 채워지므로 급하지 않다.
1. 브이월드(vworld.kr) → 공간정보 다운로드 → **연속지적도형정보**(국토교통부, **CC BY**). 시·도마다 구분 **전체데이터**·형식 **SHP**의
   최신 기준일 파일(`AL_D002_<시도>_<기준일>.zip`)을 받아 한 폴더(예: `D:\geo_src\연속지적도형정보\전국_YYYYMM`)에 모은다.
   **시도마다 파일 하나만** 둔다(같은 시도의 여러 기준일이 섞이면 나중 파일 값이 쓰인다). 17개 시도, 합계 약 1.5GB.
   - 화면 하단 출처 표시(`templates/base.html`, "단지 위치: 국토교통부 연속지적도형정보(CC BY)")를 지우지 않는다.
2. 로컬 DB로 먼저 시험한다(저장 없음):
   ```bash
   .venv/Scripts/python -m geo.parcel_points --dir <폴더> --dry-run
   ```
   출력: `zip N개 · 필지 …개 읽음 · 대상 단지 … · PNU 없음 … · 찾음 …(…%) · 못 찾음 … · 범위 밖 …`
   - `PNU 열을 찾지 못했습니다`면 연속지적도형정보 파일이 아니다(GIS건물통합정보·공시가격 파일과 섞이지 않았는지 확인).
3. 운영 DB에 저장한다. **먼저 `--dry-run`으로 운영 DB의 찾음 비율을 본다**(서울 시험에서 단지 99% 이상). Railway → Postgres 서비스 →
   Connect → **Public Network** 연결 문자열을 그 셸에서만 환경변수로 쓰고, 끝나면 셸을 닫는다(PowerShell):
   ```powershell
   $env:DATABASE_URL = "<공개 연결 문자열>"
   .venv/Scripts/python -m geo.parcel_points --dir <폴더> --dry-run
   .venv/Scripts/python -m geo.parcel_points --dir <폴더> --yes
   ```
   - 시작할 때 `대상 DB: host:port/dbname`이 출력된다(비밀번호는 가려진다). **운영 DB가 맞는지 확인**한다.
   - 좌표가 없는 단지(대기·못 찾음)만 다룬다. 수동 좌표와 이미 찾은 단지는 건드리지 않는다.
   - 10분 안에 `/status`의 지역 판정에서 "경계"가 늘고, 지도 읍면동 단계와 단지 상세에 점이 나온다.
   - 이 도구는 마이그레이션을 하지 않는다(스키마는 배포된 앱이 관리). 코드를 먼저 배포해야 한다.
````

- B절 2번 끝에 추가: `대응표만 고쳐 배포해도 서버가 바뀐 것을 알아채 법정동 코드로 판정한 단지를 다시 판정한다(경계 버전을 새로 내지 않아도 된다).`
- C절 제목 아래 문장은 그대로 둔다.
- D절의 "단지 생성 뒤 적재한 주소 좌표(A)로 처음 좌표 연결(LOCATE)이 끝날 때까지는 …" 문장을 지운다.

`README.md` 38줄 지리 설명의 `단지 등록·좌표 연결(`complexes`, `locate`)`을 `단지 등록·좌표 상태(`complexes`), 좌표 도구(`parcel_points`, 로컬), 법정동 코드 대응표(`code_map`)`로 바꾸고, README에서 `위치정보요약DB`가 나오는 다른 줄도 연속지적도로 고친다(`grep -n 위치정보요약 README.md`).

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest -q`
Expected: PASS(전체)

`grep -rn "위치정보요약\|address_points\|locate_pending" --include=*.py --include=*.html --include=*.js --include=*.md . | grep -v ".venv\|docs/superpowers\|.superpowers"` → 런북 첫 문단의 "쓰지 않는다" 한 줄 말고는 없어야 한다.

- [ ] **Step 5: 커밋**

```bash
git add geo/complexes.py templates/status.html templates/base.html analytics/export.py docs/runbooks/geo-data.md README.md tests/test_geo_web.py tests/test_ui.py
git commit -m "수집 현황에 좌표 출처·판정 방식 집계, 출처를 연속지적도로, 런북 A를 좌표 도구 절차로 개정

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

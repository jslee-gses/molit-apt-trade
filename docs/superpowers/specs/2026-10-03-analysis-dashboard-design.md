# 분석 대시보드 설계 (하위 프로젝트 ①)

- 작성일: 2026-10-03
- 상태: 승인됨 (2026-10-03 개정: 좌표 출처를 VWorld 지오코더에서 주소정보누리집 위치정보요약DB로 변경)
- 범위: molit-apt-trade를 연구·분석용 대시보드로 확장. SQLite → Postgres 이전 포함

## 1. 배경과 목표

### 1.1 목적과 우선순위
현재 앱은 국토부 아파트 매매 실거래가 API를 자동 수집하고 목록·누락 점검을 보여주는 Flask 앱이다(Railway, SQLite 볼륨 0.5GB).
이를 단계적으로 확장한다.

| 순서 | 하위 프로젝트 | 용도 |
|---|---|---|
| ① | **분석 대시보드 (이 문서)** | 본인·연구실의 연구·분석 |
| ② | 운영 관제 | 수집 상태 모니터링, 실패·누락·개편 알림, 수동 재수집 제어 |
| ③ | 수업용 포털 | ① 화면 재사용, 실습 데이터셋, 읽기 전용 공개 |

외부 고객용 서비스(다수 사용자, 권한 체계, SLA)는 범위 밖이다.
②·③은 각자 별도 명세 → 계획 → 구현 주기를 거친다.

### 1.2 분석 기능 우선순위
1. **a. 시계열 추이**: 지역별 월별 중위 가격, ㎡당 가격, 거래량
2. **b. 지역 비교·지도**: 시도·시군구·읍면동 단계구분도, 지역 간 비교
3. **c. 단지·개별 거래 탐색**: 단지별 거래 이력, 면적·층 분포
4. **d. 데이터 추출**: 조건별 원본·집계 CSV / Parquet

### 1.3 성공 기준
- 2006-01 이후 전국 전체 기간 원본이 Postgres에 저장되고, 작업별 저장 건수가 API `totalCount`와 일치한다.
- 추이·지도 화면이 **최신 법정동 경계 기준**으로 과거 전 기간을 보여준다.
- 추이·지도 API 응답이 300ms 이내다.
- 원본·집계를 CSV(utf-8-sig, 기존 API 필드명)와 Parquet로 받을 수 있다.
- 읍면동 커버리지(좌표가 확정된 단지의 거래 비율)를 화면에서 확인할 수 있다.

### 1.4 유지하는 원칙
- 데이터는 공공데이터포털 Open API로만 받는다(rt.molit.go.kr 크롤링 금지).
- 인증키·비밀번호(`MOLIT_SERVICE_KEY`, `APP_PASSWORD`, `SECRET_KEY`)는 저장소에 올리지 않는다. 로컬은 `.env`, Railway는 Variables에 둔다.
- 결과 저장을 금지하는 외부 API(VWorld 지오코더, 카카오 로컬 등)로 만든 좌표는 저장하지 않는다. 좌표는 저장·재사용이 허용된 공개 파일(주소정보누리집 위치정보요약DB)에서만 얻는다.

## 2. 플랫폼

**Railway Hobby (월 $5) + Railway Postgres**로 파일럿을 운영하고, 검증되면 필요에 따라 Pro로 전환한다.

검토한 대안과 제외 이유:
| 대안 | 제외 이유 |
|---|---|
| Cloudflare (Workers + D1/Hyperdrive) | 자체 Postgres 없음(D1은 SQLite, DB당 10GB 상한). 무료 플랜은 Cron 실행 10ms라 수집기 불가. Flask/pandas 코드 전면 재작성 필요 |
| Supabase | 무료 500MB에 1주일 비활성 시 일시 정지. Pro 월 $25. 수집기·앱 서버가 별도로 필요해 관리 지점이 2곳 |
| Neon | DB로는 적합(무료 1GB). 수집기·앱 서버가 별도로 필요 |
| 로컬 Postgres | 비용은 0이지만 PC가 켜져 있을 때만 수집 |

Cloudflare R2/Pages는 ③(공개 포털)이나 대용량 데이터 배포 때, Supabase는 ③에서 인증·행 단위 권한이 필요할 때 다시 검토한다.

### 2.1 용량
- 원본 1천만 건 안팎에 인덱스를 더해 3~5GB로 추정한다. Hobby 볼륨 상한 5GB에 근접한다.
- 이전 직후 실측한다. `/status`는 Postgres DB 크기를 기준으로 사용률을 표시한다.
- 80%를 넘으면 Pro 전환 여부를 결정한다.
- 90%(`STORAGE_STOP_PCT`) 이상이면 과거 자료 수집을 멈추는 기존 안전장치를 유지한다.

## 3. 아키텍처

### 3.1 Railway 구성
- **web 서비스**: Flask + gunicorn + APScheduler 수집 스레드를 한 프로세스에 둔다(기존 방식). 비용 절감이 목적이며, 코드는 모듈로 분리해 나중에 worker 서비스로 떼어낼 수 있게 한다.
- **Postgres 서비스**: Railway Postgres. 이전이 끝나면 SQLite 볼륨을 제거한다.

### 3.2 코드 구조
```
app.py                  # Flask 앱 생성, 블루프린트 등록, 로그인 보호, 스케줄러 시작
db.py                   # psycopg 3 커넥션 풀, 마이그레이션(migrations/*.sql 순서 적용)
migrations/*.sql        # 스키마 정의
collector/
  api.py                # 국토부 API 호출·XML 파싱 (기존 fetch_page/fetch_job/to_rows 이식)
  jobs.py               # 작업 생성·선택, 할당량, 재시도, 재확인 주기 (기존 로직 이식)
  quality.py            # 누락 점검 (기존 job_quality/quality_report 이식)
geo/
  address_points.py     # [로컬 수동 실행] 위치정보요약DB 파일 → 공동주택 도로명주소 좌표를 address_points에 적재
  locate.py             # pending 단지에 address_points 좌표 연결 (SQL, API 호출 없음)
  boundaries.py         # [로컬 수동 실행] 읍면동 경계 SHP → 변환·단순화 → GeoJSON, 시군구·시도 dissolve
  assign.py             # 단지 좌표 × 최신 법정동 폴리곤 → 지역 판정 (shapely STRtree)
analytics/
  aggregates.py         # agg_month 증분·전체 계산
  queries.py            # 추이·지도·단지·추출 조회
web/
  pages.py              # 화면 라우트
  api.py                # JSON API, 추출 스트리밍
templates/, static/     # Jinja + ECharts(CDN), static/geo/{version}/*.json
scripts/migrate_sqlite.py  # SQLite → Postgres 1회 이전
tests/
docker-compose.yml      # 로컬 Postgres
requirements.txt        # 서버: flask, gunicorn, requests, pandas, apscheduler, psycopg[binary,pool], shapely, pyarrow
requirements-dev.txt    # 로컬 전용: pytest, geopandas, pyproj
```

### 3.3 기술 선택
| 항목 | 선택 | 이유 |
|---|---|---|
| DB 드라이버 | psycopg 3 + psycopg_pool | 표준, COPY 대량 적재·스트리밍 |
| ORM | 없음(SQL 직접) | 기존 스타일 유지, 집계 SQL을 명확하게 관리 |
| 차트·지도 | ECharts (CDN) | 시계열과 단계구분도를 한 라이브러리로 처리 |
| 공간 처리 | 서버: shapely만 / 로컬: geopandas | 서버 의존성·메모리 최소화 |
| 추출 형식 | CSV + Parquet(pyarrow) | 대용량을 Python/R로 분석할 때 크기·타입 보존 |
| 접근 제어 | 공유 비밀번호(`APP_PASSWORD`) + 세션 쿠키(`SECRET_KEY`) | 연구실 범위 |

## 4. 데이터 모델

### 4.1 `trades` (원본 거래)
- 수집 단위 `(lawd_cd, deal_ymd)`마다 통째로 교체 저장한다(삭제 + 삽입을 한 트랜잭션으로).
- 컬럼은 API 필드를 snake_case로 바꾼다(예: `dealAmount` → `deal_amount`, `aptSeq` → `apt_seq`). 수집 메타 컬럼은 `lawd_cd`, `deal_ymd`, `collected_at`이다.
- 타입:
  - `deal_amount` INTEGER (만원)
  - `exclu_use_ar` NUMERIC(8,2)
  - `floor`, `build_year` SMALLINT
  - `deal_date` DATE
  - 나머지는 TEXT
- 파생 컬럼:
  - `price_per_m2` NUMERIC: `deal_amount / exclu_use_ar`, 생성 컬럼(GENERATED STORED)
  - `is_cancelled` BOOLEAN: `cdeal_type`이 비어 있지 않으면 true
- 인덱스: `(lawd_cd, deal_ymd)`, `(apt_seq, deal_date)`, `(deal_date)`

### 4.2 수집 관리 (기존 이식)
- `jobs`: `lawd_cd`, `deal_ymd`, `status`, `total_count`, `stored_count`, `fetched_at`, `checked_at`, `attempts`, `next_try_at`, `error`, `q_blank`, `q_dup`, `q_ymd_bad`, `q_sgg_bad`, `q_cancelled`
- `api_usage`: `day`, `calls`
- `changes`: `at`, `lawd_cd`, `deal_ymd`, `before`, `after`
- `address_points`: `road_key`(도로명코드 12자리 + 지하여부 + 건물본번 + 건물부번) PK, `lon`, `lat`, `bld_nm`, `source_month`. 위치정보요약DB 중 건물용도가 공동주택인 출입구만 적재한다(건물마다 출입구 일련번호가 가장 작은 것).

### 4.3 `complexes` (단지·지역 대응)
| 컬럼 | 설명 |
|---|---|
| `apt_seq` PK | 단지 식별자 |
| `apt_nm`, `jibun`, `road_nm`, `build_year` | 가장 최근 거래 기준 단지 정보 |
| `api_sgg_cd`, `api_umd_cd`, `api_umd_nm` | API 원본 코드 |
| `lon`, `lat` | WGS84 좌표 |
| `geocode_status` | `pending` / `ok` / `failed` / `manual` |
| `geocode_source` | `road`(위치정보요약DB 도로명주소 매칭) / `manual` |
| `geocoded_at` | 좌표를 정한 시각 |
| `region_sgg_cd`, `region_umd_cd` | 최신 경계 기준으로 판정한 시군구(5자리)·읍면동(8자리: 시군구 5 + 읍면동 3. 리 단위는 읍·면으로 묶는다) |
| `region_match` | `within` / `nearest` / `none` |
| `boundary_version` | 판정에 쓴 경계 버전 |
| `sgg_mismatch` | `api_sgg_cd` ≠ `region_sgg_cd` |

### 4.4 경계
- `boundary_versions`: `version`(예: `2026-10`), `source`, `fetched_at`, `is_active`, `note`. 활성 버전은 항상 하나다.
- `regions`: `boundary_version`, `region_cd`, `level`(`sido`/`sgg`/`umd`), `name`, `parent_cd`
- GeoJSON 파일:
  - `static/geo/{version}/sido.json`, `sgg.json`
  - `umd_{시도코드}.json` (읍면동은 시도별로 분할)
  - 좌표계는 EPSG:4326이고, 웹 표시용으로 단순화한다.
- 시군구·시도 폴리곤은 별도 파일을 받지 않고, 최신 법정동 폴리곤을 `lawd_codes.csv` 코드 기준으로 합쳐(dissolve) 만든다. 그래서 지도와 데이터 코드가 항상 일치한다.

### 4.5 `agg_month` (월별 집계)
- 기본키: `(boundary_version, level, region_cd, ym, size_band)`
  - `level`: `nation` / `sido` / `sgg` / `umd`
  - `size_band`: `all` / `le60`(60㎡ 이하) / `60_85`(60㎡ 초과 85㎡ 이하) / `gt85`(85㎡ 초과)
- 지표: `n_trades`, `median_price`, `p25_price`, `p75_price`, `mean_price`, `median_ppm2`
- 집계 규칙:
  - 해제 거래(`is_cancelled`)는 제외한다.
  - 완전 중복 행은 1건으로 센다.
  - 지역은 `complexes.region_*`(최신 경계)을 기준으로 한다.
  - 좌표가 없거나 판정이 `none`인 단지의 거래는 시군구를 `api_sgg_cd`로 대신 집계하고, 읍면동 집계에서는 빠진다.
- `agg_coverage`: `(boundary_version, sgg_cd, ym, n_total, n_umd_assigned)`. 읍면동 커버리지 표시용이다.
- `agg_dirty`: `(sgg_cd, ym)`. 다시 집계할 대상이다. 단지의 `api_sgg_cd`와 `region_sgg_cd`가 다르면 두 시군구를 모두 표시한다.

## 5. 데이터 흐름

### 5.1 매일 자동 처리
1. **수집 (06:00 KST 시작)**: 기존 로직(최근 3개월 재수집 → 재확인 → 과거 자료, 하루 한도까지)을 그대로 쓴다. 작업 하나를 저장할 때마다 다음을 한다.
   - 처음 보는 `apt_seq`를 `complexes`에 `pending`으로 등록하고, 기존 단지는 최근 정보로 갱신한다.
   - 해당 `(lawd_cd, deal_ymd)`를 `agg_dirty`에 추가한다.
2. **좌표 연결 (10분마다)**: `pending` 단지의 거래들에 있는 도로명주소 키(`road_nm_sgg_cd`+`road_nm_cd`, `road_nmb_cd`, `road_nm_bonbun`, `road_nm_bubun`)를 `address_points`와 맞대어 좌표를 정한다(SQL 한 번, API 호출 없음).
   - 키가 여러 개 맞으면 가장 많은 거래가 가진 키를 쓴다.
   - 맞는 키가 없으면 `failed`로 둔다. `address_points`를 새로 적재하면 `failed` 단지를 다시 `pending`으로 돌려 재시도한다.
3. **지역 판정 (좌표가 생긴 직후)**: 해당 시도의 활성 버전 법정동 폴리곤을 STRtree로 만들어(시도별로 메모리 캐시) 포함 여부를 판정한다.
   - 포함되는 폴리곤이 없으면 200m 안의 가장 가까운 폴리곤에 배정하고 `nearest`로 표시한다. 그보다 멀면 `none`이다.
   - 판정된 단지의 거래가 있는 `(sgg, ym)`을 `agg_dirty`에 추가한다.
4. **증분 집계 (수집 종료 직후 + 매시 정각)**: `agg_dirty`의 `(sgg, ym)`마다 하나의 트랜잭션으로 처리한다.
   - 그 시군구의 읍면동과 시군구, 소속 시도, 전국의 해당 월을 다시 계산한다(`percentile_cont`).
   - 성공하면 dirty 행을 지운다.

### 5.2 최초 구축 (이전 직후)
- 위치정보요약DB를 처음 적재한 뒤, 전체 단지 좌표 연결은 SQL 몇 번으로 끝난다(수 분).
- 단지마다 판정이 끝나는 대로 증분 집계가 반영된다.
- 전부 끝나면 전체 집계를 한 번 다시 계산하고, 증분 결과와 대조해 검증한다.

### 5.3 경계 갱신 (수동, 분기나 반기마다)
1. 로컬에서 `python -m geo.boundaries --version YYYY-MM`을 실행한다.
   - 브이월드·국가공간정보포털에서 내려받은 최신 읍면동 경계 SHP(`LT_C_ADEMD_INFO`, 속성 `emd_cd` 8자리)를 읽는다.
   - 경계 코드가 `lawd_codes.csv`에 없으면 `geo/code_map.csv`(옛 읍면동 코드 → 새 코드)로 바꾸고, 그래도 없으면 목록을 출력하고 중단한다.
   - EPSG:4326으로 변환하고 단순화한 뒤, `lawd_codes.csv` 기준으로 시군구·시도를 합친다.
   - GeoJSON과 `regions` 시드 파일을 생성한다.
2. 커밋하고 배포한다. 서버가 새 버전을 `is_active=false`로 등록한다.
3. 백그라운드에서 전체 단지를 새 버전으로 재판정하고 전체 집계를 계산한다. 단지 테이블의 활성 판정 컬럼은 전환 시점에만 바꾸고, 재판정 결과는 임시 테이블에 둔다.
4. 끝나면 하나의 트랜잭션으로 활성 버전을 전환하고, 이전 버전 집계와 GeoJSON을 정리한다. 중간에 실패하면 전환하지 않는다.

### 5.4 행정구역 개편 대응 (①에서는 수동 절차, ②에서 자동화)
- 감지 신호:
  - 전 기간 0건인 시군구
  - 요청 코드와 응답 `sggCd`의 불일치
  - `sgg_mismatch` 단지 증가
- 조치:
  1. `lawd_codes.csv`를 수정한다.
  2. 해당 코드의 전 기간을 재수집한다.
  3. 옛 코드 행을 정리한다.
  4. 경계를 갱신한다(5.3).

## 6. 화면

공통:
- 공유 비밀번호로 로그인한다. 상단 메뉴는 대시보드 · 추이 · 지도 · 단지 · 추출 · 수집 현황이다.
- 차트에서 최근 2개월(신고기한 30일)은 "잠정"으로 음영 표시한다.
- 화면 조건은 URL 쿼리에 담아 공유하거나 재현할 수 있게 한다.

| 경로 | 우선순위 | 내용 |
|---|---|---|
| `/login` | — | 비밀번호 입력, IP당 분당 5회 제한 |
| `/` | — | 전국 요약(최근 확정월 거래량, 중위가, 전년 동월 대비), 24개월 추이, 전년 대비 상승·하락 상위 시군구 |
| `/trends` | a | 지역 다중 선택(최대 8개, 수준 혼합 가능), 면적 구간, 지표(중위가 / ㎡당 중위가 / 거래량 / 25~75% 범위), 기간 / 옵션: 3개월 이동평균, 기준월=100 지수 / 표 보기, PNG 저장 |
| `/map` | b | 시도 → 시군구 → 읍면동 클릭 드릴다운, 지표(특정 월이나 기간 값, 전년 대비 %) / 순위표 / 지역 클릭 시 미니 추이와 `/trends` 링크 / 읍면동 커버리지 표시 |
| `/complexes`, `/complexes/<apt_seq>` | c | 단지명·지역 검색 / 상세: 계약일×가격 산점도(면적 구간별 색), 거래 목록, 면적·층 분포, 위치와 좌표 변환 상태 |
| `/export` | d | 조건(지역, 기간, 면적 구간, 해제 거래 포함 여부), 대상(원본 / 월별 집계), 형식(CSV / Parquet), 코드북 다운로드 |
| `/status` | — | 기존 내용 + 좌표 변환 커버리지, 실패 단지 목록과 수동 좌표 입력, 집계 대기열 수, DB 용량(%), 활성 경계 버전 |

## 7. API

| 경로 | 설명 |
|---|---|
| `GET /api/regions?level=&parent=` | 지역 목록 (활성 경계 버전) |
| `GET /api/agg?level=&codes=&from=&to=&band=` | 추이 데이터 |
| `GET /api/map?level=&parent=&ym=` 또는 `&from=&to=` | 지도·순위 값 |
| `GET /api/complexes?q=&region=` | 단지 검색 |
| `GET /api/complexes/<apt_seq>/trades` | 단지 거래 이력 |
| `GET /export.csv`, `GET /export.parquet` | 조건별 스트리밍 추출(COPY). 원본은 한 요청당 최대 5년 |
| `POST /api/complexes/<apt_seq>/coords` | 수동 좌표 입력 |
| `GET /api/trades`, `/api/status`, `/api/quality` | 기존 유지 |

- 파라미터 검증에 실패하면 400과 한국어 메시지를 돌려준다.
- 집계 조회 응답에는 다음 갱신 예정 시각까지 `Cache-Control`을 붙인다.
- 기존 `/download.csv`는 `/export.csv`로 리다이렉트한다.

## 8. 오류 처리

| 영역 | 상황 | 처리 |
|---|---|---|
| 수집 | API 한도·오류 | 기존과 같음(다음 날 06:00까지 대기, 지수 백오프) |
| 수집 | DB 연결 끊김 | 풀이 재연결. 작업 저장은 한 트랜잭션이라 부분 저장 없음 |
| 좌표 연결 | 도로명주소 키가 없거나 `address_points`에 없음 | `failed`. 위치정보요약DB를 새로 적재하면 재시도 |
| 좌표 연결 | 끝내 실패 | `/status`에서 수동 입력. `manual`은 자동 처리가 덮어쓰지 않음 |
| 지역 판정 | 폴리곤 밖 | 200m 이내면 `nearest`, 아니면 `none` |
| 집계 | 실패 | `(sgg, ym)` 단위 트랜잭션. dirty에 남겨 재시도, 로그 기록 |
| 경계 전환 | 재계산 실패 | 전환하지 않음. 이전 버전 유지 |
| 웹 | 잘못된 파라미터·빈 결과 | 400 + 메시지 / "데이터 없음" 명시 |
| 추출 | 대용량 | 원본은 요청당 최대 5년. Railway 요청 시간 제한은 구현할 때 확인해 조정 |

## 9. 테스트

pytest를 쓰고 TDD로 진행한다.
- **단위**:
  - 실제 API 응답 XML 샘플을 파싱한다.
  - 작업 선택, 재확인 주기, 누락 점검 규칙을 검증한다.
  - 위치정보요약DB 행 파싱·필터·좌표계 변환(EPSG:5179 → 4326)을 작은 샘플 파일로 확인한다.
  - 좌표 연결 SQL이 거래가 가장 많은 키를 고르고, `manual`을 덮어쓰지 않는지 확인한다.
  - 지역 판정의 내부·경계·200m 이내·미배정 경우를 작은 테스트 폴리곤으로 확인한다.
- **DB** (docker compose Postgres):
  - 집계 SQL 결과가 같은 데이터로 pandas에서 계산한 중위값·백분위와 일치한다.
  - 증분 집계 결과가 전체 재계산 결과와 같다.
  - 경계 버전 전환이 원자적이다.
- **웹**: Flask 테스트 클라이언트로 응답 형식, 파라미터 검증, 로그인 보호, 추출 형식(CSV 헤더·인코딩, Parquet 스키마)을 확인한다.
- **화면**: 로컬 서버를 띄우고 브라우저로 각 화면을 확인한다.

## 10. 이전 절차 (SQLite → Postgres)

1. Railway에 Postgres를 추가하고, web 서비스에 `DATABASE_URL`을 연결한다.
2. 새 코드를 `COLLECT_ENABLED=false`로 배포한다(수집 중지). 이전 버전은 git 태그로 남긴다.
3. Railway에서 `python scripts/migrate_sqlite.py`를 한 번 실행한다.
   - 볼륨의 `trades.db`를 Postgres로 복사한다(`trades`, `jobs`, `api_usage`, `changes`).
   - 작업별 건수가 원본과 같은지 검증한다.
4. 로컬에서 위치정보요약DB와 경계를 적재한다(`geo.address_points`, `geo.boundaries`).
5. `COLLECT_ENABLED=true`로 바꾼다. 좌표 연결과 집계가 자동으로 시작된다.
6. 1주일 동안 안정적으로 돌면 SQLite 백업을 내려받고 볼륨을 제거한다. 그 전까지는 이전 태그로 롤백할 수 있다.

## 11. 환경변수 (추가·변경)

| 이름 | 기본값 | 설명 |
|---|---|---|
| `DATABASE_URL` | (필수) | Postgres 연결 문자열 |
| `APP_PASSWORD` | (필수) | 공유 로그인 비밀번호 |
| `SECRET_KEY` | (필수) | 세션 서명 키 |
| `COLLECT_ENABLED` | `true` | 수집 스케줄러 사용 여부 |
| `DB_LIMIT_MB` | `5000` | 용량 사용률 기준(기존 `VOLUME_LIMIT_MB` 대체) |
| `DATA_DIR` | `data` | SQLite 이전 원본 위치(이전 후 불필요) |

기존 `MOLIT_SERVICE_KEY`, `START_YMD`, `DAILY_LIMIT`, `REQUEST_INTERVAL`, `REFRESH_AT`, `STORAGE_STOP_PCT` 등은 유지한다.

## 12. 갱신 주기 요약
- 위치정보요약DB: 월 1회 로컬에서 `python -m geo.address_points --dir <압축 푼 폴더>` 실행(운영 DB의 공개 연결 문자열 사용)
- 경계: 분기·반기 1회 `python -m geo.boundaries` 실행 후 커밋·배포

## 13. 범위 밖 (이번에 하지 않음)
- 운영 알림(이메일·메신저), 개편 자동 감지·재수집 → ②
- 학생용 공개 범위, 실습 데이터셋 → ③
- 다중 사용자 계정·권한, 외부 서비스
- 전월세·오피스텔 등 다른 데이터셋
- PostGIS, 배경지도 위 단지 위치 표시(필요하면 카카오·VWorld 지도를 브라우저에서 실시간 호출하는 방식으로, 좌표 저장 없이)
- 지번 주소 기반 좌표 매칭(위치정보요약DB에는 지번이 없음)
- 수집기 별도 worker 서비스 분리

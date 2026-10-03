# molit-apt-trade

국토교통부 **아파트 매매 실거래가 상세자료**(공공데이터포털 Open API)를 자동으로 수집·최신화해 보여주는 웹앱 (Flask + Postgres, Railway 배포).

> rt.molit.go.kr은 크롤러 등 자동화 수단 이용을 금지하고, 대량·반복 이용은 공공데이터포털 API를 쓰도록 안내합니다. 이 앱은 API만 사용합니다.

## 수집 방식
- 2006년 1월(공개 시작) 계약분부터 **오래된 달 순서로**, 전국 256개 시군구(`lawd_codes.csv`)별로 받음
- 매일 06:00(KST, `REFRESH_AT`)에 시작해 하루 한도까지 수집. 최근 자료를 먼저 갱신한 뒤 남은 한도로 과거 자료를 받음(2006~2025년 약 6.1만 작업, 9~10일)
- 호출 간격 1.5초, 하루 8,000회 이하(`DAILY_LIMIT`). 한도에 닿거나 API가 한도 초과(22)를 돌려주면 다음 날 06:00까지 대기
- 최근 3개월은 매일 06:00(KST, `REFRESH_AT`)에 다시 받음(신고기한 30일, 계약 해제 반영). 그 이전 1년은 7일마다, 더 오래된 달은 180일마다 전체 건수만 확인해 바뀌었으면 다시 받음
- DB 사용률(Postgres 크기 / `DB_LIMIT_MB`)이 90%(`STORAGE_STOP_PCT`)에 닿으면 과거 자료 수집을 멈추고 최근 자료만 갱신
- 실패한 작업은 지수 백오프로 자동 재시도
- `dealAmount`는 정수(만원), `수집시각` 열 추가, CSV는 `utf-8-sig`

## 누락 점검 (`/status`)
1. 작업(시군구×계약월)마다 API `totalCount`와 저장 건수 비교, 다르면 재시도
2. 받은 달이 모두 0건인 시군구 → 행정구역 개편으로 코드가 바뀌었을 가능성 경고
3. 요청 코드와 응답 `sggCd`가 다른 경우
4. 요청 계약월과 `dealDate` 불일치
5. 완전 중복 행, 해제 거래 수
6. 지오코딩·분석용 핵심 필드(지번·도로명 등)의 빈 값 비율
7. 재수집 시 건수 변동 이력
8. 단지 좌표 확보율, 좌표 못 찾은 단지(수동 입력), 경계 밖·근접 배정, API 시군구와 좌표 시군구 불일치

## 시군구 코드
`lawd_codes.csv`는 법정동코드(2022.9)에 이후 개편을 반영하고 API 조회로 검증한 목록입니다.
강원·전북 특별자치도(51·52), 군위군 대구 편입, 부천·화성 구 신설, 인천 개편(제물포·영종·서구·검단), 전남광주통합특별시(12xxx).
API는 과거 자료도 새 코드로만 제공하므로 개편이 있으면 이 파일을 고쳐야 합니다.

## 구조
| 경로 | 역할 |
|---|---|
| `settings.py` | 환경변수(`.env`)·KST 시각·수집 설정 |
| `db.py`, `migrations/` | Postgres 커넥션 풀, SQL 마이그레이션(시작할 때 자동 적용) |
| `collector/` | 국토부 API 호출(`api`), 저장(`store`), 작업 선택·배치(`jobs`), 누락 점검(`quality`) |
| `web/` | 화면(`pages`), JSON API(`api`), 로그인(`auth`) |
| `geo/` | 단지 등록·좌표 연결(`complexes`, `locate`), 지역 판정(`assign`), 경계 버전(`versions`), 10분 주기 처리(`pipeline`), 로컬 도구(`address_points`, `boundaries`) |
| `static/geo/`, `geo_data/` | 경계 버전별 화면용 GeoJSON / 판정용 폴리곤·지역 목록 |
| `scheduler.py` | 백그라운드 수집 작업 |
| `scripts/migrate_sqlite.py` | 옛 SQLite → Postgres 1회 이전 |

모든 화면과 API는 로그인이 필요합니다(공유 비밀번호 `APP_PASSWORD`).

## 엔드포인트
| 경로 | 설명 |
|---|---|
| `/` | 거래 목록 (시도·시군구·연도·계약월·검색 필터) |
| `/status` | 수집 현황·누락 점검 |
| `/download.csv` | 현재 필터 조건으로 CSV 다운로드 (지역·연도·계약월 중 하나 필수) |
| `/api/trades`, `/api/status`, `/api/quality` | JSON |

## 환경변수
| 이름 | 기본값 | 설명 |
|---|---|---|
| `MOLIT_SERVICE_KEY` | (필수) | 공공데이터포털 인증키 |
| `DATABASE_URL` | (필수) | Postgres 연결 문자열. Railway는 `${{Postgres.DATABASE_URL}}` |
| `APP_PASSWORD` | (필수) | 공유 로그인 비밀번호 |
| `SECRET_KEY` | (필수) | 세션 서명 키 (`python -c "import secrets;print(secrets.token_hex(32))"`) |
| `COLLECT_ENABLED` | `true` | `false`면 수집 스케줄러를 켜지 않음 |
| `START_YMD` | `200601` | 수집 시작 계약월 |
| `DAILY_LIMIT` | `8000` | 하루 호출 상한 |
| `REQUEST_INTERVAL` | `1.5` | 호출 간격(초) |
| `REFRESH_AT` | `06:00` | 매일 수집을 시작하는 시각(KST). 최근 3개월 재수집도 이때 |
| `DB_LIMIT_MB` | `5000` | 용량 사용률 기준(Railway Hobby 볼륨 5GB) |
| `STORAGE_STOP_PCT` | `90` | 이 사용률 이상이면 과거 자료 수집 중단 |
| `DB_POOL_MAX` | `8` | Postgres 커넥션 풀 크기 |
| `PORT` | (Railway가 지정) | 웹 서버 포트 |
| `DATA_DIR` | `data` | 옛 SQLite 위치(이전 스크립트 기본 경로) |

앱은 스케줄러가 import 시점에 시작되므로 gunicorn을 `--workers 1`로, `--preload` 없이 실행해야 합니다.

인증키·비밀번호는 로컬은 `.env`, Railway는 Variables에 둡니다(저장소에 올리지 않음).

## 로컬 실행
Postgres 17을 설치하고(`winget install -e --id PostgreSQL.PostgreSQL.17 --interactive`) `molit_dev`, `molit_test` DB를 만든 뒤 `.env`에 다음을 넣습니다.
```
MOLIT_SERVICE_KEY=발급받은키
DATABASE_URL=postgresql://postgres:<pw>@localhost:5432/molit_dev
TEST_DATABASE_URL=postgresql://postgres:<pw>@localhost:5432/molit_test
APP_PASSWORD=로컬비밀번호
SECRET_KEY=임의의긴문자열
COLLECT_ENABLED=false
```
```bash
# Windows 예시
py -3.14 -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest
.venv/Scripts/python app.py        # http://localhost:8000
```

## 지리 데이터
좌표는 주소정보누리집 위치정보요약DB(도로명주소 매칭), 경계는 브이월드·국가공간정보포털 읍면동 SHP로 만듭니다.
갱신 절차: `docs/runbooks/geo-data.md`

# Railway: SQLite → Postgres 이전 절차

명세 §10을 따른다. 모든 단계는 사람이 Railway 대시보드·CLI에서 직접 한다.

## 0. 준비
- Railway 플랜이 Hobby 이상인지 확인(볼륨 5GB, Postgres 서비스 사용).
- 되돌릴 지점을 남긴다: `git tag pre-postgres <현재 운영 중인 main 커밋>` 후 `git push origin pre-postgres`.

## 1. Postgres 추가
1. 프로젝트 → New → Database → PostgreSQL.
2. web 서비스 Variables:
   - `DATABASE_URL` = `${{Postgres.DATABASE_URL}}` (내부 네트워크 주소)
   - `APP_PASSWORD` = 연구실 공유 비밀번호 (필수: 없으면 앱이 시작되지 않는다)
   - `SECRET_KEY` = `python -c "import secrets;print(secrets.token_hex(32))"` 결과
   - `COLLECT_ENABLED` = `false`
   - `DB_LIMIT_MB` = `5000`
   - 예전 변수 `COLLECTOR_DISABLED`, `VOLUME_LIMIT_MB`는 지금 지우지 말고 현재 값을 적어 둔다(새 코드는 무시한다. 롤백에 필요하므로 3단계 검증 통과 뒤에 지운다)
3. 기존 볼륨(`/data`)은 **그대로 둔다**(SQLite 원본).

## 2. 새 코드 배포
`feature/analysis-dashboard` 브랜치를 main에 합치거나, web 서비스의 배포 브랜치를 이 브랜치로 바꿔 배포한다.
새 코드가 배포되면 옛 프로세스가 교체되어 옛 SQLite 수집기가 멈춘다. 이전 스크립트가 요구하는 "옛 수집기를 먼저 멈출 것"이 이것으로 충족된다.
또 `COLLECT_ENABLED=false`인 새 코드는 수집 데이터를 Postgres에 쓰지 않는다(수집 스케줄러를 켜지 않음. 스키마 마이그레이션은 시작할 때 적용한다).
배포 로그에서 마이그레이션 오류가 없는지, `/login`이 열리는지 확인한다.

## 3. 데이터 이전
```bash
railway ssh --service web
nohup python scripts/migrate_sqlite.py --sqlite /data/trades.db > /data/migrate.log 2>&1 &
tail -f /data/migrate.log
```
SSH 연결이 끊겨도 이전이 중단되지 않도록 백그라운드로 실행하고, 로그 파일로 진행을 본다(`tail -f`는 Ctrl+C로 빠져나와도 이전은 계속된다).
복사 중 데이터베이스 형식 오류로 실패하면 전체 복사가 롤백된 것이므로 그대로 다시 실행해도 안전하다.
스크립트는 trades·jobs·api_usage·changes를 한 트랜잭션으로 복사한 뒤, 표별 건수와 (지역, 계약월)별 거래 건수를 원본과 비교한다. 하나라도 다르면 `검증 실패`를 출력하고 종료 코드 1을 돌려준다.
- Postgres의 네 표 중 하나라도 비어 있지 않으면 중단한다. 다시 옮기려면 `--replace`를 붙인다(주의: Postgres의 네 표를 모두 지우고 다시 복사).
- 작업별 `stored_count`와 실제 건수가 다른 경우는 `경고`로만 출력된다(원본 자체의 불일치이며 복사 오류가 아니다).

마지막 줄이 `검증 통과: 표별 건수와 계약월·지역별 거래 건수가 원본과 같습니다.`인지 확인한다.
검증에 실패해도 복사한 행은 Postgres에 남는다. 그래서 재시도는 `--replace`로 한다.
실패하면 `COLLECT_ENABLED=false`를 그대로 두고, 출력된 불일치 줄(`... 불일치 ...`)을 기록해 보고한 뒤 `--replace`로 다시 실행한다.

## 4. 수집 재개
- `/status`에서 거래 건수·작업 수가 이전과 같은지 확인한다.
- 예전 변수 `COLLECTOR_DISABLED`, `VOLUME_LIMIT_MB`를 지운다(롤백이 필요 없다고 판단한 경우에만. 롤백하려면 3단계에서 적어 둔 값이 필요하다).
- Variables에서 `COLLECT_ENABLED`를 지운다(기본 `true`). 재배포하면 수집이 바로 시작된다(`REFRESH_AT` 기본 00:00 KST 이후이므로 즉시 국토부 API를 호출해 그날 남은 한도를 쓴다). 재배포 시점을 정해서 한다. `/status`로 수집이 도는지 확인한다.

## 5. 정리 (1주일 동안 안정적으로 돈 뒤)
- SQLite 원본을 내려받아 보관한다(예: `railway ssh --service web` 안에서 `gzip -c /data/trades.db > /data/trades.db.gz` 후 Railway 볼륨 백업 또는 파일 다운로드 기능 사용).
- web 서비스에서 볼륨을 분리·삭제한다.

## 롤백 (5단계 전까지)
Railway는 태그가 아니라 브랜치·이전 배포를 배포한다. 다음 중 하나로 이전 코드를 되살린다.
- Railway web 서비스의 Deployments 목록에서 이전(이전 전) 배포를 Redeploy한다.
- 또는 `git branch pre-postgres-rollback pre-postgres`로 만든 브랜치를 push하고, web 서비스의 배포 브랜치를 그 브랜치로 바꾼다.

그다음 Variables에 `COLLECTOR_DISABLED`·`VOLUME_LIMIT_MB`를 1단계에서 적어 둔 원래 값으로 되돌린다. SQLite 볼륨이 그대로라 이전 상태로 동작한다.
4단계 이후에 롤백하면 그동안 Postgres에 수집된 자료는 쓰이지 않고 버려진다(SQLite에는 없다).

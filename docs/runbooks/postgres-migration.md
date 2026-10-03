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
   - 예전 변수 `COLLECTOR_DISABLED`, `VOLUME_LIMIT_MB`가 있으면 삭제
3. 기존 볼륨(`/data`)은 **그대로 둔다**(SQLite 원본).

## 2. 새 코드 배포
`feature/analysis-dashboard` 브랜치를 main에 합치거나, web 서비스의 배포 브랜치를 이 브랜치로 바꿔 배포한다.
새 코드가 배포되면 옛 프로세스가 교체되어 옛 SQLite 수집기가 멈춘다. 이전 스크립트가 요구하는 "옛 수집기를 먼저 멈출 것"이 이것으로 충족된다.
또 `COLLECT_ENABLED=false`인 새 코드는 Postgres에 쓰지 않는다(수집 스케줄러를 켜지 않음).
배포 로그에서 마이그레이션 오류가 없는지, `/login`이 열리는지 확인한다.

## 3. 데이터 이전
```bash
railway ssh --service web
python scripts/migrate_sqlite.py --sqlite /data/trades.db
```
스크립트는 trades·jobs·api_usage·changes를 한 트랜잭션으로 복사한 뒤, 표별 건수와 (지역, 계약월)별 거래 건수를 원본과 비교한다. 하나라도 다르면 `검증 실패`를 출력하고 종료 코드 1을 돌려준다.
- Postgres의 네 표 중 하나라도 비어 있지 않으면 중단한다. 다시 옮기려면 `--replace`를 붙인다(주의: Postgres의 네 표를 모두 지우고 다시 복사).
- 작업별 `stored_count`와 실제 건수가 다른 경우는 `경고`로만 출력된다(원본 자체의 불일치이며 복사 오류가 아니다).

마지막 줄이 `검증 통과: 표별 건수와 계약월·지역별 거래 건수가 원본과 같습니다.`인지 확인한다.
실패하면 `COLLECT_ENABLED=false`를 그대로 두고, 출력된 불일치 줄(`... 불일치 ...`)을 기록해 보고한 뒤 `--replace`로 다시 실행한다.

## 4. 수집 재개
- `/status`에서 거래 건수·작업 수가 이전과 같은지 확인한다.
- Variables에서 `COLLECT_ENABLED`를 지운다(기본 `true`). 재배포 후 다음 06:00(KST)부터 수집되는지 `/status`로 확인한다.

## 5. 정리 (1주일 동안 안정적으로 돈 뒤)
- SQLite 원본을 내려받아 보관한다(예: `railway ssh --service web` 안에서 `gzip -c /data/trades.db > /data/trades.db.gz` 후 Railway 볼륨 백업 또는 파일 다운로드 기능 사용).
- web 서비스에서 볼륨을 분리·삭제한다.

## 롤백 (5단계 전까지)
web 서비스를 `pre-postgres` 태그 커밋으로 다시 배포하고, Variables에 `COLLECTOR_DISABLED`·`VOLUME_LIMIT_MB`를 원래 값으로 되돌린다. SQLite 볼륨이 그대로라 이전 상태로 동작한다.

"""지리 처리 중간에 다른 모듈(집계 등)이 끼어드는 지점. wiring.wire()가 채운다.

- ON_REGION_CHANGE: fn(conn, apt_seqs) — 단지 지역이 바뀌기 직전과 직후에 한 번씩(같은 트랜잭션)
- BEFORE_ACTIVATE: fn(conn, version) — 경계 전환 트랜잭션 안, 활성화 직전. 임시 테이블 staged_regions 사용 가능
- AFTER_ACTIVATE: fn(conn, version) — 경계 전환 트랜잭션 안, 활성화 직후
"""
ON_REGION_CHANGE = []
BEFORE_ACTIVATE = []
AFTER_ACTIVATE = []

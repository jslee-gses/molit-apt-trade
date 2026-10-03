-- 계약월 조건(WHERE deal_ymd = ...) 집계·내보내기용 인덱스
CREATE INDEX IF NOT EXISTS ix_trades_ymd ON trades (deal_ymd);

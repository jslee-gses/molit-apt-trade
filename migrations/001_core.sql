-- 원본 거래: 수집 단위(lawd_cd x deal_ymd)마다 통째로 교체 저장한다
CREATE TABLE trades (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lawd_cd TEXT NOT NULL,
    deal_ymd TEXT NOT NULL,
    sgg_cd TEXT, umd_cd TEXT, land_cd TEXT, bonbun TEXT, bubun TEXT,
    road_nm TEXT, road_nm_sgg_cd TEXT, road_nm_cd TEXT, road_nm_seq TEXT, road_nmb_cd TEXT,
    road_nm_bonbun TEXT, road_nm_bubun TEXT,
    umd_nm TEXT, apt_nm TEXT, jibun TEXT,
    exclu_use_ar NUMERIC,
    deal_year TEXT, deal_month TEXT, deal_day TEXT,
    deal_amount INTEGER,
    floor SMALLINT,
    build_year SMALLINT,
    apt_seq TEXT, cdeal_type TEXT, cdeal_day TEXT, dealing_gbn TEXT, estate_agent_sgg_nm TEXT,
    rgst_date TEXT, apt_dong TEXT, sler_gbn TEXT, buyer_gbn TEXT, land_leasehold_gbn TEXT,
    deal_date DATE,
    collected_at TIMESTAMP,
    price_per_m2 NUMERIC GENERATED ALWAYS AS (
        CASE WHEN exclu_use_ar > 0 THEN round(deal_amount / exclu_use_ar, 1) END) STORED,
    is_cancelled BOOLEAN GENERATED ALWAYS AS (COALESCE(btrim(cdeal_type), '') <> '') STORED
);
CREATE INDEX ix_trades_job ON trades (lawd_cd, deal_ymd);
CREATE INDEX ix_trades_apt ON trades (apt_seq, deal_date);
CREATE INDEX ix_trades_date ON trades (deal_date);

CREATE TABLE jobs (
    lawd_cd TEXT NOT NULL,
    deal_ymd TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',   -- pending / done / incomplete / error
    total_count INTEGER,
    stored_count INTEGER,
    fetched_at TIMESTAMP,
    checked_at TIMESTAMP,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_try_at TIMESTAMP,
    error TEXT,
    q_blank JSONB,
    q_dup INTEGER,
    q_ymd_bad INTEGER,
    q_sgg_bad INTEGER,
    q_cancelled INTEGER,
    PRIMARY KEY (lawd_cd, deal_ymd)
);
CREATE INDEX ix_jobs_status ON jobs (status, deal_ymd);

CREATE TABLE api_usage (day DATE PRIMARY KEY, calls INTEGER NOT NULL);

-- 재수집 시 건수 변동 기록(늦은 신고·해제 추적)
CREATE TABLE changes (
    at TIMESTAMP NOT NULL,
    lawd_cd TEXT NOT NULL,
    deal_ymd TEXT NOT NULL,
    before INTEGER,
    after INTEGER
);
CREATE INDEX ix_changes_at ON changes (at DESC);

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

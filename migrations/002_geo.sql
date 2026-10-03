-- 도로명주소 키: 도로명코드 12자리(시군구 5 + 도로 7) | 지하여부(0/1) | 건물본번 | 건물부번
-- 거래(API)와 위치정보요약DB의 표기 차이(앞자리 0, 빈 값)를 없앤다. geo/keys.py와 같은 규칙.
CREATE FUNCTION road_key(sgg TEXT, road TEXT, under TEXT, bon TEXT, bu TEXT) RETURNS TEXT
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN btrim(COALESCE(sgg, '')) ~ '^[0-9]{5}$'
         AND btrim(COALESCE(road, '')) ~ '^[0-9]{7}$'
         AND btrim(COALESCE(bon, '')) ~ '^[0-9]{1,9}$'
        THEN btrim(sgg) || btrim(road)
             || '|' || CASE WHEN btrim(COALESCE(under, '')) = '1' THEN '1' ELSE '0' END
             || '|' || (btrim(bon)::int)::text
             || '|' || (CASE WHEN btrim(COALESCE(bu, '')) ~ '^[0-9]{1,9}$' THEN btrim(bu)::int ELSE 0 END)::text
    END
$$;

-- 단지(aptSeq). 좌표와 최신 경계 기준 지역 판정 결과를 함께 둔다
CREATE TABLE complexes (
    apt_seq TEXT PRIMARY KEY,
    apt_nm TEXT,
    jibun TEXT,
    road_nm TEXT,
    build_year SMALLINT,
    api_sgg_cd TEXT,
    api_umd_cd TEXT,
    api_umd_nm TEXT,
    last_deal_date DATE,
    lon DOUBLE PRECISION,
    lat DOUBLE PRECISION,
    geocode_status TEXT NOT NULL DEFAULT 'pending',   -- pending / ok / failed / manual
    geocode_source TEXT,                              -- road / manual
    geocoded_at TIMESTAMP,
    region_sgg_cd TEXT,
    region_umd_cd TEXT,
    region_match TEXT,                                -- within / nearest / none
    boundary_version TEXT,
    sgg_mismatch BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX ix_complexes_status ON complexes (geocode_status);
CREATE INDEX ix_complexes_region ON complexes (region_sgg_cd, region_umd_cd);

-- 위치정보요약DB 중 필요한 건물 출입구 좌표(WGS84)
CREATE TABLE address_points (
    road_key TEXT PRIMARY KEY,
    lon DOUBLE PRECISION NOT NULL,
    lat DOUBLE PRECISION NOT NULL,
    bld_nm TEXT,
    source_month TEXT NOT NULL
);

CREATE TABLE boundary_versions (
    version TEXT PRIMARY KEY,
    source TEXT,
    loaded_at TIMESTAMP NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT false,
    note TEXT
);
CREATE UNIQUE INDEX ux_boundary_active ON boundary_versions (is_active) WHERE is_active;

CREATE TABLE regions (
    boundary_version TEXT NOT NULL REFERENCES boundary_versions ON DELETE CASCADE,
    region_cd TEXT NOT NULL,
    level TEXT NOT NULL,          -- sido / sgg / umd
    name TEXT NOT NULL,
    full_name TEXT,
    parent_cd TEXT,
    PRIMARY KEY (boundary_version, region_cd)
);
CREATE INDEX ix_regions_parent ON regions (boundary_version, level, parent_cd);

-- 일회성 작업 표식(예: 단지 초기 적재 완료)
CREATE TABLE app_flags (name TEXT PRIMARY KEY, set_at TIMESTAMP NOT NULL);

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

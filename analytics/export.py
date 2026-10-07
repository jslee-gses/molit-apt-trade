"""데이터 추출: 원본 거래·월별 집계를 CSV(utf-8-sig)·Parquet로 흘려보낸다(서버 측 커서, 메모리 일정)."""
import csv
import io
import logging

import pyarrow as pa
import pyarrow.parquet as pq

import db
from analytics import params, queries
from collector import api, codes

log = logging.getLogger(__name__)

MAX_RAW_MONTHS = 60
BATCH = 5000

_TYPED = {"deal_amount": pa.int32(), "exclu_use_ar": pa.float64(), "floor": pa.int16(),
          "build_year": pa.int16()}
# (SQL 식, 내보낼 이름, Arrow 형)
RAW_COLUMNS = (
    [("t.lawd_cd", "lawd_cd", pa.string()), ("t.deal_ymd", "deal_ymd", pa.string())]
    + [(f"t.{c}::float8" if c == "exclu_use_ar" else f"t.{c}", api.CAMEL[c], _TYPED.get(c, pa.string()))
       for c in api.COLUMNS]
    + [("t.deal_date", "dealDate", pa.date32()), ("t.collected_at", "collected_at", pa.timestamp("s")),
       ("c.region_sgg_cd", "region_sgg_cd", pa.string()), ("c.region_umd_cd", "region_umd_cd", pa.string()),
       ("t.is_cancelled", "is_cancelled", pa.bool_())]
)
AGG_COLUMNS = [
    ("a.boundary_version", "boundary_version", pa.string()), ("a.level", "level", pa.string()),
    ("a.region_cd", "region_cd", pa.string()), ("COALESCE(r.full_name, '전국')", "region_name", pa.string()),
    ("a.ym", "ym", pa.string()), ("a.size_band", "size_band", pa.string()),
    ("a.n_trades", "n_trades", pa.int32()), ("a.median_price", "median_price", pa.float64()),
    ("a.p25_price", "p25_price", pa.float64()), ("a.p75_price", "p75_price", pa.float64()),
    ("a.mean_price", "mean_price", pa.float64()), ("a.median_ppm2", "median_ppm2", pa.float64()),
]

_FIELD_DOCS = {
    "sggCd": ("법정동 시군구코드", "거래 신고 기준 시군구 코드(5자리)"),
    "umdCd": ("법정동 읍면동코드", "시군구 안의 법정동 코드(5자리, 리 포함)"),
    "landCd": ("지번 구분", "대지·산 등 지번 구분 코드"),
    "bonbun": ("지번 본번", ""), "bubun": ("지번 부번", ""),
    "roadNm": ("도로명", ""), "roadNmSggCd": ("도로명 시군구코드", ""),
    "roadNmCd": ("도로명코드", "도로명 7자리. 시군구코드와 합쳐 도로명주소를 가리킴"),
    "roadNmSeq": ("도로명 일련번호", ""), "roadNmbCd": ("지상·지하 구분", "0 지상, 1 지하"),
    "roadNmBonbun": ("건물 본번", ""), "roadNmBubun": ("건물 부번", ""),
    "umdNm": ("법정동명", ""), "aptNm": ("단지명", ""), "jibun": ("지번", ""),
    "excluUseAr": ("전용면적", "㎡"), "dealYear": ("계약 연도", ""), "dealMonth": ("계약 월", ""),
    "dealDay": ("계약 일", ""), "dealAmount": ("거래금액", "만원"), "floor": ("층", "음수는 지하"),
    "buildYear": ("건축년도", ""), "aptSeq": ("단지 일련번호", "단지 식별자"),
    "cdealType": ("해제 여부", "O면 계약 해제 신고된 거래"), "cdealDay": ("해제사유 발생일", ""),
    "dealingGbn": ("거래 유형", "중개거래·직거래"), "estateAgentSggNm": ("중개사 소재지", ""),
    "rgstDate": ("등기일자", ""), "aptDong": ("동", ""), "slerGbn": ("매도자 구분", ""),
    "buyerGbn": ("매수자 구분", ""), "landLeaseholdGbn": ("토지임대부 여부", ""),
}
_EXTRA_DOCS = {
    "lawd_cd": ("수집 시군구코드", "API에 요청한 시군구 코드"), "deal_ymd": ("계약년월", "YYYYMM"),
    "dealDate": ("계약일", "YYYY-MM-DD"), "collected_at": ("수집시각", "KST"),
    "region_sgg_cd": ("최신 경계 시군구", "단지 위치(필지 대표점)나 법정동 코드로 판정한 시군구. 판정하지 못하면 빈 값"),
    "region_umd_cd": ("최신 경계 읍면동", "단지 위치(필지 대표점)나 법정동 코드로 판정한 읍면동 8자리. 판정하지 못하면 빈 값"),
    "is_cancelled": ("해제 거래", "1 해제, 0 정상"),
    "boundary_version": ("경계 버전", "집계에 쓴 경계 버전"),
    "level": ("지역 수준", "nation 전국 / sido 시도 / sgg 시군구 / umd 읍면동"),
    "region_cd": ("지역 코드", "전국 00, 시도 2자리, 시군구 5자리, 읍면동 8자리"),
    "region_name": ("지역 이름", ""), "ym": ("계약년월", "YYYYMM"),
    "size_band": ("면적 구간", "all 전체 / le60 60㎡ 이하 / 60_85 60~85㎡ / gt85 85㎡ 초과"),
    "n_trades": ("거래 수", "해제·금액 없음 제외, 완전 중복 1건"),
    "median_price": ("중위 거래가", "만원"), "p25_price": ("25% 거래가", "만원"),
    "p75_price": ("75% 거래가", "만원"), "mean_price": ("평균 거래가", "만원"),
    "median_ppm2": ("㎡당 중위가", "만원/㎡"),
}
CODEBOOK = [(name, *_FIELD_DOCS[name]) for name in api.FIELDS] + [(n, *d) for n, d in _EXTRA_DOCS.items()]


def parse_args(args):
    target = params.choice(args.get("target"), ("raw", "agg"), "대상", "raw")
    region = params.region_item(args["region"]) if args.get("region") else None
    # 옛 /download.csv 인자
    if not region and args.get("lawd_cd"):
        region = params.region_item(f"sgg:{args['lawd_cd']}")
    if not region and args.get("sido"):
        codes_df = codes.load_codes()
        match = codes_df.loc[codes_df["시도"] == args["sido"], "LAWD_CD"]
        if match.empty:
            raise params.BadParam(f"없는 시도입니다: {args['sido']!r}")
        region = ("sido", match.iloc[0][:2])
    rng = dict(args)
    if args.get("ymd") and not args.get("from"):
        rng["from"] = rng["to"] = args["ymd"]
    elif args.get("year") and not args.get("from"):
        rng["from"], rng["to"] = f"{args['year']}01", f"{args['year']}12"
    ym_from, ym_to = params.ym_range(rng, default_months=12,
                                     max_months=MAX_RAW_MONTHS if target == "raw" else None)
    band = params.choice(args.get("band"), [b for b, _ in params.BANDS], "면적 구간", "all")
    q = (args.get("q") or "").strip() or None
    return dict(target=target, region=region, ym_from=ym_from, ym_to=ym_to, band=band,
                include_cancelled=args.get("cancelled") in ("1", "true", "on"), q=q)


def raw_query(p):
    where = ["t.deal_ymd BETWEEN %s AND %s"]
    args = [p["ym_from"], p["ym_to"]]
    if not p["include_cancelled"]:
        where.append("NOT t.is_cancelled")
    if p["region"]:
        level, code = p["region"]
        expr = {"sido": "left(COALESCE(c.region_sgg_cd, t.lawd_cd), 2)",
                "sgg": "COALESCE(c.region_sgg_cd, t.lawd_cd)", "umd": "c.region_umd_cd"}.get(level)
        if expr:
            where.append(f"{expr} = %s")
            args.append(code)
    if p["band"] != "all":
        where.append({"le60": "t.exclu_use_ar <= 60", "60_85": "t.exclu_use_ar > 60 AND t.exclu_use_ar <= 85",
                      "gt85": "t.exclu_use_ar > 85"}[p["band"]])
    if p.get("q"):
        where.append("(t.apt_nm ILIKE %s OR t.umd_nm ILIKE %s OR t.road_nm ILIKE %s)")
        args.extend([f"%{p['q']}%"] * 3)
    cols = ", ".join(f"{expr} AS \"{name}\"" for expr, name, _ in RAW_COLUMNS)
    sql = (f"SELECT {cols} FROM trades t LEFT JOIN complexes c ON c.apt_seq = btrim(t.apt_seq) "
           f"WHERE {' AND '.join(where)} ORDER BY t.deal_date, t.lawd_cd, t.id")
    return sql, args


def agg_query(version, p):
    where = ["a.boundary_version = %s", "a.ym BETWEEN %s AND %s"]
    args = [version, p["ym_from"], p["ym_to"]]
    if p["region"]:
        where += ["a.level = %s", "a.region_cd = %s"]
        args += list(p["region"])
    if p["band"] != "all" or not p["region"]:
        where.append("a.size_band = %s")
        args.append(p["band"])
    cols = ", ".join(f"{expr} AS \"{name}\"" for expr, name, _ in AGG_COLUMNS)
    sql = (f"SELECT {cols} FROM agg_month a LEFT JOIN regions r ON r.boundary_version = a.boundary_version "
           f"AND r.region_cd = a.region_cd WHERE {' AND '.join(where)} ORDER BY a.level, a.region_cd, a.ym, a.size_band")
    return sql, args


def _rows(sql, args):
    with db.connection() as conn, conn.transaction(), conn.cursor(name="export") as cur:
        try:
            cur.execute(sql, args)
            while batch := cur.fetchmany(BATCH):
                yield batch
        except Exception:
            log.exception("추출 중 오류")
            raise


def _cell(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "1" if v else "0"
    return v


def csv_stream(sql, args, columns):
    names = [name for _, name, _ in columns]
    buf = io.StringIO()
    writer = csv.writer(buf)
    yield "﻿".encode("utf-8")
    writer.writerow(names)
    for batch in _rows(sql, args):
        for row in batch:
            writer.writerow([_cell(row[n]) for n in names])
        yield buf.getvalue().encode("utf-8")
        buf.seek(0)
        buf.truncate()
    if buf.tell():
        yield buf.getvalue().encode("utf-8")


class _Sink(io.RawIOBase):
    """ParquetWriter가 쓰는 바이트를 모아 두었다가 조금씩 내보낸다."""

    def __init__(self):
        self.chunks, self.pos = [], 0

    def writable(self):
        return True

    def write(self, b):
        self.chunks.append(bytes(b))
        self.pos += len(b)
        return len(b)

    def tell(self):
        return self.pos

    def drain(self):
        out = b"".join(self.chunks)
        self.chunks.clear()
        return out


def parquet_stream(sql, args, columns):
    schema = pa.schema([(name, typ) for _, name, typ in columns])
    sink = _Sink()
    writer = pq.ParquetWriter(sink, schema, compression="zstd")
    for batch in _rows(sql, args):
        writer.write_table(pa.Table.from_pylist(batch, schema=schema))
        yield sink.drain()
    writer.close()
    yield sink.drain()


def codebook_csv():
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["열 이름", "설명", "단위·값"])
    writer.writerows(CODEBOOK)
    return ("﻿" + buf.getvalue()).encode("utf-8")


def plan(args):
    """요청 인자 → (sql, args, columns, 파일 이름 앞부분)."""
    p = parse_args(args)
    region = f"_{p['region'][1]}" if p["region"] else ""
    stem = f"apt_{p['target']}{region}_{p['ym_from']}_{p['ym_to']}"
    if p["target"] == "raw":
        sql, qargs = raw_query(p)
        return sql, qargs, RAW_COLUMNS, stem
    with db.connection() as conn:
        version = queries.active_version(conn)
    sql, qargs = agg_query(version, p)
    return sql, qargs, AGG_COLUMNS, stem

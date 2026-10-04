"""화면·API 공용: 시군구 이름, 거래 조회 조건, 거래 행 변환, 템플릿 필터."""
from collector import api, codes

CODES = codes.load_codes()
SIDO = list(dict.fromkeys(CODES["시도"]))
NAMES = {r.LAWD_CD: (r.시도, r.시군구) for r in CODES.itertuples()}

# CSV·화면용 한글 열 이름 (API 필드명 기준. 기존 /download.csv 형식 유지)
LABELS = [
    ("dealDate", "계약일"), ("sido", "시도"), ("sigungu", "시군구"), ("lawd_cd", "시군구코드"),
    ("umdNm", "법정동"), ("jibun", "지번"), ("roadNm", "도로명"), ("roadNmBonbun", "건물본번"),
    ("roadNmBubun", "건물부번"), ("aptNm", "단지명"), ("aptDong", "동"), ("aptSeq", "단지일련번호"),
    ("excluUseAr", "전용면적"), ("floor", "층"), ("buildYear", "건축년도"), ("dealAmount", "거래금액(만원)"),
    ("dealingGbn", "거래유형"), ("estateAgentSggNm", "중개사소재지"), ("slerGbn", "매도자"),
    ("buyerGbn", "매수자"), ("cdealType", "해제여부"), ("cdealDay", "해제사유발생일"),
    ("rgstDate", "등기일자"), ("landLeaseholdGbn", "토지임대부"), ("sggCd", "법정동시군구코드"),
    ("umdCd", "법정동읍면동코드"), ("bonbun", "본번"), ("bubun", "부번"), ("deal_ymd", "계약년월"),
    ("collected_at", "수집시각"),
]

_HIDDEN = {"id", "price_per_m2", "is_cancelled"}


def filters(args, for_jobs=False):
    """요청 인자 → WHERE 절과 파라미터. for_jobs=True면 jobs 테이블에도 있는 조건(지역·기간)만."""
    where, params = [], []
    if args.get("sido"):
        where.append("lawd_cd = ANY(%s)")
        params.append(CODES.loc[CODES["시도"] == args["sido"], "LAWD_CD"].tolist())
    if args.get("lawd_cd"):
        where.append("lawd_cd = %s")
        params.append(args["lawd_cd"])
    if args.get("year"):
        where.append("deal_ymd BETWEEN %s AND %s")
        params += [f"{args['year']}01", f"{args['year']}12"]
    if args.get("ymd"):
        where.append("deal_ymd = %s")
        params.append(args["ymd"].replace("-", ""))
    if not for_jobs:
        if args.get("q"):
            where.append("(apt_nm ILIKE %s OR umd_nm ILIKE %s OR road_nm ILIKE %s)")
            params += [f"%{args['q']}%"] * 3
        if args.get("exclude_cancelled"):
            where.append("NOT is_cancelled")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def api_row(row):
    """DB 행(snake_case) → API 필드명(camelCase) + 시도·시군구 이름."""
    d = {api.CAMEL.get(k, k): v for k, v in row.items() if k not in _HIDDEN}
    d["sido"], d["sigungu"] = NAMES.get(row["lawd_cd"], ("", ""))
    return d


def register_filters(app):
    @app.template_filter("comma")
    def comma(v):
        return f"{v:,}" if isinstance(v, (int, float)) else (v or "")

    @app.template_filter("mb")
    def mb(v):
        return f"{(v or 0) / 1024 / 1024:,.1f}MB"

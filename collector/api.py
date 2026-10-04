"""공공데이터포털 '아파트 매매 실거래가 상세자료' API 호출과 응답 파싱.

DB는 모른다. 호출 수 기록·한도 확인은 호출하는 쪽이 on_call 콜백으로 맡긴다.
"""
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import date

import requests

import settings

API_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/129.0 Safari/537.36"
    ),
    "Accept": "application/xml",
}
NUM_ROWS = 1000

# API 응답 필드 (기술문서 순서)
FIELDS = [
    "sggCd", "umdCd", "landCd", "bonbun", "bubun", "roadNm", "roadNmSggCd", "roadNmCd",
    "roadNmSeq", "roadNmbCd", "roadNmBonbun", "roadNmBubun", "umdNm", "aptNm", "jibun",
    "excluUseAr", "dealYear", "dealMonth", "dealDay", "dealAmount", "floor", "buildYear",
    "aptSeq", "cdealType", "cdealDay", "dealingGbn", "estateAgentSggNm", "rgstDate",
    "aptDong", "slerGbn", "buyerGbn", "landLeaseholdGbn",
]


def snake(name):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


COLUMNS = [snake(f) for f in FIELDS]                       # trades 테이블의 원본 필드 컬럼
CAMEL = {snake(f): f for f in FIELDS} | {"deal_date": "dealDate"}


class QuotaExceeded(Exception):
    """오늘 호출 한도 소진. 다음 날 수집 시작 시각까지 쉰다."""


class ApiError(Exception):
    """재시도할 수 있는 API·네트워크 오류."""


def to_int(value):
    text = str(value if value is not None else "").replace(",", "").strip()
    try:
        return int(text)
    except ValueError:
        return None


def to_smallint(value):
    """정수로 바꾸되 Postgres SMALLINT 범위를 벗어나면 None."""
    n = to_int(value)
    return n if n is not None and -32768 <= n <= 32767 else None


def to_float(value):
    if value is None or str(value).strip() == "":
        return None
    try:
        f = float(value)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


def parse_response(content):
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        raise ApiError("XML 파싱 실패") from None

    # 게이트웨이 오류(키 미등록·한도 초과 등)는 형식이 다르다
    reason = root.findtext(".//returnReasonCode")
    if reason:
        if reason.strip() == "22":
            raise QuotaExceeded("공공데이터포털 일일 한도 초과(22)")
        raise ApiError(f"게이트웨이 오류 {reason.strip()}: {root.findtext('.//returnAuthMsg')}")
    code = (root.findtext(".//resultCode") or "").strip()
    if code == "22":
        raise QuotaExceeded("공공데이터포털 일일 한도 초과(22)")
    if code not in ("000", "00"):
        raise ApiError(f"API 오류 {code}: {root.findtext('.//resultMsg')}")

    items = [{c.tag: (c.text or "").strip() for c in item} for item in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or 0)


def fetch_page(session, key, lawd_cd, deal_ymd, page_no, num_rows=NUM_ROWS, on_call=None):
    """한 페이지를 받는다. on_call은 호출 직전에 불려 한도 확인·호출 수 기록을 맡는다."""
    if on_call:
        on_call()
    params = {"serviceKey": key, "LAWD_CD": lawd_cd, "DEAL_YMD": deal_ymd,
              "pageNo": page_no, "numOfRows": num_rows}
    time.sleep(settings.REQUEST_INTERVAL)
    try:
        resp = session.get(API_URL, params=params, headers=HEADERS, timeout=30)
    except requests.RequestException as e:
        # 예외 메시지에 serviceKey가 든 URL이 섞일 수 있어 종류만 남긴다
        raise ApiError(f"네트워크 오류: {type(e).__name__}") from None
    if resp.status_code != 200:
        raise ApiError(f"HTTP {resp.status_code}")
    return parse_response(resp.content)


def fetch_job(session, key, lawd_cd, deal_ymd, on_call=None):
    """작업 하나(시군구 x 계약월)의 전체 페이지 -> (items, total_count)."""
    items, total = fetch_page(session, key, lawd_cd, deal_ymd, 1, on_call=on_call)
    page = 1
    while len(items) < total:
        page += 1
        more, total = fetch_page(session, key, lawd_cd, deal_ymd, page, on_call=on_call)
        if not more:
            break
        items += more
    return items, total


def to_rows(items, lawd_cd, deal_ymd, collected_at):
    """API 항목 -> trades 행(snake_case 키, 숫자·날짜 변환)."""
    rows = []
    for it in items:
        r = {snake(f): it.get(f, "") for f in FIELDS}
        r["deal_amount"] = to_int(r["deal_amount"])
        r["exclu_use_ar"] = to_float(r["exclu_use_ar"])
        r["floor"] = to_smallint(r["floor"])
        r["build_year"] = to_smallint(r["build_year"])
        try:
            r["deal_date"] = date(int(r["deal_year"]), int(r["deal_month"]), int(r["deal_day"]))
        except (TypeError, ValueError, OverflowError):
            r["deal_date"] = None
        r.update(lawd_cd=lawd_cd, deal_ymd=deal_ymd, collected_at=collected_at)
        rows.append(r)
    return rows

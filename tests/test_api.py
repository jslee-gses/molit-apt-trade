from datetime import date

import pytest

import settings
from collector import api

OK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<response><header><resultCode>000</resultCode><resultMsg>OK</resultMsg></header>
<body><items>
<item><aptNm>테스트아파트</aptNm><aptSeq>11110-1</aptSeq><dealAmount>  84,000</dealAmount>
<dealYear>2026</dealYear><dealMonth>1</dealMonth><dealDay>5</dealDay><excluUseAr>84.9751</excluUseAr>
<floor>12</floor><buildYear>2005</buildYear><sggCd>11110</sggCd><umdCd>10100</umdCd><umdNm>청운동</umdNm>
<cdealType> </cdealType></item>
<item><aptNm>둘째</aptNm><dealAmount>abc</dealAmount><dealYear>2026</dealYear><dealMonth>13</dealMonth>
<dealDay>1</dealDay><floor>-1</floor><excluUseAr>x</excluUseAr></item>
</items><numOfRows>1000</numOfRows><pageNo>1</pageNo><totalCount>2</totalCount></body></response>"""

GATEWAY_QUOTA_XML = """<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>
<returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>
<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""

GATEWAY_KEY_XML = """<OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>
<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"""

RESULT_ERROR_XML = """<response><header><resultCode>03</resultCode><resultMsg>NO DATA</resultMsg></header></response>"""


def test_snake_and_camel_maps():
    assert api.snake("excluUseAr") == "exclu_use_ar"
    assert api.snake("roadNmbCd") == "road_nmb_cd"
    assert api.snake("sggCd") == "sgg_cd"
    assert len(api.COLUMNS) == len(api.FIELDS) == 32
    assert api.CAMEL["apt_seq"] == "aptSeq"
    assert api.CAMEL["deal_date"] == "dealDate"


def test_parse_response_ok():
    items, total = api.parse_response(OK_XML.encode())
    assert total == 2
    assert items[0]["aptNm"] == "테스트아파트"
    assert items[0]["cdealType"] == ""


def test_parse_response_gateway_quota():
    with pytest.raises(api.QuotaExceeded):
        api.parse_response(GATEWAY_QUOTA_XML.encode())


def test_parse_response_gateway_other_error():
    with pytest.raises(api.ApiError, match="게이트웨이 오류 30"):
        api.parse_response(GATEWAY_KEY_XML.encode())


def test_parse_response_result_error():
    with pytest.raises(api.ApiError, match="API 오류 03"):
        api.parse_response(RESULT_ERROR_XML.encode())


def test_parse_response_bad_xml():
    with pytest.raises(api.ApiError, match="XML 파싱 실패"):
        api.parse_response(b"<not xml")


def test_to_rows_types_and_defaults():
    items, _ = api.parse_response(OK_XML.encode())
    rows = api.to_rows(items, "11110", "202601", "COLLECTED")
    r = rows[0]
    assert r["deal_amount"] == 84000
    assert r["exclu_use_ar"] == pytest.approx(84.9751)
    assert r["floor"] == 12
    assert r["build_year"] == 2005
    assert r["deal_date"] == date(2026, 1, 5)
    assert r["road_nm"] == ""
    assert (r["lawd_cd"], r["deal_ymd"], r["collected_at"]) == ("11110", "202601", "COLLECTED")


def test_to_rows_bad_values_become_none():
    items, _ = api.parse_response(OK_XML.encode())
    r = api.to_rows(items, "11110", "202601", None)[1]
    assert r["deal_amount"] is None
    assert r["exclu_use_ar"] is None
    assert r["floor"] == -1
    assert r["build_year"] is None


def test_to_rows_invalid_date_is_none():
    items, _ = api.parse_response(OK_XML.encode())
    assert api.to_rows(items, "11110", "202601", None)[1]["deal_date"] is None


class FakeResponse:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


class FakeSession:
    """pageNo에 따라 정해진 XML을 돌려주는 가짜 세션."""

    def __init__(self, pages, status_code=200):
        self.pages = pages
        self.status_code = status_code
        self.calls = []

    def get(self, url, params, headers, timeout):
        self.calls.append(params)
        return FakeResponse(self.pages[params["pageNo"]], self.status_code)


def page_xml(n_items, total):
    items = "".join(f"<item><aptNm>A{i}</aptNm></item>" for i in range(n_items))
    return (f"<response><header><resultCode>000</resultCode></header><body><items>{items}</items>"
            f"<totalCount>{total}</totalCount></body></response>").encode()


def test_fetch_job_pages_until_total(monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    session = FakeSession({1: page_xml(2, 3), 2: page_xml(1, 3)})
    calls = []
    items, total = api.fetch_job(session, "KEY", "11110", "202601", on_call=lambda: calls.append(1))
    assert total == 3 and len(items) == 3
    assert len(calls) == 2
    assert session.calls[0]["serviceKey"] == "KEY" and session.calls[1]["pageNo"] == 2


def test_fetch_page_http_error(monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    with pytest.raises(api.ApiError, match="HTTP 500"):
        api.fetch_page(FakeSession({1: b""}, status_code=500), "KEY", "11110", "202601", 1)


def test_fetch_page_network_error_hides_url(monkeypatch):
    import requests
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)

    class Boom:
        def get(self, *a, **k):
            raise requests.ConnectionError("https://apis.data.go.kr/?serviceKey=SECRET")

    with pytest.raises(api.ApiError) as e:
        api.fetch_page(Boom(), "KEY", "11110", "202601", 1)
    assert "SECRET" not in str(e.value)


def test_fetch_page_on_call_can_stop(monkeypatch):
    monkeypatch.setattr(settings, "REQUEST_INTERVAL", 0)
    session = FakeSession({1: page_xml(1, 1)})

    def stop():
        raise api.QuotaExceeded("한도")

    with pytest.raises(api.QuotaExceeded):
        api.fetch_page(session, "KEY", "11110", "202601", 1, on_call=stop)
    assert session.calls == []

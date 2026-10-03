"""조회 조건 검증. 잘못된 값은 BadParam(한국어 메시지) → API가 400으로 돌려준다."""
import re

from collector import codes


class BadParam(ValueError):
    pass


LEVELS = ("nation", "sido", "sgg", "umd")
LEVEL_LABELS = {"nation": "전국", "sido": "시도", "sgg": "시군구", "umd": "읍면동"}
CODE_LEN = {"nation": 2, "sido": 2, "sgg": 5, "umd": 8}
BANDS = [("all", "전체 면적"), ("le60", "60㎡ 이하"), ("60_85", "60~85㎡"), ("gt85", "85㎡ 초과")]
_YM = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])$")


def ym(value, name):
    v = (value or "").replace("-", "").strip()
    if not _YM.match(v):
        raise BadParam(f"{name}은(는) YYYYMM 형식이어야 합니다: {value!r}")
    return v


def choice(value, options, name, default=None):
    if value in (None, ""):
        if default is None:
            raise BadParam(f"{name}을(를) 지정하세요.")
        return default
    if value not in options:
        raise BadParam(f"{name}은(는) {', '.join(options)} 중 하나여야 합니다: {value!r}")
    return value


def region_item(value):
    level, sep, code = (value or "").strip().partition(":")
    if not sep or level not in LEVELS:
        raise BadParam(f"지역은 '수준:코드' 형식이어야 합니다(예: sgg:11110): {value!r}")
    if not (code.isdigit() and len(code) == CODE_LEN[level]) or (level == "nation" and code != "00"):
        raise BadParam(f"{LEVEL_LABELS[level]} 코드는 {CODE_LEN[level]}자리 숫자여야 합니다: {value!r}")
    return level, code


def region_items(value, max_n=8):
    items = [region_item(v) for v in (value or "").split(",") if v.strip()]
    if len(items) > max_n:
        raise BadParam(f"지역은 최대 {max_n}개까지 비교할 수 있습니다.")
    return list(dict.fromkeys(items))


def ym_range(args, default_months=60, max_months=None):
    """from/to(YYYYMM). 없으면 to=이번 달, from=to에서 default_months-1개월 전."""
    to = ym(args.get("to"), "종료월") if args.get("to") else codes.months_ago(0)
    if args.get("from"):
        start = ym(args.get("from"), "시작월")
    else:
        from analytics.queries import shift_ym
        start = shift_ym(to, -(default_months - 1))
    if start > to:
        raise BadParam("시작월이 종료월보다 늦습니다.")
    if max_months and len(codes.month_range(start, to)) > max_months:
        raise BadParam(f"기간은 최대 {max_months}개월까지 지정할 수 있습니다.")
    return start, to

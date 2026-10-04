"""도로명주소 키. migrations/002_geo.sql의 road_key()와 같은 규칙이어야 한다."""
import re

_D5 = re.compile(r"[0-9]{5}")
_D7 = re.compile(r"[0-9]{7}")
_DIGITS = re.compile(r"[0-9]{1,9}")


def road_key(sgg, road, under, bon, bu):
    """시군구 5 + 도로 7 | 지하여부 0/1 | 건물본번 | 건물부번. 형식이 안 맞으면 None."""
    sgg, road, under, bon, bu = (str(v if v is not None else "").strip(" ") for v in (sgg, road, under, bon, bu))
    if not (_D5.fullmatch(sgg) and _D7.fullmatch(road) and _DIGITS.fullmatch(bon)):
        return None
    sub = int(bu) if _DIGITS.fullmatch(bu) else 0
    return f"{sgg}{road}|{'1' if under == '1' else '0'}|{int(bon)}|{sub}"

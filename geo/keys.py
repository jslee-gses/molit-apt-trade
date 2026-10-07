"""도로명주소 키·필지고유번호(PNU). migrations의 road_key()·pnu()와 같은 규칙이어야 한다."""
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


def _num4(v):
    """0채움 포함 1~9자리 숫자이고 값이 0~9999면 int, 아니면 None."""
    return int(v) if _DIGITS.fullmatch(v) and int(v) <= 9999 else None


def pnu(sgg, umd, land, bon, bu):
    """필지고유번호 19자리 = 시군구5 + 법정동5 + 대지구분1(산 2, 그 밖 1) + 본번4 + 부번4. 형식이 안 맞으면 None."""
    sgg, umd, land, bon, bu = (str(v if v is not None else "").strip(" ") for v in (sgg, umd, land, bon, bu))
    if not (_D5.fullmatch(sgg) and _D5.fullmatch(umd)):
        return None
    main = _num4(bon)
    sub = 0 if bu == "" else _num4(bu)
    if not main or sub is None:
        return None
    return f"{sgg}{umd}{'2' if land == '2' else '1'}{main:04d}{sub:04d}"

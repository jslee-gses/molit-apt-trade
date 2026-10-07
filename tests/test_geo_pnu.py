import pytest

from geo.keys import pnu

CASES = [
    (("11110", "10100", "1", "0001", "0000"), "1111010100100010000"),
    (("11110", "10100", "1", "12", ""), "1111010100100120000"),
    (("11110", "10100", "2", "0071", "0003"), "1111010100200710003"),     # 산
    ((" 11110 ", "10100", None, "71", "3"), "1111010100100710003"),        # 공백·대지구분 없음 → 일반
    (("11110", "10100", "1", "00071", "00003"), "1111010100100710003"),    # 5자리 0채움 입력
    (("11110", "10100", "1", "0", "0"), None),                            # 본번 0
    (("11110", "10100", "1", "", "0"), None),                             # 본번 없음
    (("11110", "10100", "1", "abc", "0"), None),
    (("11110", "10100", "1", "10000", "0"), None),                        # 본번 4자리 초과
    (("11110", "10100", "1", "1", "x"), None),                            # 부번 숫자 아님
    (("11110", "10100", "1", "1", "10000"), None),                        # 부번 4자리 초과
    (("1111", "10100", "1", "1", "0"), None),                             # 시군구 자릿수
    (("11110", "101", "1", "1", "0"), None),                              # 법정동 자릿수
    ((None, None, None, None, None), None),
]


@pytest.mark.parametrize("args, expected", CASES)
def test_pnu(args, expected):
    assert pnu(*args) == expected


def test_sql_pnu_matches_python(pg):
    with pg.connection() as conn:
        for args, expected in CASES:
            got = conn.execute("SELECT pnu(%s, %s, %s, %s, %s) AS p", args).fetchone()["p"]
            assert got == expected, args

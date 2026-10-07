"""읍면동 코드 개편 대응표(geo/code_map.csv: old_emd_cd,new_emd_cd[,old_name,new_name]).

서버(지리 처리)도 쓰므로 표준 라이브러리만 쓴다. geo/boundaries.py(로컬 경계 도구)도 같은 파일을 읽는다.
"""
import csv
import hashlib

import settings

PATH = settings.BASE_DIR / "geo" / "code_map.csv"


def read(path=None):
    """옛 읍면동 코드 8자리 → 새 코드 8자리. 파일이 없으면 빈 dict."""
    p = path or PATH
    if not p.exists():
        return {}
    with open(p, encoding="utf-8-sig", newline="") as f:
        out = {}
        for r in csv.DictReader(f):
            old, new = (r.get("old_emd_cd") or "").strip(), (r.get("new_emd_cd") or "").strip()
            if old and new:
                out[old.zfill(8)] = new.zfill(8)
        return out


def digest(path=None):
    """파일 내용이 바뀌었는지 보는 짧은 해시. 파일이 없으면 'none'."""
    p = path or PATH
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12] if p.exists() else "none"

from datetime import datetime

import pandas as pd
import pytest

import settings
from analytics import aggregates
from collector import store
from geo import hooks
from tests.helpers import add_job, item

V = "2026-10"


@pytest.fixture(autouse=True)
def setup(monkeypatch):
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 7, 0, tzinfo=settings.KST))
    monkeypatch.setattr(store, "AFTER_SAVE", [])
    monkeypatch.setattr(hooks, "ON_REGION_CHANGE", [])


# (lawd_cd, aptSeq, 금액, 면적, 덮어쓸 항목)
TRADES = [
    ("11110", "A", "50,000", "59.5", {}),
    ("11110", "A", "70,000", "84.9", {"dealDay": "6"}),
    ("11110", "A", "70,000", "84.9", {"dealDay": "6"}),          # 완전 중복 → 1건
    ("11110", "B", "90,000", "114.8", {"dealDay": "7"}),
    ("11110", "B", "80,000", "84.9", {"cdealType": "O"}),       # 해제 → 제외
    ("11110", "D", "60,000", "59.9", {"dealDay": "8"}),         # 좌표상 중구
    ("11110", "E", "", "59.9", {}),                              # 금액 없음 → 제외
    ("11140", "C", "40,000", "49.0", {"sggCd": "11140"}),       # 좌표 미판정
    ("11140", "C", "45,000", "49.0", {"sggCd": "11140", "dealDay": "9"}),
]
# 단지 → (시군구, 읍면동)
REGIONS = {"A": ("11110", "11110101"), "B": ("11110", "11110102"), "D": ("11140", "11140101"),
           "C": (None, None), "E": (None, None)}


def seed(conn, ym="202601"):
    for lawd in ("11110", "11140"):
        add_job(conn, lawd, ym)
        items = [item(aptSeq=s, dealAmount=a, excluUseAr=ar, dealMonth=str(int(ym[4:])), **kw)
                 for l, s, a, ar, kw in TRADES if l == lawd]
        store.save_job(conn, lawd, ym, items, len(items))
    for seq, (sgg, umd) in REGIONS.items():
        conn.execute("""INSERT INTO complexes (apt_seq, api_sgg_cd, geocode_status, region_sgg_cd, region_umd_cd,
                            region_match, boundary_version) VALUES (%s, '11110', 'ok', %s, %s, %s, %s)
                        ON CONFLICT (apt_seq) DO NOTHING""",
                     (seq, sgg, umd, "within" if umd else "none", V))
    conn.execute("INSERT INTO boundary_versions (version, loaded_at, is_active) VALUES (%s, now(), true) "
                 "ON CONFLICT DO NOTHING", (V,))


def band(area):
    return "le60" if area <= 60 else "60_85" if area <= 85 else "gt85"


def expected():
    """같은 규칙을 pandas로 계산한 기대값 {(level, region_cd, size_band): {...}}"""
    seen, rows = set(), []
    for lawd, seq, amount, area, kw in TRADES:
        key = (lawd, seq, amount, area, tuple(sorted(kw.items())))
        if kw.get("cdealType") or not amount or key in seen:
            continue
        seen.add(key)
        sgg, umd = REGIONS[seq]
        price, a = float(amount.replace(",", "")), float(area)
        rows.append(dict(sgg=sgg or lawd, umd=umd, price=price, ppm2=price / a, band=band(a)))
    df = pd.DataFrame(rows)
    df = pd.concat([df.assign(band="all"), df])
    df["sido"], df["nation"] = df["sgg"].str[:2], "00"
    out = {}
    for level, col in [("nation", "nation"), ("sido", "sido"), ("sgg", "sgg"), ("umd", "umd")]:
        for (code, b), g in df.dropna(subset=[col]).groupby([col, "band"]):
            out[(level, code, b)] = dict(
                n_trades=len(g), median_price=g.price.median(), p25_price=g.price.quantile(0.25),
                p75_price=g.price.quantile(0.75), mean_price=g.price.mean(), median_ppm2=g.ppm2.median())
    return out


def fetch(conn, version=V, ym="202601"):
    rows = conn.execute("SELECT * FROM agg_month WHERE boundary_version = %s AND ym = %s", (version, ym)).fetchall()
    return {(r["level"], r["region_cd"], r["size_band"]): r for r in rows}


def test_refresh_month_matches_pandas(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        got = fetch(conn)
    exp = expected()
    assert set(got) == set(exp)
    for key, e in exp.items():
        for k, v in e.items():
            assert got[key][k] == pytest.approx(v), (key, k)
    assert got[("sgg", "11140", "all")]["n_trades"] == 3          # C 2건 + 좌표상 중구인 D 1건


def test_unassigned_counts_in_sgg_not_umd(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        got = fetch(conn)
    umd_sum = sum(r["n_trades"] for (lv, code, b), r in got.items() if lv == "umd" and b == "all"
                  and code.startswith("11140"))
    assert umd_sum == 1 and got[("sgg", "11140", "all")]["n_trades"] == 3
    assert not any(code is None for (_, code, _) in got)


def test_refresh_month_replaces(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        conn.execute("DELETE FROM trades WHERE apt_seq = 'B'")
        aggregates.refresh_month(conn, "202601", V)
        got = fetch(conn)
    assert ("umd", "11110102", "all") not in got


def test_hooks_mark_and_refresh_dirty(pg, monkeypatch):
    monkeypatch.setattr(store, "AFTER_SAVE", [aggregates.on_saved])
    with pg.connection() as conn:
        seed(conn)
        assert [r["ym"] for r in conn.execute("SELECT ym FROM agg_dirty")] == ["202601"]
        assert aggregates.refresh_dirty(conn) == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"] == 0
        assert fetch(conn)[("nation", "00", "all")]["n_trades"] == 6
        aggregates.on_region_change(conn, ["C"])
        assert [r["ym"] for r in conn.execute("SELECT ym FROM agg_dirty")] == ["202601"]


def test_refresh_dirty_isolates_failures_newest_first(pg, monkeypatch):
    calls = []

    def fake(conn, ym, version, mapping="live"):
        calls.append(ym)
        if ym == "202601":
            raise RuntimeError("boom")

    monkeypatch.setattr(aggregates, "refresh_month", fake)
    with pg.connection() as conn:
        seed(conn)
        aggregates.mark_months(conn, ["202601", "202512"])
        assert aggregates.refresh_dirty(conn) == 1
        assert calls == ["202601", "202512"]                 # 최신 달 먼저
        assert [r["ym"] for r in conn.execute("SELECT ym FROM agg_dirty")] == ["202601"]


def test_refresh_dirty_waits_for_active_version(pg):
    with pg.connection() as conn:
        seed(conn)
        conn.execute("UPDATE boundary_versions SET is_active = false")
        aggregates.mark_months(conn, ["202601"])
        assert aggregates.refresh_dirty(conn) == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"] == 1


def test_incremental_equals_full_rebuild(pg):
    with pg.connection() as conn:
        seed(conn, "202601")
        seed(conn, "202602")
        aggregates.mark_months(conn, ["202601", "202602"])
        aggregates.refresh_dirty(conn)
        inc = {**fetch(conn, ym="202601"), **{("x",) + k: v for k, v in fetch(conn, ym="202602").items()}}
        conn.execute("DELETE FROM agg_month")
        assert aggregates.rebuild_all(conn, V, "live") == 2
        full = {**fetch(conn, ym="202601"), **{("x",) + k: v for k, v in fetch(conn, ym="202602").items()}}
    assert inc == full


def test_before_activate_uses_staged_mapping(pg):
    with pg.connection() as conn:
        seed(conn)
        with conn.transaction():
            conn.execute("CREATE TEMP TABLE staged_regions (apt_seq TEXT PRIMARY KEY, umd TEXT, sgg TEXT, "
                         "match TEXT, version TEXT, mismatch BOOLEAN) ON COMMIT DROP")
            conn.execute("INSERT INTO staged_regions (apt_seq, umd, sgg) VALUES ('A', '11110199', '11110')")
            aggregates.before_activate(conn, "2027-01")
            aggregates.after_activate(conn, "2027-01")
        new = fetch(conn, "2027-01")
        old = fetch(conn, V)
    assert ("umd", "11110199", "all") in new and ("umd", "11110101", "all") not in new
    assert old == {}


def test_before_activate_keeps_dirty_queue(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.mark_months(conn, ["202601"])
        with conn.transaction():
            conn.execute("CREATE TEMP TABLE staged_regions (apt_seq TEXT PRIMARY KEY, umd TEXT, sgg TEXT, "
                         "match TEXT, version TEXT, mismatch BOOLEAN) ON COMMIT DROP")
            aggregates.before_activate(conn, "2027-01")
            aggregates.after_activate(conn, "2027-01")
        assert conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"] == 1


def test_status(pg):
    with pg.connection() as conn:
        seed(conn)
        aggregates.refresh_month(conn, "202601", V)
        aggregates.mark_months(conn, ["202602"])
        s = aggregates.status(conn)
    assert s["dirty"] == 1 and s["rows"] > 0 and s["latest_ym"] == "202601"


def test_wire_connects_aggregates(monkeypatch):
    import wiring
    from geo import pipeline
    monkeypatch.setattr(pipeline, "AFTER_RUN", [])
    monkeypatch.setattr(hooks, "BEFORE_ACTIVATE", [])
    monkeypatch.setattr(hooks, "AFTER_ACTIVATE", [])
    wiring.wire()
    assert aggregates.on_saved in store.AFTER_SAVE
    assert hooks.ON_REGION_CHANGE == [aggregates.on_region_change]
    assert hooks.BEFORE_ACTIVATE == [aggregates.before_activate]
    assert hooks.AFTER_ACTIVATE == [aggregates.after_activate]
    assert pipeline.AFTER_RUN == [aggregates.refresh_dirty]


def test_refresh_dirty_bootstraps_empty_version(pg):
    with pg.connection() as conn:
        seed(conn)
        assert conn.execute("SELECT COUNT(*) AS n FROM agg_dirty").fetchone()["n"] == 0
        assert aggregates.refresh_dirty(conn) == 1
        assert fetch(conn)[("nation", "00", "all")]["n_trades"] == 6

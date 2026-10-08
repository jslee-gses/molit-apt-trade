"""집계·단지 조회(화면·API용). 모두 활성 경계 버전 기준."""
from collector import codes

PROVISIONAL_MONTHS = 2   # 이번 달·지난달은 신고기한(30일) 때문에 잠정
METRICS = ("n_trades", "median_price", "p25_price", "p75_price", "mean_price", "median_ppm2")
MOVER_MIN_TRADES = 10


class NotReady(Exception):
    pass


def shift_ym(ym, months):
    y, m = int(ym[:4]), int(ym[4:]) + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return f"{y}{m:02d}"


def provisional_from():
    return codes.months_ago(PROVISIONAL_MONTHS - 1)


def confirmed_ym():
    return codes.months_ago(PROVISIONAL_MONTHS)


def _pct(cur, prev):
    return round(100 * (cur - prev) / prev, 1) if cur is not None and prev else None


def active_version(conn):
    row = conn.execute("SELECT version FROM boundary_versions WHERE is_active").fetchone()
    if not row:
        raise NotReady("경계 데이터가 아직 준비되지 않았습니다. 수집 현황에서 활성 경계를 확인하세요.")
    return row["version"]


def regions(conn, version, level, parent=None):
    if level == "nation":
        return [dict(region_cd="00", level="nation", name="전국", full_name="전국", parent_cd=None)]
    sql = ("SELECT region_cd, level, name, full_name, parent_cd FROM regions "
           "WHERE boundary_version = %s AND level = %s")
    args = [version, level]
    if parent:
        sql += " AND parent_cd = %s"
        args.append(parent)
    return conn.execute(sql + " ORDER BY region_cd", args).fetchall()


def _names(conn, version, items):
    out = {("nation", "00"): "전국"}
    for r in conn.execute("SELECT level, region_cd, full_name FROM regions "
                          "WHERE boundary_version = %s AND region_cd = ANY(%s)",
                          (version, [c for _, c in items])):
        out[(r["level"], r["region_cd"])] = r["full_name"]
    return out


def series(conn, version, items, band, ym_from, ym_to):
    """지역별 월 집계. 거래가 없는 달은 행이 없다(화면에서 빈 값으로 그린다)."""
    rows = conn.execute("""
        SELECT level, region_cd, ym, n_trades, median_price, p25_price, p75_price, mean_price, median_ppm2
          FROM agg_month
         WHERE boundary_version = %s AND size_band = %s AND ym BETWEEN %s AND %s
           AND (level, region_cd) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
         ORDER BY level, region_cd, ym""",
                        (version, band, ym_from, ym_to, [l for l, _ in items], [c for _, c in items])).fetchall()
    names = _names(conn, version, items)
    points = {key: [] for key in items}
    for r in rows:
        points[(r["level"], r["region_cd"])].append({k: r[k] for k in ("ym", *METRICS)})
    return [dict(key=f"{l}:{c}", level=l, code=c, name=names.get((l, c), c), points=points[(l, c)])
            for l, c in items]


def _window(conn, version, level, band, ym_from, ym_to, parent=None, only=None):
    """기간 합계: 거래량 합, 월별 중위가의 거래량 가중평균."""
    sql = """
        SELECT a.region_cd, SUM(a.n_trades)::int AS n,
               SUM(a.median_price * a.n_trades) / NULLIF(SUM(a.n_trades), 0) AS price,
               SUM(a.median_ppm2 * a.n_trades)
                 / NULLIF(SUM(a.n_trades) FILTER (WHERE a.median_ppm2 IS NOT NULL), 0) AS ppm2
          FROM agg_month a
          {join}
         WHERE a.boundary_version = %s AND a.level = %s AND a.size_band = %s AND a.ym BETWEEN %s AND %s
           {only}
         GROUP BY a.region_cd"""
    args = [version, level, band, ym_from, ym_to]
    join = only_sql = ""
    if parent:
        join = ("JOIN regions r ON r.boundary_version = a.boundary_version AND r.region_cd = a.region_cd "
                "AND r.parent_cd = %s")
        args.insert(0, parent)
    if only:
        only_sql = "AND a.region_cd = ANY(%s)"
        args.append(list(only))
    rows = conn.execute(sql.format(join=join, only=only_sql), args).fetchall()
    return {r["region_cd"]: r for r in rows}


def _parents(conn, version, level, parent):
    if level == "sgg":
        chain = [parent]
    elif level == "umd":
        chain = [parent[:2], parent]
    else:
        return []
    rows = {r["region_cd"]: r for r in conn.execute(
        "SELECT region_cd, level, name FROM regions WHERE boundary_version = %s AND region_cd = ANY(%s)",
        (version, chain))}
    return [dict(region_cd=c, level=rows[c]["level"], name=rows[c]["name"]) for c in chain if c in rows]


def map_values(conn, version, level, parent, band, ym_from, ym_to):
    prev_from, prev_to = shift_ym(ym_from, -12), shift_ym(ym_to, -12)
    scope = parent if level in ("sgg", "umd") else None
    cur = _window(conn, version, level, band, ym_from, ym_to, parent=scope)
    prev = _window(conn, version, level, band, prev_from, prev_to, parent=scope)
    values = []
    for r in regions(conn, version, level, scope):
        c, p = cur.get(r["region_cd"]), prev.get(r["region_cd"])
        values.append(dict(
            region_cd=r["region_cd"], name=r["name"], full_name=r["full_name"],
            n=c["n"] if c else 0, median_price=c["price"] if c else None, median_ppm2=c["ppm2"] if c else None,
            yoy_price=_pct(c["price"] if c else None, p["price"] if p else None),
            yoy_n=_pct(c["n"] if c else None, p["n"] if p else None)))
    coverage = None
    if level == "umd":
        sgg = _window(conn, version, "sgg", band, ym_from, ym_to, only=[parent]).get(parent)
        total = sgg["n"] if sgg else 0
        coverage = round(100 * sum(v["n"] for v in values) / total, 1) if total else None
    return dict(values=values, coverage=coverage, prev_from=prev_from, prev_to=prev_to,
                parents=_parents(conn, version, level, parent))


def summary(conn, version):
    conf, prov, now = confirmed_ym(), provisional_from(), codes.months_ago(0)
    first = shift_ym(conf, -21)
    nat = {r["ym"]: r for r in conn.execute("""
        SELECT ym, n_trades, median_price FROM agg_month
         WHERE boundary_version = %s AND level = 'nation' AND size_band = 'all' AND ym BETWEEN %s AND %s""",
                                            (version, shift_ym(first, -12), now))}
    cur, prev = nat.get(conf), nat.get(shift_ym(conf, -12))
    kpi = dict(ym=conf, n=cur["n_trades"] if cur else None, median=cur["median_price"] if cur else None,
               yoy_n=_pct(cur["n_trades"] if cur else None, prev["n_trades"] if prev else None),
               yoy_median=_pct(cur["median_price"] if cur else None, prev["median_price"] if prev else None))
    series_ = [dict(ym=ym, n=nat[ym]["n_trades"] if ym in nat else None,
                    median=nat[ym]["median_price"] if ym in nat else None)
               for ym in codes.month_range(first, now)]
    w_from, w_to = shift_ym(conf, -2), conf
    a = _window(conn, version, "sgg", "all", w_from, w_to)
    b = _window(conn, version, "sgg", "all", shift_ym(w_from, -12), shift_ym(w_to, -12))
    names = {r["region_cd"]: r["full_name"] for r in regions(conn, version, "sgg")}
    movers = []
    for code, r in a.items():
        p = b.get(code)
        if p and r["n"] >= MOVER_MIN_TRADES and p["n"] >= MOVER_MIN_TRADES and p["price"]:
            movers.append(dict(region_cd=code, name=names.get(code, code), n=r["n"], median=r["price"],
                               yoy=_pct(r["price"], p["price"])))
    movers.sort(key=lambda m: m["yoy"], reverse=True)
    up = [m for m in movers if m["yoy"] >= 0][:10]
    down = [m for m in reversed(movers) if m["yoy"] < 0][:10]
    return dict(version=version, confirmed_ym=conf, provisional_from=prov, kpi=kpi, series=series_,
                movers=dict(up=up, down=down, window=[w_from, w_to], min_trades=MOVER_MIN_TRADES))


def _region_filter(region):
    if not region:
        return "", []
    if not region.isdigit() or len(region) not in (2, 5, 8):
        from analytics.params import BadParam
        raise BadParam("지역 코드는 시도 2자리·시군구 5자리·읍면동 8자리 숫자여야 합니다.")
    if len(region) == 8:
        return " AND c.region_umd_cd = %s", [region]
    if len(region) == 5:
        return " AND COALESCE(c.region_sgg_cd, c.api_sgg_cd) = %s", [region]
    return " AND left(COALESCE(c.region_sgg_cd, c.api_sgg_cd), 2) = %s", [region]


_COMPLEX_SELECT = """
    SELECT c.apt_seq, c.apt_nm, c.build_year, c.last_deal_date, c.api_umd_nm, c.jibun, c.road_nm,
           c.lon, c.lat, c.geocode_status, c.geocode_source, c.region_match, c.region_umd_cd, c.sgg_mismatch,
           COALESCE(ru.full_name, rs.full_name) AS region_name
      FROM complexes c
      LEFT JOIN regions ru ON ru.boundary_version = %s AND ru.region_cd = c.region_umd_cd
      LEFT JOIN regions rs ON rs.boundary_version = %s AND rs.region_cd = COALESCE(c.region_sgg_cd, c.api_sgg_cd)
"""


def search_complexes(conn, version, q=None, region=None, limit=50):
    """limit=None이면 전부."""
    where, args = _region_filter(region)
    if q:
        where += " AND c.apt_nm ILIKE %s"
        args.append(f"%{q}%")
    return conn.execute(_COMPLEX_SELECT + " WHERE TRUE" + where +
                        " ORDER BY c.last_deal_date DESC NULLS LAST, c.apt_nm LIMIT %s",
                        [version, version, *args, limit]).fetchall()


def complex_detail(conn, version, apt_seq):
    row = conn.execute(_COMPLEX_SELECT + " WHERE c.apt_seq = %s", (version, version, apt_seq)).fetchone()
    if not row:
        raise LookupError(apt_seq)
    trades = conn.execute("""
        SELECT deal_date, deal_amount, exclu_use_ar::float8 AS area, floor, apt_dong, dealing_gbn,
               is_cancelled, price_per_m2::float8 AS ppm2
          FROM trades WHERE apt_seq = %s
         ORDER BY deal_date DESC NULLS LAST, id DESC LIMIT 2000""", (apt_seq,)).fetchall()
    return dict(complex=row, trades=trades)


NATION_LIMIT = 3000   # 조건 없는 전국 지도는 최근 거래 순 이만큼만(전체는 4만여 개)


def complex_locations(conn, q=None, region=None):
    """단지 화면 지도: 검색 목록과 같은 조건(단지명·지역)의 좌표 있는 단지 전부. 조건이 없으면(전국)
    최근 거래 순 NATION_LIMIT개. → [[apt_seq, apt_nm, lon, lat], ...] (응답을 작게 하려고 배열, 좌표 소수 6자리)"""
    where, args = _region_filter(region)
    if q:
        where += " AND c.apt_nm ILIKE %s"
        args.append(f"%{q}%")
    rows = conn.execute("""
        SELECT c.apt_seq, c.apt_nm, round(c.lon::numeric, 6)::float8 AS lon, round(c.lat::numeric, 6)::float8 AS lat
          FROM complexes c
         WHERE c.geocode_status IN ('ok', 'manual') AND c.lon IS NOT NULL AND c.lat IS NOT NULL""" + where +
                        " ORDER BY c.last_deal_date DESC NULLS LAST, c.apt_seq LIMIT %s",
                        [*args, None if (q or region) else NATION_LIMIT]).fetchall()
    return [[r["apt_seq"], r["apt_nm"], r["lon"], r["lat"]] for r in rows]


def nearby(conn, version, apt_seq, limit=500):
    """같은 읍면동의 좌표 있는 단지(자기 자신 먼저). 지역 미판정이면 빈 목록."""
    me = conn.execute("SELECT region_umd_cd FROM complexes WHERE apt_seq = %s", (apt_seq,)).fetchone()
    if not me:
        raise LookupError(apt_seq)
    umd = me["region_umd_cd"]
    if not umd:
        return dict(version=version, umd_cd=None, umd_name=None, complexes=[])
    name = conn.execute("SELECT full_name FROM regions WHERE boundary_version = %s AND region_cd = %s",
                        (version, umd)).fetchone()
    rows = conn.execute("""
        SELECT apt_seq, apt_nm, lon, lat, apt_seq = %s AS is_self FROM complexes
         WHERE region_umd_cd = %s AND geocode_status IN ('ok', 'manual') AND lon IS NOT NULL AND lat IS NOT NULL
         ORDER BY (apt_seq = %s) DESC, last_deal_date DESC NULLS LAST, apt_seq LIMIT %s""",
                        (apt_seq, umd, apt_seq, limit)).fetchall()
    return dict(version=version, umd_cd=umd, umd_name=name["full_name"] if name else None, complexes=rows)

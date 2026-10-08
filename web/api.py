"""JSON API. 거래 필드는 API 필드명(기존 형식), 분석 API는 snake_case."""
from flask import Blueprint, jsonify, request

import db
from analytics import params, queries
from analytics.params import BadParam
from collector import jobs, quality
from geo import complexes
from web.common import api_row, filters

bp = Blueprint("api", __name__, url_prefix="/api")


@bp.route("/status")
def status():
    return jsonify(jobs.progress())


@bp.route("/quality")
def quality_():
    return jsonify(quality.quality_report())


@bp.route("/trades")
def trades():
    where, params = filters(request.args)
    try:
        limit = int(request.args.get("limit", 100) or 100)
    except ValueError:
        return jsonify(error="limit은 숫자여야 합니다."), 400
    if limit < 1:
        return jsonify(error="limit은 1 이상이어야 합니다."), 400
    limit = min(limit, 1000)
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY deal_date DESC NULLS LAST, id DESC LIMIT %s",
            [*params, limit]).fetchall()
    return jsonify([api_row(r) for r in rows])


@bp.route("/complexes/<apt_seq>/coords", methods=["POST"])
def manual_coords(apt_seq):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="JSON 형식의 {lon, lat}이 필요합니다."), 400
    try:
        with db.connection() as conn:
            complexes.set_manual(conn, apt_seq, data.get("lon"), data.get("lat"))
    except ValueError as e:
        return jsonify(error=str(e)), 400
    except LookupError:
        return jsonify(error="없는 단지입니다."), 404
    return jsonify(ok=True)


CACHED = {"api.regions_", "api.agg", "api.map_", "api.summary", "api.complexes_locations"}


@bp.errorhandler(BadParam)
def bad_param(e):
    return jsonify(error=str(e)), 400


@bp.errorhandler(queries.NotReady)
def not_ready(e):
    return jsonify(error=str(e)), 503


@bp.after_request
def cache_headers(resp):
    if request.endpoint in CACHED and resp.status_code == 200:
        resp.headers["Cache-Control"] = "private, max-age=600"
    return resp


@bp.route("/regions")
def regions_():
    level = params.choice(request.args.get("level"), params.LEVELS, "수준")
    with db.connection() as conn:
        version = queries.active_version(conn)
        rows = queries.regions(conn, version, level, request.args.get("parent") or None)
    return jsonify(version=version, regions=rows)


@bp.route("/agg")
def agg():
    items = params.region_items(request.args.get("regions"))
    if not items:
        raise BadParam("regions에 지역을 1개 이상 지정하세요(예: sgg:11110).")
    band = params.choice(request.args.get("band"), [b for b, _ in params.BANDS], "면적 구간", "all")
    ym_from, ym_to = params.ym_range(request.args)
    with db.connection() as conn:
        version = queries.active_version(conn)
        data = queries.series(conn, version, items, band, ym_from, ym_to)
    return jsonify(version=version, band=band, provisional_from=queries.provisional_from(),
                   series=data, **{"from": ym_from, "to": ym_to})


def _map_period():
    """지도 기간: from/to가 있으면 검증, 없으면 확정 최근 3개월."""
    if request.args.get("from") or request.args.get("to"):
        return params.ym_range(request.args)
    ym_to = queries.confirmed_ym()
    return queries.shift_ym(ym_to, -2), ym_to


@bp.route("/map")
def map_():
    level = params.choice(request.args.get("level"), ("sido", "sgg", "umd"), "수준", "sido")
    parent = request.args.get("parent") or None
    if level == "sgg" and not (parent and parent.isdigit() and len(parent) == 2):
        raise BadParam("시군구 지도는 시도 코드(parent, 2자리)가 필요합니다.")
    if level == "umd" and not (parent and parent.isdigit() and len(parent) == 5):
        raise BadParam("읍면동 지도는 시군구 코드(parent, 5자리)가 필요합니다.")
    band = params.choice(request.args.get("band"), [b for b, _ in params.BANDS], "면적 구간", "all")
    ym_from, ym_to = _map_period()
    with db.connection() as conn:
        version = queries.active_version(conn)
        data = queries.map_values(conn, version, level, parent if level != "sido" else None,
                                  band, ym_from, ym_to)
    return jsonify(version=version, level=level, parent=parent if level != "sido" else None, band=band,
                   **{"from": ym_from, "to": ym_to}, **data)


@bp.route("/summary")
def summary():
    with db.connection() as conn:
        return jsonify(queries.summary(conn, queries.active_version(conn)))


@bp.route("/complexes")
def complexes_search():
    with db.connection() as conn:
        version = queries.active_version(conn)
        rows = queries.search_complexes(conn, version, request.args.get("q") or None,
                                        request.args.get("region") or None)
    return jsonify(rows)


@bp.route("/complexes/locations")
def complexes_locations():
    with db.connection() as conn:
        rows = queries.complex_locations(conn, request.args.get("q") or None, request.args.get("region") or None)
    return jsonify(fields=["apt_seq", "apt_nm", "lon", "lat"], complexes=rows)


@bp.route("/complexes/<apt_seq>")
def complex_detail(apt_seq):
    try:
        with db.connection() as conn:
            return jsonify(queries.complex_detail(conn, queries.active_version(conn), apt_seq))
    except LookupError:
        return jsonify(error="없는 단지입니다."), 404


@bp.route("/complexes/<apt_seq>/nearby")
def complex_nearby(apt_seq):
    try:
        with db.connection() as conn:
            return jsonify(queries.nearby(conn, queries.active_version(conn), apt_seq))
    except LookupError:
        return jsonify(error="없는 단지입니다."), 404

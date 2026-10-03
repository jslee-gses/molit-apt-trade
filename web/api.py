"""JSON API (기존 형식 유지: 거래 필드는 API 필드명)."""
from flask import Blueprint, jsonify, request

import db
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

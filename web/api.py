"""JSON API (기존 형식 유지: 거래 필드는 API 필드명)."""
from flask import Blueprint, jsonify, request

import db
from collector import jobs, quality
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
    limit = min(int(request.args.get("limit", 100) or 100), 1000)
    with db.connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY deal_date DESC NULLS LAST, id DESC LIMIT %s",
            [*params, limit]).fetchall()
    return jsonify([api_row(r) for r in rows])

"""데이터 추출 라우트."""
from flask import Blueprint, Response, jsonify, request

from analytics import export, queries
from analytics.params import BadParam

bp = Blueprint("export", __name__)


@bp.errorhandler(BadParam)
def bad_param(e):
    return jsonify(error=str(e)), 400


@bp.errorhandler(queries.NotReady)
def not_ready(e):
    return jsonify(error=str(e)), 503


def _download(fmt):
    sql, args, columns, stem = export.plan(request.args)
    if fmt == "csv":
        body, mimetype = export.csv_stream(sql, args, columns), "text/csv"
    else:
        body, mimetype = export.parquet_stream(sql, args, columns), "application/vnd.apache.parquet"
    return Response(body, mimetype=mimetype,
                    headers={"Content-Disposition": f"attachment; filename={stem}.{fmt}"})


@bp.route("/export.csv")
def export_csv():
    return _download("csv")


@bp.route("/export.parquet")
def export_parquet():
    return _download("parquet")


@bp.route("/export/codebook.csv")
def codebook():
    return Response(export.codebook_csv(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=apt_codebook.csv"})

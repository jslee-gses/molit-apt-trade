"""화면: 거래 목록, 수집 현황."""
from urllib.parse import urlencode

from flask import Blueprint, abort, redirect, render_template, request, url_for

import db
import settings
from analytics import aggregates, params, queries
from analytics.params import BadParam
from collector import jobs, quality
from geo import complexes, pipeline
from web.common import CODES, SIDO, api_row, filters

bp = Blueprint("pages", __name__)
PAGE_SIZE = 50


def count_trades(conn, args):
    """지역·기간 조건만 있으면 jobs의 저장 건수 합으로 바로 계산하고, 검색어가 있을 때만 행을 센다."""
    if args.get("q"):
        where, params = filters(args)
        return conn.execute(f"SELECT COUNT(*) AS n FROM trades{where}", params).fetchone()["n"]
    where, params = filters(args, for_jobs=True)
    col = "stored_count - COALESCE(q_cancelled, 0)" if args.get("exclude_cancelled") else "stored_count"
    return conn.execute(f"SELECT COALESCE(SUM({col}), 0) AS n FROM jobs{where}", params).fetchone()["n"]


@bp.route("/")
def dashboard():
    return render_template("dashboard.html", p=jobs.progress())


@bp.route("/trends")
def trends_page():
    return render_template("trends.html", p=jobs.progress(), bands=params.BANDS)


@bp.route("/map")
def map_page():
    return render_template("map.html", p=jobs.progress(), bands=params.BANDS)


@bp.route("/export")
def export_page():
    return render_template("export.html", p=jobs.progress(), bands=params.BANDS)


@bp.route("/trades")
def trades():
    args = request.args
    where, params = filters(args)
    try:
        page = max(int(args.get("page", 1) or 1), 1)
    except ValueError:
        return "page는 숫자여야 합니다.", 400
    with db.connection() as conn:
        total = count_trades(conn, args)
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY deal_date DESC NULLS LAST, id DESC LIMIT %s OFFSET %s",
            [*params, PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
        all_months = [r["deal_ymd"] for r in conn.execute(
            "SELECT DISTINCT deal_ymd FROM jobs ORDER BY deal_ymd DESC")]
    year = args.get("year", "")
    sido = args.get("sido", "")
    sigungu = CODES[CODES["시도"] == sido] if sido else CODES
    return render_template(
        "trades.html", rows=[api_row(r) for r in rows], total=total, page=page,
        pages=max((total - 1) // PAGE_SIZE + 1, 1), args=args, sido_list=SIDO,
        sigungu_list=sigungu.to_dict("records"), months=[m for m in all_months if m.startswith(year)],
        p=jobs.progress(), years=sorted({m[:4] for m in all_months}, reverse=True),
    )


@bp.route("/status")
def status():
    with db.connection() as conn:
        g = dict(complexes.summary(conn), failed_rows=complexes.failed(conn), pipeline=pipeline.state)
        agg = aggregates.status(conn)
    return render_template(
        "status.html", p=jobs.progress(), q=quality.quality_report(), g=g, agg=agg, start_ymd=settings.START_YMD,
        refresh_months=settings.REFRESH_MONTHS, refresh_at=settings.REFRESH_AT,
        recheck_days=settings.RECHECK_DAYS, old_recheck_days=settings.OLD_RECHECK_DAYS,
        interval=settings.REQUEST_INTERVAL,
    )


@bp.route("/download.csv")
def download():
    """옛 주소: 같은 조건으로 /export.csv(원본)로 보낸다."""
    # 옛 다운로드는 exclude_cancelled가 없으면 해제 거래도 포함했다
    args = request.args.to_dict()
    if not args.pop("exclude_cancelled", None):
        args["cancelled"] = "1"
    args["target"] = "raw"
    return redirect(url_for("export.export_csv") + "?" + urlencode(args), code=302)


@bp.route("/complexes")
def complexes_page():
    q = request.args.get("q", "").strip()
    sido, sgg, umd = (request.args.get(k, "").strip() for k in ("sido", "sgg", "umd"))
    # 상위를 바꿨는데 하위 값이 남은 경우: 상위에 속하지 않는 하위는 버린다
    if sgg and not (sido and sgg.startswith(sido)):
        sgg = ""
    if umd and not (sgg and umd.startswith(sgg)):
        umd = ""
    region = umd or sgg or sido or request.args.get("region", "").strip()
    rows, error, opts = [], None, dict(sido=[], sgg=[], umd=[])
    # 시군구·읍면동을 고르면 그 지역 단지를 모두, 아니면 최근 거래 순 50개
    all_rows = len(region) in (5, 8)
    try:
        with db.connection() as conn:
            version = queries.active_version(conn)
            opts["sido"] = queries.regions(conn, version, "sido")
            opts["sgg"] = queries.regions(conn, version, "sgg", sido) if sido else []
            opts["umd"] = queries.regions(conn, version, "umd", sgg) if sgg else []
            rows = queries.search_complexes(conn, version, q or None, region or None, limit=None if all_rows else 50)
    except (BadParam, queries.NotReady) as e:
        error = str(e)
    return render_template("complexes.html", p=jobs.progress(), rows=rows, q=q, region=region, error=error,
                           sel=dict(sido=sido, sgg=sgg, umd=umd), opts=opts, all_rows=all_rows)


@bp.route("/complexes/<apt_seq>")
def complex_page(apt_seq):
    try:
        with db.connection() as conn:
            detail = queries.complex_detail(conn, queries.active_version(conn), apt_seq)
    except queries.NotReady:
        abort(503)
    except LookupError:
        abort(404)
    return render_template("complex.html", p=jobs.progress(), c=detail["complex"], trades=detail["trades"][:200])

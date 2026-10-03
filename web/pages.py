"""화면: 거래 목록, 수집 현황, CSV 다운로드."""
import csv
import io

from flask import Blueprint, Response, render_template, request

import db
import settings
from collector import jobs, quality
from web.common import CODES, LABELS, SIDO, api_row, filters

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
def index():
    args = request.args
    where, params = filters(args)
    page = max(int(args.get("page", 1) or 1), 1)
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
        "index.html", rows=[api_row(r) for r in rows], total=total, page=page,
        pages=max((total - 1) // PAGE_SIZE + 1, 1), args=args, sido_list=SIDO,
        sigungu_list=sigungu.to_dict("records"), months=[m for m in all_months if m.startswith(year)],
        p=jobs.progress(), years=sorted({m[:4] for m in all_months}, reverse=True),
    )


@bp.route("/status")
def status():
    return render_template(
        "status.html", p=jobs.progress(), q=quality.quality_report(), start_ymd=settings.START_YMD,
        refresh_months=settings.REFRESH_MONTHS, refresh_at=settings.REFRESH_AT,
        recheck_days=settings.RECHECK_DAYS, old_recheck_days=settings.OLD_RECHECK_DAYS,
        interval=settings.REQUEST_INTERVAL,
    )


@bp.route("/download.csv")
def download():
    args = request.args
    if not any(args.get(k) for k in ("sido", "lawd_cd", "year", "ymd")):
        # 전체(2006년~) 한 번에 내려받으면 수 GB가 되어 서버에 부담이 크다
        return Response("시도·시군구·연도·계약월 중 하나 이상을 선택한 뒤 내려받으세요.",
                        status=400, mimetype="text/plain; charset=utf-8")
    where, params = filters(args)

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        # utf-8-sig: 엑셀에서 한글이 깨지지 않도록 BOM을 맨 앞에 붙인다
        yield "\ufeff".encode("utf-8")
        writer.writerow([label for _, label in LABELS])
        # 서버 측 커서(이름 있는 커서)는 트랜잭션 안에서만 동작한다
        with db.connection() as conn, conn.transaction(), conn.cursor(name="download") as cur:
            cur.execute(f"SELECT * FROM trades{where} ORDER BY deal_date, lawd_cd", params)
            while batch := cur.fetchmany(5000):
                for row in batch:
                    d = api_row(row)
                    writer.writerow(["" if d.get(k) is None else d.get(k) for k, _ in LABELS])
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate()
        if buf.tell():
            yield buf.getvalue().encode("utf-8")

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=apt_trades.csv"})

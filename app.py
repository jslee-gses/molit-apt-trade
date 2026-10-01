"""아파트 매매 실거래가를 자동 수집·최신화해 보여주는 웹앱."""
import csv
import io
import logging
import os
from contextlib import closing

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, Response, jsonify, render_template, request

import collector as c

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_MINUTES = int(os.environ.get("BATCH_MINUTES", "1"))
BATCH_JOBS = int(os.environ.get("BATCH_JOBS", "20"))
PAGE_SIZE = 50

# CSV·화면용 한글 열 이름
COLUMNS = [
    ("dealDate", "계약일"), ("sido", "시도"), ("sigungu", "시군구"), ("lawd_cd", "시군구코드"),
    ("umdNm", "법정동"), ("jibun", "지번"), ("roadNm", "도로명"), ("roadNmBonbun", "건물본번"),
    ("roadNmBubun", "건물부번"), ("aptNm", "단지명"), ("aptDong", "동"), ("aptSeq", "단지일련번호"),
    ("excluUseAr", "전용면적"), ("floor", "층"), ("buildYear", "건축년도"), ("dealAmount", "거래금액(만원)"),
    ("dealingGbn", "거래유형"), ("estateAgentSggNm", "중개사소재지"), ("slerGbn", "매도자"),
    ("buyerGbn", "매수자"), ("cdealType", "해제여부"), ("cdealDay", "해제사유발생일"),
    ("rgstDate", "등기일자"), ("landLeaseholdGbn", "토지임대부"), ("sggCd", "법정동시군구코드"),
    ("umdCd", "법정동읍면동코드"), ("bonbun", "본번"), ("bubun", "부번"), ("deal_ymd", "계약년월"),
    ("collected_at", "수집시각"),
]

app = Flask(__name__)
CODES = c.load_codes()
SIDO = list(dict.fromkeys(CODES["시도"]))
NAMES = {r.LAWD_CD: (r.시도, r.시군구) for r in CODES.itertuples()}


def filters():
    args = request.args
    where, params = [], []
    if args.get("sido"):
        codes = CODES.loc[CODES["시도"] == args["sido"], "LAWD_CD"].tolist() or [""]
        where.append(f"lawd_cd IN ({','.join('?' * len(codes))})")
        params += codes
    if args.get("lawd_cd"):
        where.append("lawd_cd = ?")
        params.append(args["lawd_cd"])
    if args.get("year"):
        where.append("deal_ymd BETWEEN ? AND ?")
        params += [f"{args['year']}01", f"{args['year']}12"]
    if args.get("ymd"):
        where.append("deal_ymd = ?")
        params.append(args["ymd"].replace("-", ""))
    if args.get("q"):
        where.append("(aptNm LIKE ? OR umdNm LIKE ? OR roadNm LIKE ?)")
        params += [f"%{args['q']}%"] * 3
    if args.get("exclude_cancelled"):
        where.append("TRIM(COALESCE(cdealType,'')) = ''")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def count_trades(conn, where, params):
    """지역·기간 조건만 있으면 jobs의 저장 건수 합으로 바로 계산하고, 검색어가 있을 때만 행을 센다."""
    if request.args.get("q"):
        return conn.execute(f"SELECT COUNT(*) FROM trades{where}", params).fetchone()[0]
    jw = where.replace("TRIM(COALESCE(cdealType,'')) = ''", "1=1")
    col = "stored_count - COALESCE(q_cancelled, 0)" if request.args.get("exclude_cancelled") else "stored_count"
    return conn.execute(f"SELECT COALESCE(SUM({col}), 0) FROM jobs{jw}", params).fetchone()[0]


def with_names(row):
    d = dict(row)
    d["sido"], d["sigungu"] = NAMES.get(d["lawd_cd"], ("", ""))
    return d


@app.route("/")
def index():
    where, params = filters()
    page = max(int(request.args.get("page", 1) or 1), 1)
    with closing(c.connect()) as conn:
        total = count_trades(conn, where, params)
        rows = conn.execute(
            f"SELECT * FROM trades{where} ORDER BY dealDate DESC, rowid DESC LIMIT ? OFFSET ?",
            params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
        ).fetchall()
        all_months = [r[0] for r in conn.execute("SELECT DISTINCT deal_ymd FROM jobs ORDER BY deal_ymd DESC")]
    year = request.args.get("year", "")
    months = [m for m in all_months if m.startswith(year)]
    sido = request.args.get("sido", "")
    sigungu = CODES[CODES["시도"] == sido] if sido else CODES
    return render_template(
        "index.html", rows=[with_names(r) for r in rows], total=total, page=page,
        pages=max((total - 1) // PAGE_SIZE + 1, 1), args=request.args, sido_list=SIDO,
        sigungu_list=sigungu.to_dict("records"), months=months, p=c.progress(),
        years=sorted({m[:4] for m in all_months}, reverse=True),
    )


@app.route("/status")
def status():
    return render_template("status.html", p=c.progress(), q=c.quality_report(),
                           start_ymd=c.START_YMD, refresh_months=c.REFRESH_MONTHS, refresh_at=c.REFRESH_AT,
                           recheck_days=c.RECHECK_DAYS, old_recheck_days=c.OLD_RECHECK_DAYS, interval=c.REQUEST_INTERVAL)


@app.route("/download.csv")
def download():
    args = request.args
    if not any(args.get(k) for k in ("sido", "lawd_cd", "year", "ymd")):
        # 전체(2006년~) 한 번에 내려받으면 수 GB가 되어 서버에 부담이 크다
        return Response("시도·시군구·연도·계약월 중 하나 이상을 선택한 뒤 내려받으세요.",
                        status=400, mimetype="text/plain; charset=utf-8")
    where, params = filters()

    def generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        # utf-8-sig: 엑셀에서 한글이 깨지지 않도록 BOM을 맨 앞에 붙인다
        yield "﻿".encode("utf-8")
        writer.writerow([label for _, label in COLUMNS])
        with closing(c.connect()) as conn:
            cur = conn.execute(f"SELECT * FROM trades{where} ORDER BY dealDate, lawd_cd", params)
            while batch := cur.fetchmany(5000):
                for row in batch:
                    d = with_names(row)
                    writer.writerow(["" if d.get(k) is None else d.get(k) for k, _ in COLUMNS])
                yield buf.getvalue().encode("utf-8")
                buf.seek(0)
                buf.truncate()
        if buf.tell():
            yield buf.getvalue().encode("utf-8")

    return Response(generate(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=apt_trades.csv"})


@app.route("/api/status")
def api_status():
    return jsonify(c.progress())


@app.route("/api/quality")
def api_quality():
    return jsonify(c.quality_report())


@app.route("/api/trades")
def api_trades():
    where, params = filters()
    limit = min(int(request.args.get("limit", 100) or 100), 1000)
    with closing(c.connect()) as conn:
        rows = conn.execute(f"SELECT * FROM trades{where} ORDER BY dealDate DESC LIMIT ?",
                            params + [limit]).fetchall()
    return jsonify([with_names(r) for r in rows])


@app.template_filter("comma")
def comma(v):
    return f"{v:,}" if isinstance(v, (int, float)) else (v or "")


@app.template_filter("mb")
def mb(v):
    return f"{(v or 0) / 1024 / 1024:,.1f}MB"


def start_background():
    c.init_db()
    c.ensure_jobs()
    scheduler = BackgroundScheduler(timezone="Asia/Seoul")
    scheduler.add_job(c.run_batch, "interval", minutes=BATCH_MINUTES, kwargs={"max_jobs": BATCH_JOBS},
                      id="collect", max_instances=1, coalesce=True, next_run_time=None)
    scheduler.start()
    scheduler.get_job("collect").modify(next_run_time=c.now_kst())  # 시작 직후 1회


if os.environ.get("COLLECTOR_DISABLED") != "1":
    start_background()
else:
    c.init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))

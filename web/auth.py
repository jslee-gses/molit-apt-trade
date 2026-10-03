"""공유 비밀번호 로그인(연구실 범위). 다중 사용자 계정은 범위 밖."""
import hmac
import threading
import time
from collections import defaultdict, deque
from urllib.parse import quote

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for

import settings

bp = Blueprint("auth", __name__)

MAX_FAILURES = 5
WINDOW_SECONDS = 60
_failures = defaultdict(deque)
_failures_lock = threading.Lock()
_OPEN_ENDPOINTS = {"auth.login", "static"}


def reset():
    with _failures_lock:
        _failures.clear()


def _recent_failures(ip):
    now = time.monotonic()
    with _failures_lock:
        q = _failures[ip]
        while q and now - q[0] > WINDOW_SECONDS:
            q.popleft()
        return len(q)


def _record_failure(ip):
    with _failures_lock:
        _failures[ip].append(time.monotonic())


def _safe_next(target):
    """같은 사이트 안의 경로만 허용한다(//host, scheme://, 역슬래시 우회 차단)."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/"
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        ip = request.remote_addr or "?"
        if _recent_failures(ip) >= MAX_FAILURES:
            return render_template("login.html", error="시도 횟수가 많습니다. 1분 뒤 다시 시도하세요."), 429
        password = request.form.get("password", "")
        if hmac.compare_digest(password.encode(), settings.require("APP_PASSWORD").encode()):
            session.clear()
            session["auth"] = True
            session.permanent = True
            return redirect(_safe_next(request.args.get("next")))
        _record_failure(ip)
        error = "비밀번호가 맞지 않습니다."
    return render_template("login.html", error=error)


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("auth.login"))


def protect(app):
    @app.before_request
    def require_login():
        if request.endpoint in _OPEN_ENDPOINTS or session.get("auth"):
            return None
        if request.path.startswith("/api/"):
            return jsonify(error="로그인이 필요합니다."), 401
        nxt = request.full_path.rstrip("?")
        return redirect(f"{url_for('auth.login')}?next={quote(nxt, safe='')}")

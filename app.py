"""아파트 매매 실거래가를 자동 수집·최신화하고 분석하는 웹앱."""
import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from flask import Flask
from flask.json.provider import DefaultJSONProvider
from werkzeug.middleware.proxy_fix import ProxyFix

import db
import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# 정적 파일은 1년 캐시: 화면을 옮길 때마다 브라우저가 글꼴·CSS를 서버에 다시 확인하면(304) 그동안 대체 글꼴로
# 그렸다가 바꿔 그려 화면이 출렁인다. url_for('static')에는 배포 버전(?v=)을 붙여 새 배포 때 새 파일을 받게 한다.
# 글꼴(woff2)은 바뀌지 않고, 경계 파일은 버전 폴더(static/geo/<YYYY-MM>/)로 나뉘어 버전 쿼리가 필요 없다.
STATIC_MAX_AGE = 365 * 24 * 3600
ASSET_VERSION = (settings.env("RAILWAY_GIT_COMMIT_SHA") or datetime.now().strftime("%Y%m%d%H%M%S"))[:12]


def _json_default(o):
    if isinstance(o, datetime):
        return o.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(o, date):
        return o.isoformat()
    if isinstance(o, Decimal):
        return float(o)
    return DefaultJSONProvider.default(o)


class JSONProvider(DefaultJSONProvider):
    default = staticmethod(_json_default)
    ensure_ascii = False


def create_app():
    from web import api, auth, export, pages
    from web.common import register_filters

    flask_app = Flask(__name__)
    flask_app.secret_key = settings.require("SECRET_KEY")
    settings.require("APP_PASSWORD")  # 없으면 시작 단계에서 실패
    flask_app.config.update(
        PERMANENT_SESSION_LIFETIME=timedelta(days=30),
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=bool(settings.env("RAILWAY_ENVIRONMENT")),  # Railway(HTTPS)에서만
        SEND_FILE_MAX_AGE_DEFAULT=STATIC_MAX_AGE,
    )

    @flask_app.context_processor
    def asset_version():
        return {"asset_version": ASSET_VERSION, "vworld_key": settings.VWORLD_KEY}

    @flask_app.url_defaults
    def static_version(endpoint, values):
        if endpoint == "static" and "v" not in values:
            values["v"] = ASSET_VERSION
    # Railway 프록시 뒤: 실제 클라이언트 IP·https를 반영(시도 제한·보안 쿠키용)
    # 가정: Railway 엣지 프록시가 실제 클라이언트 IP를 X-Forwarded-For의 마지막 항목으로 덧붙인다
    flask_app.wsgi_app = ProxyFix(flask_app.wsgi_app, x_for=1, x_proto=1)
    flask_app.json = JSONProvider(flask_app)
    db.migrate()
    import wiring
    wiring.wire()
    flask_app.register_blueprint(auth.bp)
    flask_app.register_blueprint(pages.bp)
    flask_app.register_blueprint(export.bp)
    flask_app.register_blueprint(api.bp)
    auth.protect(flask_app)
    register_filters(flask_app)
    return flask_app


app = create_app()

if settings.flag("COLLECT_ENABLED", True):
    import scheduler
    scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(settings.env("PORT", "8000")))

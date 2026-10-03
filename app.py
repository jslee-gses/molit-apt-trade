"""아파트 매매 실거래가를 자동 수집·최신화하고 분석하는 웹앱."""
import logging
from datetime import date, datetime
from decimal import Decimal

from flask import Flask
from flask.json.provider import DefaultJSONProvider

import db
import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


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
    from web import api, pages
    from web.common import register_filters

    flask_app = Flask(__name__)
    flask_app.secret_key = settings.require("SECRET_KEY")
    flask_app.json = JSONProvider(flask_app)
    db.migrate()
    flask_app.register_blueprint(pages.bp)
    flask_app.register_blueprint(api.bp)
    register_filters(flask_app)
    return flask_app


app = create_app()

if settings.flag("COLLECT_ENABLED", True):
    import scheduler
    scheduler.start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(settings.env("PORT", "8000")))

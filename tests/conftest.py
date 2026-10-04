"""테스트 공통 설정: 로컬 Postgres 테스트 DB를 테스트마다 비우고 스키마를 새로 만든다."""
import os
from urllib.parse import urlparse

import psycopg
import pytest

import settings

TEST_URL = settings.env("TEST_DATABASE_URL")
if not TEST_URL:
    pytest.exit(".env에 TEST_DATABASE_URL(로컬 테스트 DB)을 설정하세요.", returncode=2)
if urlparse(TEST_URL).hostname not in ("localhost", "127.0.0.1"):
    pytest.exit("TEST_DATABASE_URL은 로컬 DB만 허용합니다.", returncode=2)

# 앱 코드가 운영 DB나 실제 키를 보지 않도록 테스트 값으로 덮어쓴다
os.environ["DATABASE_URL"] = TEST_URL
os.environ["COLLECT_ENABLED"] = "false"
os.environ["APP_PASSWORD"] = "test-password"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["MOLIT_SERVICE_KEY"] = "test-key"


@pytest.fixture
def pg():
    """빈 스키마에 마이그레이션을 적용한 DB. db 모듈을 돌려준다."""
    import db
    db.close_pool()
    with psycopg.connect(TEST_URL, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public")
    db.migrate()
    yield db
    db.close_pool()


@pytest.fixture
def app(pg):
    import app as app_module
    from web import auth
    auth.reset()
    flask_app = app_module.create_app()
    flask_app.config.update(TESTING=True)
    return flask_app


@pytest.fixture
def client(app):
    """로그인된 테스트 클라이언트."""
    c = app.test_client()
    with c.session_transaction() as s:
        s["auth"] = True
    return c

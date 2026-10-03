import pytest


@pytest.fixture
def anon(app):
    from web import auth
    auth.reset()
    return app.test_client()


def test_pages_redirect_to_login(anon):
    resp = anon.get("/status")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login?next=%2Fstatus"


def test_api_returns_401(anon):
    resp = anon.get("/api/status")
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "로그인이 필요합니다."}


def test_login_page_renders(anon):
    resp = anon.get("/login")
    assert resp.status_code == 200
    assert "비밀번호" in resp.get_data(as_text=True)


def test_login_wrong_password(anon):
    resp = anon.post("/login", data={"password": "nope"})
    assert resp.status_code == 200
    assert "비밀번호가 맞지 않습니다" in resp.get_data(as_text=True)
    assert anon.get("/status").status_code == 302


def test_login_success_redirects_to_next(anon):
    resp = anon.post("/login?next=/status", data={"password": "test-password"})
    assert resp.status_code == 302 and resp.headers["Location"] == "/status"
    assert anon.get("/status").status_code == 200


@pytest.mark.parametrize("bad_next", ["//evil.example", "https://evil.example", "/\\evil.example"])
def test_login_rejects_external_next(anon, bad_next):
    resp = anon.post(f"/login?next={bad_next}", data={"password": "test-password"})
    assert resp.headers["Location"] == "/"


def test_login_rate_limited(anon):
    for _ in range(5):
        anon.post("/login", data={"password": "nope"})
    resp = anon.post("/login", data={"password": "test-password"})
    assert resp.status_code == 429


def test_logout(client):
    assert client.get("/status").status_code == 200
    client.post("/logout")
    assert client.get("/status").status_code == 302

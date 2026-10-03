import settings


def test_env_prefers_environment(monkeypatch):
    monkeypatch.setenv("SOME_TEST_VAR", "from-env")
    assert settings.env("SOME_TEST_VAR", "default") == "from-env"


def test_env_default_when_missing(monkeypatch):
    monkeypatch.delenv("NOPE_TEST_VAR", raising=False)
    assert settings.env("NOPE_TEST_VAR", "default") == "default"


def test_require_raises_when_missing(monkeypatch):
    monkeypatch.delenv("NOPE_TEST_VAR", raising=False)
    try:
        settings.require("NOPE_TEST_VAR")
    except RuntimeError as e:
        assert "NOPE_TEST_VAR" in str(e)
    else:
        raise AssertionError("RuntimeError가 나야 한다")


def test_flag(monkeypatch):
    monkeypatch.setenv("FLAG_TEST", "false")
    assert settings.flag("FLAG_TEST", True) is False
    monkeypatch.setenv("FLAG_TEST", "1")
    assert settings.flag("FLAG_TEST", False) is True
    monkeypatch.delenv("FLAG_TEST")
    assert settings.flag("FLAG_TEST", True) is True


def test_read_dotenv(tmp_path):
    p = tmp_path / ".env"
    p.write_text('# 주석\nA=1\nB="two"\nC=\'three\'\nbroken line\n', encoding="utf-8")
    assert settings._read_dotenv(p) == {"A": "1", "B": "two", "C": "three"}


def test_service_key_unquotes_encoding_key(monkeypatch):
    monkeypatch.setenv("MOLIT_SERVICE_KEY", "abc%2Bdef%3D%3D")
    assert settings.service_key() == "abc+def=="


def test_now_ts_is_naive_kst(monkeypatch):
    from datetime import datetime
    monkeypatch.setattr(settings, "now_kst", lambda: datetime(2026, 10, 3, 6, 0, 1, 999, tzinfo=settings.KST))
    assert settings.now_ts() == datetime(2026, 10, 3, 6, 0, 1)
    assert settings.now_str() == "2026-10-03 06:00:01"

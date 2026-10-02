import base64
import os

import pytest

from sbob.auth import browser


def test_parse_token_location():
    raw = base64.b64encode(b"abcdef0123:::TOKEN123:::PRIV456").decode()
    assert browser.parse_token_location(f"moodlemobile://token={raw}") == "TOKEN123"
    with pytest.raises(ValueError):
        browser.parse_token_location("https://webeep.polimi.it/login/index.php")


def test_token_storage_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(browser, "CONFIG_HOME", tmp_path)
    monkeypatch.setattr(browser, "TOKEN_FILE", tmp_path / "webeep_token")
    monkeypatch.delenv("WEBEEP_TOKEN", raising=False)
    assert browser.load_token() is None
    browser.save_token("segreto")
    assert browser.load_token() == "segreto" and oct(os.stat(tmp_path / "webeep_token").st_mode)[-3:] == "600"
    monkeypatch.setenv("WEBEEP_TOKEN", "da-env")
    assert browser.load_token() == "da-env"


class _Resp:
    def __init__(self, status, location): self.status, self.headers = status, {"location": location}


class _Ctx:
    def __init__(self, location):
        self.location, self.cookies_data = location, []
        self.request = self

    def get(self, url, max_redirects=None):
        assert "service=moodle_mobile_app" in url and max_redirects == 0
        return _Resp(303, self.location)


def test_webeep_token_from_redirect():
    raw = base64.b64encode(b"x:::TOK:::y").decode()
    assert browser._webeep_token(_Ctx(f"moodlemobile://token={raw}")) == "TOK"
    with pytest.raises(RuntimeError):
        browser._webeep_token(_Ctx("https://webeep.polimi.it/login/index.php"))

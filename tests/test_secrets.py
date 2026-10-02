import json
import os

from sbob.auth import browser
from sbob.core import secrets


def test_cookie_written_private_and_merged(tmp_path, monkeypatch):
    store = tmp_path / "sbob" / "cookies.json"
    legacy = tmp_path / "prd" / "cookies.json"
    monkeypatch.setattr(secrets, "cookie_store", lambda: store)
    monkeypatch.setattr(secrets, "prd_cookie_store", lambda: legacy)
    secrets.save_cookie("ticket", "AAA")
    secrets.save_cookie("MoodleSession", "BBB")
    assert json.loads(store.read_text()) == {"ticket": "AAA", "MoodleSession": "BBB"}
    assert oct(store.stat().st_mode)[-3:] == "600" and oct(store.parent.stat().st_mode)[-3:] == "700"
    assert secrets.cookie_names() == ["MoodleSession", "ticket"]              # solo nomi, mai valori
    assert secrets.load_cookie("ticket") == "AAA"


def test_load_cookie_falls_back_to_old_downloader_store(tmp_path, monkeypatch):
    legacy = tmp_path / "prd" / "cookies.json"
    legacy.parent.mkdir()
    legacy.write_text(json.dumps({"ticket": "VECCHIO"}))
    monkeypatch.setattr(secrets, "cookie_store", lambda: tmp_path / "sbob" / "cookies.json")
    monkeypatch.setattr(secrets, "prd_cookie_store", lambda: legacy)
    assert secrets.load_cookie("ticket") == "VECCHIO" and secrets.load_cookie("MoodleSession") is None
    secrets.save_cookie("ticket", "NUOVO")
    assert secrets.load_cookie("ticket") == "NUOVO"                            # il file di sbob ha la precedenza


def test_secure_permissions_tightens(tmp_path, monkeypatch):
    monkeypatch.setattr(secrets, "CONFIG_HOME", tmp_path)
    monkeypatch.setattr(secrets, "prd_cookie_store", lambda: tmp_path / "x" / "cookies.json")
    (tmp_path / "browser").mkdir()
    tok = tmp_path / "webeep_token"
    tok.write_text("t")
    os.chmod(tok, 0o644)
    os.chmod(tmp_path / "browser", 0o755)
    fixed = secrets.secure_permissions()
    assert str(tok) in fixed and oct(tok.stat().st_mode)[-3:] == "600"
    assert oct((tmp_path / "browser").stat().st_mode)[-3:] == "700"
    assert secrets.secure_permissions() == []                                  # idempotente


def test_set_cookie_never_uses_subprocess(settings, monkeypatch, tmp_path):
    import subprocess

    from sbob.steps import download
    monkeypatch.setattr(secrets, "cookie_store", lambda: tmp_path / "c.json")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no subprocess")))
    download.set_cookie(settings, "ticket", " v ")
    assert json.loads((tmp_path / "c.json").read_text()) == {"ticket": "v"}


def test_login_report_contains_no_secret_values(monkeypatch):
    """Il report di `sbob login` contiene solo nomi delle credenziali ottenute, mai i valori."""
    from typer.testing import CliRunner

    from sbob import cli
    monkeypatch.setattr(browser, "login", lambda *a, **k: {"webeep_token": True, "MoodleSession": True, "ticket": True})
    out = CliRunner().invoke(cli.app, ["login", "--json"]).stdout
    assert json.loads(out)["done"] == ["webeep_token", "MoodleSession", "ticket"]
    assert "SEGRETO" not in out

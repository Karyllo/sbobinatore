import tomllib

from sbob.config import load_settings
from sbob.core import doctor
from sbob.setup import needed_providers, render_config, render_course, write_env


def test_render_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)        # altrimenti load_settings carica il .env vero del repo
    course = render_course("edp", 'Metodi "EDP"', "2025-26", "4 anno /EDP", {"tipo": "txt", "file": "link.txt"})
    text = render_config("~/Università", "it", "gemini", [course])
    data = tomllib.loads(text)                                  # TOML valido
    assert data["modelli"]["notes"]["riserva"]["provider"] == "deepseek"
    cfg = tmp_path / "sbob.toml"
    cfg.write_text(text)
    s = load_settings(cfg)
    c = s.corso("edp")
    assert c.nome == 'Metodi "EDP"' and c.cartella.name == "EDP" and c.sorgente["tipo"] == "txt"
    ds = tomllib.loads(render_config("~/U", "en", "deepseek"))
    assert ds["modelli"]["refiner"]["provider"] == "deepseek" and ds["modelli"]["refiner"]["thinking"] is False


def test_needed_providers():
    assert needed_providers("gemini") == ["gemini", "deepseek"]
    assert needed_providers("anthropic") == ["gemini", "anthropic"]


def test_write_env_merges_and_is_private(tmp_path):
    env = tmp_path / ".env"
    env.write_text("ALTRO=1\nGOOGLE_API_KEY_ACCOUNT1=vecchia\n")
    write_env(env, {"GOOGLE_API_KEY_ACCOUNT1": "nuova", "DEEPSEEK_API_KEY_ACCOUNT1": ""})
    assert env.read_text().splitlines() == ["ALTRO=1", "GOOGLE_API_KEY_ACCOUNT1=nuova"]
    assert oct(env.stat().st_mode)[-3:] == "600"


def test_doctor_reports_missing_with_fix(settings, monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda n: None)
    import os
    for k in [k for k in os.environ if k.startswith("GOOGLE_API_KEY")]:
        monkeypatch.delenv(k)
    monkeypatch.setattr(doctor, "check_login", lambda: [])             # niente rete nei test
    from sbob.core import secrets
    monkeypatch.setattr(secrets, "cookie_names", lambda: [])          # niente credenziali vere
    checks = {c["nome"]: c for c in doctor.run_checks(settings, quick=True)}
    assert checks["ffmpeg"]["stato"] == "manca" and "ffmpeg" in checks["ffmpeg"]["rimedio"]
    assert checks["chiavi gemini"]["stato"] == "manca" and "GOOGLE_API_KEY_ACCOUNT1" in checks["chiavi gemini"]["rimedio"]
    assert checks["config"]["stato"] == "ok"
    assert checks["accesso Webex"]["rimedio"] == "sbob login"

import tomllib

from sbob.config import load_settings
from sbob.core import doctor
from sbob.setup import needed_providers, render_config, render_course, write_env


def load_settings_from_text(text, tmp_path):
    cfg = tmp_path / "solo_default.toml"
    cfg.write_text(text)
    return load_settings(cfg)


def test_render_config_roundtrip(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)        # altrimenti load_settings carica il .env vero del repo
    course = render_course("edp", 'Metodi "EDP"', "2025-26", "4 anno /EDP", {"tipo": "txt", "file": "link.txt"})
    text = render_config("~/Università", "it", "gemini", [course])
    data = tomllib.loads(text)                                  # TOML valido
    assert "modelli" not in data                                # Gemini: nessun modello nel file, valgono i default
    cfg = tmp_path / "sbob.toml"
    cfg.write_text(text)
    s = load_settings(cfg)
    c = s.corso("edp")
    assert c.nome == 'Metodi "EDP"' and c.cartella.name == "EDP" and c.sorgente["tipo"] == "txt"
    ds = tomllib.loads(render_config("~/U", "en", "deepseek"))
    assert ds["modelli"]["refiner"]["provider"] == "deepseek" and ds["modelli"]["refiner"]["thinking"] is False
    from sbob.config import DEFAULT_MODELLI
    assert load_settings_from_text(render_config("~/U", "it", "gemini"), tmp_path).modelli["notes"]["model"] == DEFAULT_MODELLI["notes"]["model"]


def test_needed_providers():
    assert needed_providers("gemini") == ["gemini"]                    # una sola chiave: niente DeepSeek da chiedere
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


def test_install_command_always_uses_the_github_address_and_keeps_base():
    """Il pacchetto non è su PyPI: un rimedio senza l'indirizzo GitHub non funzionerebbe per chi ha installato da lì,
    e senza `base` si perderebbero Gemini e il login."""
    from sbob.config import install_command
    cmd = install_command("pdf")
    assert cmd.startswith("uv tool install --reinstall") and "sbobinatore[base,pdf]" in cmd and "git+https://github.com/Karyllo/sbobinatore" in cmd
    assert install_command("base", "login").count("base") == 1 and "[base,login]" in install_command("base", "login")


def test_all_remedies_in_the_code_use_install_command():
    """Nessun comando di installazione scritto a mano (di solito senza indirizzo GitHub): passano tutti da install_command."""
    import re
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "sbob"
    for f in src.rglob("*.py"):
        if f.name == "config.py":
            continue
        assert not re.search(r'uv tool install --reinstall "sbobinatore\[', f.read_text()), f"{f.name}: usa install_command()"


def test_quota_texts_do_not_claim_20_per_model_for_every_model():
    """Misurato: solo i Flash hanno circa 20 richieste al giorno, i Lite molte di più."""
    from sbob.core.models import NOTE
    from sbob.setup import PRESETS
    assert "Lite" in PRESETS["gemini"]["label"] and "per modello" not in PRESETS["gemini"]["label"]
    assert "Lite" in NOTE and "20 richieste per modello" not in NOTE

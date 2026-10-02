import pytest

from sbob.config import load_settings


@pytest.fixture
def settings(tmp_path, monkeypatch):
    cfg = tmp_path / "sbob.toml"
    cfg.write_text(f'''
root = "{tmp_path / 'uni'}"
[corsi.prova]
nome = "Corso di Prova"
anno_accademico = "2025-26"
cartella = "1 anno /prova"
sorgente = {{ tipo = "txt", file = "link.txt" }}

[modelli.notes]
provider = "fake"
model = "fake-1"
rpm = 1000
tentativi = 3

[providers.fake]
tipo = "fake"
chiavi = "FAKE_KEY"
''', encoding="utf-8")
    monkeypatch.setenv("SBOB_CONFIG", str(cfg))
    monkeypatch.chdir(tmp_path)
    return load_settings()


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Nessun test deve attendere davvero (poll di notebooklm, backoff del registry)."""
    import time
    monkeypatch.setattr(time, "sleep", lambda s: None)

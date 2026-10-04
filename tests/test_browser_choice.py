"""Scelta del browser per `sbob login` e correzioni a `sbob init` (segnalate da un amico su Ubuntu con Firefox)."""

import pytest
from typer.testing import CliRunner

from sbob import setup
from sbob.auth import browser as br
from sbob.cli import app
from sbob.core.report import NeedsHuman

runner = CliRunner()


@pytest.fixture
def home(tmp_path, monkeypatch):
    """CONFIG_HOME e cache dei browser dentro tmp: nessun contatto col computer vero."""
    monkeypatch.setattr(br, "CONFIG_HOME", tmp_path / "cfg")
    monkeypatch.setattr(br, "KIND_FILE", tmp_path / "cfg" / "browser_kind")
    monkeypatch.setattr(br, "PROFILE_DIR", tmp_path / "cfg" / "browser")
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path / "pw"))
    monkeypatch.setattr(br.shutil, "which", lambda n: None)
    monkeypatch.setattr(br.Path, "exists", lambda self: False if "/Applications/" in str(self) else self.is_dir() or self.is_file())
    return tmp_path


class FakePW:
    """Finto Playwright: registra i tentativi e fa partire solo i browser indicati."""
    def __init__(self, works):
        self.works, self.tried = works, []
        self.chromium, self.firefox = self._engine("chromium"), self._engine("firefox")

    def _engine(self, name):
        outer = self

        class E:
            def launch_persistent_context(self, profile, channel=None, **kw):
                key = f"{name}:{channel}"
                outer.tried.append(key)
                if key not in outer.works:
                    raise RuntimeError("Executable doesn't exist")
                return key
        return E()


def test_chromium_tries_chrome_then_edge_then_dedicated(home):
    pw = FakePW({"chromium:msedge"})
    assert br._launch(pw, True) == "chromium:msedge" and pw.tried == ["chromium:chrome", "chromium:msedge"]
    pw = FakePW({"chromium:None"})
    assert br._launch(pw, True) == "chromium:None" and pw.tried == ["chromium:chrome", "chromium:msedge", "chromium:None"]
    with pytest.raises(br.BrowserMissing):
        br._launch(FakePW(set()), True)


def test_firefox_uses_only_its_dedicated_build_and_its_own_profile(home):
    pw = FakePW({"firefox:None"})
    assert br._launch(pw, True, "firefox") == "firefox:None" and pw.tried == ["firefox:None"]       # mai Chrome per Firefox
    with pytest.raises(br.BrowserMissing):
        br._launch(FakePW({"chromium:chrome"}), True, "firefox")
    assert br.profile_dir("firefox") != br.profile_dir("chromium") and br.profile_dir("firefox").name == "browser-firefox"


def test_kind_is_remembered_for_renewals_and_bad_values_fall_back(home):
    assert br.current_kind() == "chromium"
    br.set_kind("firefox")
    assert br.current_kind() == "firefox" and br.profile_dir() == br.profile_dir("firefox")
    br.KIND_FILE.write_text("netscape")
    assert br.current_kind() == "chromium"


def test_browser_installed_firefox_counts_only_the_bundled_build(home):
    assert not br.browser_installed("chromium") and not br.browser_installed("firefox")
    (home / "pw" / "chromium-1243").mkdir(parents=True)
    assert br.browser_installed("chromium") and not br.browser_installed("firefox")      # Chromium dedicato non basta a Firefox
    (home / "pw" / "firefox-1490").mkdir()
    assert br.browser_installed("firefox")


def test_login_rejects_unknown_browser(home, settings):
    with pytest.raises(NeedsHuman):
        br.login(settings, kind="netscape")


def test_missing_browser_in_background_asks_the_user_with_the_right_command(home, monkeypatch):
    import sys
    import types
    fake = types.ModuleType("playwright.sync_api")

    class CM:
        def __enter__(self): return FakePW(set())
        def __exit__(self, *a): return False
    fake.sync_playwright = lambda: CM()
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake)
    for kind, cmd in (("chromium", "sbob installa-browser"), ("firefox", "sbob installa-browser --browser firefox")):
        with pytest.raises(NeedsHuman) as e:
            with br.browser(True, kind):                              # headless = rinnovo automatico: mai domande
                pass
        assert e.value.action == cmd


def test_installa_browser_command_downloads_the_right_one(home, monkeypatch):
    calls = []
    monkeypatch.setattr(br, "install_dedicated_browser", lambda kind="chromium": calls.append(kind) or 0)
    r = runner.invoke(app, ["installa-browser", "--browser", "firefox"])
    assert r.exit_code == 0 and calls == ["firefox"] and br.current_kind() == "firefox"
    assert runner.invoke(app, ["installa-browser", "--browser", "netscape"]).exit_code != 0
    (home / "pw" / "chromium-1").mkdir(parents=True)
    calls.clear()
    assert runner.invoke(app, ["installa-browser", "--browser", "chromium"]).exit_code == 0 and calls == []   # già presente: niente download


def test_install_command_is_run_with_sbobs_own_python(monkeypatch):
    seen = []
    monkeypatch.setattr(br.subprocess, "call", lambda cmd: seen.append(cmd) or 0)
    br.install_dedicated_browser("firefox")
    assert seen[0][1:] == ["-m", "playwright", "install", "firefox"]


class Q:
    def __init__(self, value): self.value = value
    def ask(self): return self.value


def _fake_init(monkeypatch, tmp_path, answers):
    import questionary
    monkeypatch.setattr(setup, "CONFIG_HOME", tmp_path / "cfg")
    it = {k: iter(v) for k, v in answers.items()}
    monkeypatch.setattr(questionary, "path", lambda *a, **k: Q(str(tmp_path / "Uni")))
    monkeypatch.setattr(questionary, "select", lambda msg, **k: Q("it" if "Lingua" in msg else "gemini"))
    monkeypatch.setattr(questionary, "password", lambda *a, **k: Q(next(it["password"])))
    monkeypatch.setattr(questionary, "confirm", lambda msg, **k: Q(next(it["confirm"])))
    monkeypatch.setattr(questionary, "text", lambda *a, **k: Q(next(it["text"])))


def test_init_does_not_let_you_skip_the_key_by_accident(monkeypatch, tmp_path, capsys):
    # prima senza chiave e rispondendo "no" a "continuo lo stesso?" → richiede la chiave; poi la scrive; nessun corso a mano
    _fake_init(monkeypatch, tmp_path, {"password": ["", "CHIAVE-DI-PROVA"], "confirm": [False, False], "text": []})
    assert setup.init() == 0
    out = capsys.readouterr().out
    assert "sbob webeep scegli" in out and "CHIAVE" not in out                      # la chiave non viene mai stampata
    assert "GOOGLE_API_KEY_ACCOUNT1=CHIAVE-DI-PROVA" in (tmp_path / "cfg" / ".env").read_text()


def test_init_can_continue_without_key_when_the_user_insists(monkeypatch, tmp_path):
    _fake_init(monkeypatch, tmp_path, {"password": [""], "confirm": [True, False], "text": []})
    assert setup.init() == 0 and not (tmp_path / "cfg" / ".env").read_text().strip()


def test_init_course_loop_stops_on_empty_name(monkeypatch, tmp_path):
    # "corso a mano? sì" → nome vuoto → finito (prima chiedeva all'infinito "Nome del corso" senza uscita)
    _fake_init(monkeypatch, tmp_path, {"password": ["K"], "confirm": [True], "text": [""]})
    assert setup.init() == 0
    assert "[corsi." not in (tmp_path / "cfg" / "sbob.toml").read_text()

"""Contratto per gli agenti: con --json stdout è SOLO un oggetto JSON; l'exit code dice cosa è successo."""

import dataclasses
import json
import shutil
import subprocess

import pytest
from typer.testing import CliRunner

from sbob.cli import app
from sbob.core.layout import Layout

runner = CliRunner()
STEM = "2025-09-17_prova_lez01"


def test_status_json_is_pure(settings):
    r = runner.invoke(app, ["status", "prova", "--json"])
    assert r.exit_code == 0 and json.loads(r.stdout)["corso"] == "prova"


def test_corsi_json(settings):
    data = json.loads(runner.invoke(app, ["corsi", "--json"]).stdout)
    assert [c["slug"] for c in data["corsi"]] == ["prova"]


def test_unknown_course_is_error_exit_1(settings):
    r = runner.invoke(app, ["audio", "nonesiste", "--json"])
    d = json.loads(r.stdout)
    assert r.exit_code == 1 and "nonesiste" in d["error"] and d["ok"] is False


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg assente")
def test_audio_json_and_idempotence(settings):
    lay = Layout.of(settings.corso("prova"))
    lay.ensure("video")
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=duration=1", str(lay.video / f"{STEM}.mp4")],
                   check=True, capture_output=True)
    first = json.loads(runner.invoke(app, ["audio", "prova", "--json"]).stdout)
    assert first["done"] == [STEM] and first["exit_code"] == 0
    second = runner.invoke(app, ["audio", "prova", "--json"])
    assert json.loads(second.stdout)["skipped"] == [STEM] and second.exit_code == 0


def test_missing_keys_exit_3_with_action(settings, monkeypatch):
    for k in ("GOOGLE_API_KEY", "GOOGLE_API_KEY_ACCOUNT1"):
        monkeypatch.delenv(k, raising=False)
    lay = Layout.of(settings.corso("prova"))
    lay.ensure("trascrizioni")
    (lay.trascrizioni / f"{STEM}.md").write_text("parola " * 50)
    r = runner.invoke(app, ["appunti", "prova", "--json"])
    d = json.loads(r.stdout)
    assert r.exit_code == 3 and "GOOGLE_API_KEY" in d["needs_human"] and ".env" in d["action"]


def test_run_stops_on_human_and_dry_run_touches_nothing(settings):
    r = runner.invoke(app, ["run", "prova", "--dry-run", "--json", "--da", "audio", "--fino-a", "appunti"])
    d = json.loads(r.stdout)
    assert r.exit_code == 0 and [x["step"] for x in d["reports"]] == ["audio", "trascrivi", "appunti"]
    assert not Layout.of(settings.corso("prova")).video.exists()


def test_download_without_downloader_needs_human(settings):
    r = runner.invoke(app, ["download", "prova", "--json"])      # nessun ./link.txt né downloader nel tmp
    d = json.loads(r.stdout)
    assert r.exit_code == 3 and d["action"]


def test_library_prints_do_not_pollute_json(settings, monkeypatch):
    from sbob.steps import audio

    def noisy(ctx):
        print("warning: una libreria che stampa su stdout")
        return ctx.report("audio")
    monkeypatch.setattr(audio, "run", noisy)
    r = runner.invoke(app, ["audio", "prova", "--json"])
    assert json.loads(r.stdout)["step"] == "audio"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg assente")
def test_dry_run_chain_propagates(settings):
    lay = Layout.of(settings.corso("prova"))
    lay.ensure("video")
    (lay.video / f"{STEM}.mp4").write_text("x")
    d = json.loads(runner.invoke(app, ["run", "prova", "--dry-run", "--json", "--da", "audio"]).stdout)
    assert [r["step"] for r in d["reports"]] == ["audio", "trascrivi", "appunti", "mappa", "notebook"]
    assert [r["done"] for r in d["reports"]] == [[STEM]] * 4 + [[]]       # notebook: disattivato, non fa niente


def test_log_file_has_timestamps_and_traceback_on_crash(settings, monkeypatch):
    from sbob.steps import audio

    def boom(ctx):
        ctx.log("sto per rompermi")
        raise RuntimeError("bug finto")
    monkeypatch.setattr(audio, "run", boom)
    d = json.loads(runner.invoke(app, ["audio", "prova", "--json"]).stdout)
    log = (Layout.of(settings.corso("prova")).logs / "sbob.log").read_text()
    assert d["exit_code"] == 1 and "sbob.log" in d["error"]
    assert "INFO  [audio] sto per rompermi" in log and "ERROR [audio] Traceback" in log and "RuntimeError: bug finto" in log
    assert log.splitlines()[0][:4] == "2026" or log.splitlines()[0][4] == "-"                    # data e ora in testa


def test_chain_continues_after_quota_but_stops_on_login(settings, monkeypatch):
    from sbob import cli
    from sbob.core.report import NeedsHuman, QuotaExhausted

    def quota_step(ctx):
        rep = ctx.report(ctx.step)
        rep.done.append("lez01")                                     # già fatto prima che finisse la quota: non va perso
        raise QuotaExhausted("Quota esaurita", action="aspetta")

    def ok_step(ctx):
        return ctx.report(ctx.step)

    def login_step(ctx):
        raise NeedsHuman("scaduto", action="sbob login")

    steps = {"audio": quota_step, "trascrivi": ok_step, "appunti": login_step, "mappa": ok_step}
    monkeypatch.setattr(cli, "get_step", lambda name: steps[name])
    reps = cli._run_chain("prova", ("audio", "trascrivi", "appunti", "mappa"), as_json=True)
    assert [r.step for r in reps] == ["audio", "trascrivi", "appunti"]                 # "mappa" non parte: serve il login
    assert reps[0].done == ["lez01"] and reps[0].quota and reps[0].needs_human
    assert reps[2].needs_human and not reps[2].quota


def test_installa_skill_for_antigravity_links_from_repo(tmp_path, monkeypatch):
    from sbob import cli
    monkeypatch.setitem(cli.SKILL_TARGETS, "antigravity", tmp_path / "ag" / "skills")
    r = runner.invoke(app, ["installa-skill", "--per", "antigravity"])
    dst = tmp_path / "ag" / "skills" / "sbobinatore"
    assert r.exit_code == 0 and (dst / "SKILL.md").exists() and dst.is_symlink()          # dal clone: collegamento
    assert runner.invoke(app, ["installa-skill", "--per", "boh"]).exit_code != 0


def test_aggiorna_continues_with_next_course_after_quota_but_stops_on_login(settings, monkeypatch):
    from sbob import cli
    from sbob.core.report import StepReport
    s = settings
    s.corsi["prova"].webeep_id = 1
    other = dataclasses.replace(s.corsi["prova"], slug="altro", webeep_id=2)
    s.corsi["altro"] = other
    monkeypatch.setattr(cli, "_settings", lambda: s)
    monkeypatch.setattr("sbob.core.models.check", lambda st: {"avvisi": [], "nota": None})
    calls: list[str] = []

    def chain(corso, steps, **kw):
        calls.append(corso)
        if corso == "prova":      # A: quota finita
            return [StepReport(step="appunti", corso=corso, needs_human="quota", quota=True)]
        return [StepReport(step="appunti", corso=corso)]

    monkeypatch.setattr(cli, "_run_chain", chain)
    runner.invoke(app, ["aggiorna", "--json"])
    assert calls == ["prova", "altro"]                      # la quota di A non ferma B

    calls.clear()
    monkeypatch.setattr(cli, "_run_chain", lambda corso, steps, **kw: calls.append(corso) or
                        [StepReport(step="download", corso=corso, needs_human="login scaduto", action="sbob login")])
    runner.invoke(app, ["aggiorna", "--json"])
    assert calls == ["prova"]                               # un login scaduto sì


def test_permission_error_gives_message_not_traceback(monkeypatch, capsys):
    from sbob import cli

    def boom():
        raise PermissionError(1, "Operation not permitted", "/x/audio")
    monkeypatch.setattr(cli, "app", boom)
    monkeypatch.setattr(cli.sys, "argv", ["sbob", "stato"])
    try:
        cli.main()
    except SystemExit as e:
        assert e.code == 3
    assert "File e cartelle" in capsys.readouterr().err

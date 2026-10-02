"""Contratto per gli agenti: con --json stdout è SOLO un oggetto JSON; l'exit code dice cosa è successo."""

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
    assert [r["step"] for r in d["reports"]] == ["audio", "trascrivi", "appunti", "mappa"]
    assert [r["done"] for r in d["reports"]] == [[STEM]] * 4

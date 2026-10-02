from pathlib import Path

import pytest

from sbob import pianifica


def test_parse_time():
    assert pianifica.parse_time("03:00") == (3, 0) and pianifica.parse_time("7:05") == (7, 5)
    for bad in ("25:00", "3", "03:60", "mezzanotte"):
        with pytest.raises(ValueError):
            pianifica.parse_time(bad)


def test_plist_has_no_secrets_and_uses_config(tmp_path):
    cfg = tmp_path / "sbob.toml"
    p = pianifica.build_plist("/usr/local/bin/sbob", cfg, 3, 30, tmp_path / "log")
    assert p["ProgramArguments"] == ["/usr/local/bin/sbob", "aggiorna", "--notifica"]
    assert p["StartCalendarInterval"] == {"Hour": 3, "Minute": 30}
    assert p["EnvironmentVariables"]["SBOB_CONFIG"] == str(cfg) and p["WorkingDirectory"] == str(tmp_path)
    assert "/opt/homebrew/bin" in p["EnvironmentVariables"]["PATH"]
    assert not any(k for k in p["EnvironmentVariables"] if "KEY" in k or "TOKEN" in k)


def test_cron_line():
    assert pianifica.cron_line("/bin/sbob", Path("/x/sbob.toml"), 3, 5, Path("/l")).startswith("5 3 * * * SBOB_CONFIG='/x/sbob.toml'")


def test_install_and_remove_with_fake_launchctl(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(pianifica, "PLIST", tmp_path / "agent.plist")
    monkeypatch.setattr(pianifica, "LOG", tmp_path / "aggiorna.log")
    monkeypatch.setattr(pianifica, "sbob_binary", lambda: "/bin/sbob")
    monkeypatch.setattr(pianifica.sys, "platform", "darwin")
    import subprocess
    monkeypatch.setattr(pianifica, "_launchctl", lambda *a: calls.append(a) or subprocess.CompletedProcess(a, 0, "", ""))
    msg = pianifica.install(tmp_path / "sbob.toml", 3, 0)
    assert "03:00" in msg and pianifica.installed() and calls[0][0] == "bootstrap"
    assert "03:00" in pianifica.status()
    pianifica.install(None, 4, 0)                                       # reinstallazione: prima bootout
    assert calls[-2][0] == "bootout" and calls[-1][0] == "bootstrap"
    assert "rimosso" in pianifica.remove() and not pianifica.installed()

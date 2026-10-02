"""Aggiornamento notturno: `sbob pianifica` installa un job che lancia `sbob aggiorna` ogni giorno.

macOS: LaunchAgent in ~/Library/LaunchAgents (parte anche se il Mac era in stop all'ora prevista, appena si riattiva).
Altri sistemi: stampa la riga di crontab da aggiungere (non tocca il crontab).
Il job non ha credenziali nella riga di comando né nel plist: sbob legge token e chiavi dai soliti file
(`~/.config/sbob/`, `.env` accanto a sbob.toml).
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "com.sbob.aggiorna"
PLIST = Path("~/Library/LaunchAgents").expanduser() / f"{LABEL}.plist"
LOG = Path("~/.config/sbob/aggiorna.log").expanduser()
_TIME = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"        # launchd parte con un PATH minimo: serve ffmpeg


def parse_time(text: str) -> tuple[int, int]:
    m = _TIME.match(text.strip())
    if not m:
        raise ValueError(f"Ora non valida: '{text}' (usa HH:MM, per esempio 03:00)")
    return int(m.group(1)), int(m.group(2))


def sbob_binary() -> str:
    found = shutil.which("sbob")
    return str(Path(found).resolve()) if found else str(Path(sys.argv[0]).resolve())


def build_plist(binary: str, config: Path | None, hour: int, minute: int, log: Path = LOG) -> dict:
    env = {"PATH": f"{Path(binary).parent}:{DEFAULT_PATH}", "LANG": "it_IT.UTF-8"}
    if config:
        env["SBOB_CONFIG"] = str(config)
    return {"Label": LABEL, "ProgramArguments": [binary, "aggiorna", "--notifica"],
            "StartCalendarInterval": {"Hour": hour, "Minute": minute},
            "EnvironmentVariables": env,
            **({"WorkingDirectory": str(config.parent)} if config else {}),
            "StandardOutPath": str(log), "StandardErrorPath": str(log), "ProcessType": "Background"}


def cron_line(binary: str, config: Path | None, hour: int, minute: int, log: Path = LOG) -> str:
    env = f"SBOB_CONFIG='{config}' " if config else ""
    return f"{minute} {hour} * * * {env}'{binary}' aggiorna --notifica >> '{log}' 2>&1"


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def installed() -> bool:
    return PLIST.exists()


def install(config: Path | None, hour: int, minute: int) -> str:
    """Installa (o aggiorna) il job. Restituisce un messaggio per l'utente."""
    binary = sbob_binary()
    if sys.platform != "darwin":
        return f"Questo sistema non ha launchd. Aggiungi con `crontab -e`:\n{cron_line(binary, config, hour, minute)}"
    PLIST.parent.mkdir(parents=True, exist_ok=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    domain = f"gui/{os.getuid()}"
    if installed():
        _launchctl("bootout", domain, str(PLIST))
    with open(PLIST, "wb") as f:
        plistlib.dump(build_plist(binary, config, hour, minute), f)
    res = _launchctl("bootstrap", domain, str(PLIST))
    if res.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap fallito: {res.stderr.strip() or res.stdout.strip()}")
    return f"Aggiornamento automatico ogni giorno alle {hour:02d}:{minute:02d}. Log: {LOG}"


def remove() -> str:
    if sys.platform != "darwin":
        return "Rimuovi la riga di sbob con `crontab -e`."
    if not installed():
        return "Nessun aggiornamento automatico installato."
    _launchctl("bootout", f"gui/{os.getuid()}", str(PLIST))
    PLIST.unlink()
    return "Aggiornamento automatico rimosso."


def status() -> str:
    if sys.platform != "darwin":
        return "Su questo sistema il job va gestito con cron (`sbob pianifica` mostra la riga da aggiungere)."
    if not installed():
        return "Non installato (`sbob pianifica`)."
    data = plistlib.loads(PLIST.read_bytes())
    t = data.get("StartCalendarInterval", {})
    running = _launchctl("print", f"gui/{os.getuid()}/{LABEL}").returncode == 0
    return (f"Installato: ogni giorno alle {t.get('Hour', 0):02d}:{t.get('Minute', 0):02d} "
            f"({'attivo' if running else 'NON caricato in launchd'}). Log: {LOG}")


def notify(title: str, text: str) -> None:
    """Notifica di sistema (solo macOS). Il testo è fisso e breve: niente dati dei corsi né segreti."""
    if sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "{title}"'],
                       capture_output=True)

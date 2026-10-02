"""Unico punto in cui sbob tocca le credenziali (token WeBeep, cookie, profilo del browser).

Regole (vedi anche la skill):
  - i valori non vengono MAI stampati, messi nei report/JSON, passati sulla riga di comando o inclusi in un prompt LLM
  - i file che li contengono sono leggibili solo dall'utente (cartelle 700, file 600)
"""

from __future__ import annotations

import json
import os
import platform
from pathlib import Path

from sbob.config import CONFIG_HOME


def prd_cookie_store() -> Path:
    """Stesso percorso di typer.get_app_dir("polimi_recordings_downloader") usato dal downloader."""
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Application Support" / "polimi_recordings_downloader" / "cookies.json"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "polimi_recordings_downloader" / "cookies.json"


def _private_file(path: Path) -> None:
    if path.exists():
        os.chmod(path, 0o600)


def _private_dir(path: Path) -> None:
    if path.exists():
        os.chmod(path, 0o700)


def secure_permissions() -> list[str]:
    """Stringe i permessi di tutto ciò che contiene credenziali. Restituisce i percorsi sistemati."""
    fixed = []
    targets_dir = [CONFIG_HOME, CONFIG_HOME / "browser", prd_cookie_store().parent]
    targets_file = [CONFIG_HOME / "webeep_token", CONFIG_HOME / "browser_state.json", CONFIG_HOME / ".env",
                    prd_cookie_store()]
    for d in targets_dir:
        if d.exists() and (d.stat().st_mode & 0o077):
            _private_dir(d)
            fixed.append(str(d))
    for f in targets_file:
        if f.exists() and (f.stat().st_mode & 0o077):
            _private_file(f)
            fixed.append(str(f))
    return fixed


def save_prd_cookie(name: str, value: str) -> None:
    """Scrive il cookie direttamente nel file del downloader (niente valore sulla riga di comando di `prd set-cookie`)."""
    store = prd_cookie_store()
    store.parent.mkdir(parents=True, exist_ok=True)
    _private_dir(store.parent)
    try:
        data = json.loads(store.read_text()) if store.exists() else {}
    except json.JSONDecodeError:
        data = {}
    data[name] = value
    tmp = store.with_name(f".{store.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    tmp.replace(store)
    _private_file(store)


def prd_cookie_names() -> list[str]:
    """Solo i NOMI dei cookie salvati (mai i valori)."""
    try:
        return sorted(json.loads(prd_cookie_store().read_text()))
    except (OSError, json.JSONDecodeError):
        return []

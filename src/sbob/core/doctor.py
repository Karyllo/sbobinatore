"""`sbob doctor`: controlla che tutto ciò che serve sia presente e, se manca, dice il comando esatto per sistemarlo.

Ogni controllo è {nome, stato: ok|manca|avviso, dettaglio, rimedio}. "manca" = qualcosa senza cui un passo non può
funzionare; "avviso" = opzionale o non verificabile.
"""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from sbob.config import Settings
from sbob.core.keys import load_keys

PRD_COOKIES = {
    "Darwin": Path.home() / "Library" / "Application Support" / "polimi_recordings_downloader" / "cookies.json",
    "Linux": Path.home() / ".config" / "polimi_recordings_downloader" / "cookies.json",
}
_SDK = {"gemini": ("google.genai", "gemini"), "openai": ("openai", "openai"), "anthropic": ("anthropic", "anthropic")}


def _check(nome: str, ok: bool, dettaglio: str = "", rimedio: str | None = None, opzionale: bool = False) -> dict:
    stato = "ok" if ok else ("avviso" if opzionale else "manca")
    return {"nome": nome, "stato": stato, "dettaglio": dettaglio, "rimedio": None if ok else rimedio}


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:          # es. "google.genai" senza il pacchetto "google"
        return False


def _brew(pkg: str) -> str:
    return f"brew install {pkg}" if platform.system() == "Darwin" else f"installa {pkg} con il gestore di pacchetti"


def check_binaries() -> list[dict]:
    out = []
    for name, pkg, why in (("ffmpeg", "ffmpeg", "audio e divisione degli audio lunghi"),
                           ("ffprobe", "ffmpeg", "durata degli audio"),
                           ("aria2c", "aria2", "download delle registrazioni")):
        path = shutil.which(name)
        out.append(_check(name, bool(path), path or why, _brew(pkg)))
    return out


def check_config(settings: Settings) -> list[dict]:
    out = [_check("config", settings.path is not None,
                  str(settings.path) if settings.path else "nessun sbob.toml trovato", "sbob init")]
    if settings.path:
        out.append(_check("corsi", bool(settings.corsi), f"{len(settings.corsi)} configurati",
                          "sbob aggiungi-corso", opzionale=True))
    return out


def check_models(settings: Settings) -> list[dict]:
    """Chiavi e SDK per ogni provider usato da un ruolo (riserve comprese)."""
    used: dict[str, list[str]] = {}
    for role, conf in settings.modelli.items():
        used.setdefault(conf.get("provider", "?"), []).append(role)
        riserva = conf.get("riserva")
        if isinstance(riserva, dict) and riserva.get("provider"):
            used.setdefault(riserva["provider"], []).append(f"{role} (riserva)")
        elif isinstance(riserva, str) and riserva:
            used.setdefault(riserva.split(":")[0], []).append(f"{role} (riserva)")
    out = []
    for provider, roles in sorted(used.items()):
        pconf = settings.providers.get(provider)
        if not pconf:
            out.append(_check(f"provider {provider}", False, f"usato da {', '.join(roles)}",
                              f"definisci [providers.{provider}] in sbob.toml"))
            continue
        prefix = pconf.get("chiavi", "")
        n = len(load_keys(prefix, required=False)) if prefix else 0
        out.append(_check(f"chiavi {provider}", n > 0, f"{n} chiavi {prefix}_* · ruoli: {', '.join(roles)}",
                          f"aggiungi {prefix}_ACCOUNT1=... nel .env accanto a sbob.toml (oppure sbob init)"))
        module, extra = _SDK.get(pconf.get("tipo", ""), (None, None))
        if module:
            out.append(_check(f"libreria {extra}", _has(module), module,
                              f'uv tool install --reinstall "sbobinatore[{extra}]" (o "sbobinatore[all]")'))
    if "pdf" in settings.modelli:
        out.append(_check("libreria pdf", _has("pymupdf"), "pymupdf (per sbob pdf)",
                          'uv tool install --reinstall "sbobinatore[pdf]"', opzionale=True))
    return out


def check_downloader(settings: Settings, quick: bool = False) -> list[dict]:
    from sbob.core.report import NeedsHuman
    from sbob.steps.download import prd_command

    try:
        cmd, env = prd_command(settings)
    except NeedsHuman as e:
        return [_check("downloader", False, str(e), e.action)]
    out = []
    if quick:
        out.append(_check("downloader", True, f"{settings.downloader} (non avviato: --veloce)"))
    else:
        res = subprocess.run([*cmd, "--help"], capture_output=True, text=True, env=env, timeout=600)
        ok = res.returncode == 0 and "Usage" in res.stdout
        tail = (res.stderr or res.stdout).strip().splitlines()[-1:] if not ok else []
        out.append(_check("downloader", ok, settings.downloader + (f" · {tail[0]}" if tail else ""),
                          "controlla `downloader` in sbob.toml; serve uv per gli indirizzi git"))
    cookies_file = PRD_COOKIES.get(platform.system())
    cookies = {}
    if cookies_file and cookies_file.exists():
        try:
            cookies = json.loads(cookies_file.read_text())
        except json.JSONDecodeError:
            pass
    out.append(_check("cookie ticket", "ticket" in cookies,
                      "presente (la scadenza si scopre solo al download)" if "ticket" in cookies else "non impostato",
                      "copia il cookie 'ticket' da politecnicomilano.webex.com e lancia: sbob cookie ticket <valore>",
                      opzionale=True))
    if any(s.get("tipo") == "webeep" for c in settings.corsi.values() for s in c.sorgenti):
        out.append(_check("cookie MoodleSession", "MoodleSession" in cookies, "serve alla sorgente webeep",
                          "sbob cookie MoodleSession <valore> (da webeep.polimi.it)", opzionale=True))
    return out


def check_secrets() -> list[dict]:
    """Permessi dei file con credenziali: se sono larghi li stringe subito (operazione sicura e idempotente)."""
    from sbob.core.secrets import secure_permissions

    fixed = secure_permissions()
    return [_check("permessi credenziali", True,
                   f"sistemati {len(fixed)} percorsi (ora solo tu puoi leggerli)" if fixed else "solo tu puoi leggerli")]


def check_login() -> list[dict]:
    from sbob.auth.browser import PROFILE_DIR, load_token

    token = load_token()
    ok, det = False, "nessun token (WeBeep non raggiungibile da sbob)"
    if token:
        try:
            import requests
            r = requests.post("https://webeep.polimi.it/webservice/rest/server.php",
                              data={"wstoken": token, "wsfunction": "core_webservice_get_site_info",
                                    "moodlewsrestformat": "json"}, timeout=15).json()
            ok = "userid" in r
            det = "token valido" if ok else f"token non valido ({r.get('errorcode')})"
        except Exception as e:  # noqa: BLE001
            det = f"verifica non riuscita: {e}"
    out = [_check("token WeBeep", ok, det, "sbob login", opzionale=True),
           _check("profilo login", PROFILE_DIR.exists(),
                  "rinnovo automatico di ticket e cookie" if PROFILE_DIR.exists() else "mai eseguito `sbob login`",
                  "sbob login", opzionale=True),
           _check("playwright", _has("playwright"), "per sbob login",
                  'uv tool install --reinstall "sbobinatore[all]"', opzionale=True)]
    return out


def check_optional(settings: Settings) -> list[dict]:
    out = []
    if any(c.webeep_id or c.extra.get("materiale_siti") for c in settings.corsi.values()):
        has_office = bool(shutil.which("soffice") or shutil.which("libreoffice"))
        out.append(_check("LibreOffice", has_office or _has("markitdown"),
                          "per convertire PowerPoint/Word (con figure)" if has_office else
                          "assente: i .pptx/.docx si convertono solo come testo, se c'è markitdown",
                          "brew install --cask libreoffice", opzionale=True))
    if any(c.trascrizione == "notebooklm" for c in settings.corsi.values()):
        out.append(_check("notebooklm", bool(shutil.which("notebooklm")), "CLI per il backend notebooklm",
                          "uv tool install notebooklm-py && notebooklm login", opzionale=True))
    return out


def run_checks(settings: Settings, quick: bool = False) -> list[dict[str, Any]]:
    return [*check_config(settings), *check_binaries(), *check_models(settings),
            *check_downloader(settings, quick), *check_secrets(), *check_login(), *check_optional(settings)]

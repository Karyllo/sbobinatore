"""`sbob doctor`: controlla che tutto ciò che serve sia presente e, se manca, dice il comando esatto per sistemarlo.

Ogni controllo è {nome, stato: ok|manca|avviso, dettaglio, rimedio}. "manca" = qualcosa senza cui un passo non può
funzionare; "avviso" = opzionale o non verificabile.
"""

from __future__ import annotations

import importlib.util
import platform
import shutil
from typing import Any

from sbob.config import Settings, install_command
from sbob.core.keys import load_keys

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
    for name, pkg, why, opt in (("ffmpeg", "ffmpeg", "audio e divisione degli audio lunghi", False),
                                ("ffprobe", "ffmpeg", "durata degli audio", False),
                                ("aria2c", "aria2", "facoltativo: senza, le registrazioni si scaricano comunque, solo più piano", True)):
        path = shutil.which(name)
        out.append(_check(name, bool(path), path or why, _brew(pkg), opzionale=opt))
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
        if module and extra:
            out.append(_check(f"libreria {extra}", _has(module), module, install_command(extra)))
    if "pdf" in settings.modelli:
        out.append(_check("libreria pdf", _has("pymupdf"), "pymupdf (per sbob pdf)",
                          install_command("pdf"), opzionale=True))
    return out


def check_webex(settings: Settings) -> list[dict]:
    """Registrazioni: serve il cookie `ticket` di Webex (lo salva `sbob login`). Solo il nome, mai il valore."""
    from sbob.core.secrets import cookie_names

    has = "ticket" in cookie_names()
    return [_check("accesso Webex", has, "ticket presente (si rinnova da solo finché la sessione di Ateneo è valida)"
                   if has else "mai fatto `sbob login`", "sbob login", opzionale=True)]


def check_model_news(settings: Settings) -> list[dict]:
    """Modelli Gemini dei ruoli: ancora disponibili? preview? novità? (gratis, non consuma quota). Solo avvisi."""
    from sbob.core import models

    try:
        res = models.check(settings, remember=False)
    except Exception as e:  # noqa: BLE001 — rete assente: non è un problema di configurazione
        return [_check("modelli Gemini", True, f"controllo saltato ({type(e).__name__})")]
    gone = [r for r in res["ruoli"] if not r["disponibile"]]
    if gone:
        return [_check("modelli Gemini", False, "non più disponibili: " + ", ".join(sorted({r["modello"] for r in gone})),
                       "sbob modelli  (poi cambia il modello in [modelli.<ruolo>] di sbob.toml)")]
    n = len(res["avvisi"])
    return [_check("modelli Gemini", n == 0, "disponibili, nessuna novità" if n == 0 else f"{n} novità da vedere",
                   "sbob modelli", opzionale=True)]


def check_secrets() -> list[dict]:
    """Permessi dei file con credenziali: se sono larghi li stringe subito (operazione sicura e idempotente)."""
    from sbob.core.secrets import secure_permissions

    fixed = secure_permissions()
    return [_check("permessi credenziali", True,
                   f"sistemati {len(fixed)} percorsi (ora solo tu puoi leggerli)" if fixed else "solo tu puoi leggerli")]


def check_login() -> list[dict]:
    from sbob.auth.browser import load_token, profile_dir

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
    from sbob.auth.browser import browser_installed, current_kind

    kind = current_kind()
    out = [_check("browser per il login", browser_installed(kind),
                  ("Firefox dedicato di sbob" if kind == "firefox" else "Chrome, Edge o Chromium (anche quello dedicato di sbob)")
                  if browser_installed(kind) else "nessuno trovato: serve solo per `sbob login`",
                  "sbob installa-browser" + (" --browser firefox" if kind == "firefox" else ""), opzionale=True),
           _check("token WeBeep", ok, det, "sbob login", opzionale=True),
           _check("profilo login", profile_dir().exists(),
                  "rinnovo automatico di ticket e cookie" if profile_dir().exists() else "mai eseguito `sbob login`",
                  "sbob login", opzionale=True),
           _check("playwright", _has("playwright"), "per sbob login",
                  install_command("login"), opzionale=True)]
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
    if settings.raw.get("notebook", {}).get("attivo"):
        from sbob import notebooklm_cli

        if not shutil.which("notebooklm"):
            out.append(_check("notebooklm", False, "CLI per il taccuino automatico",
                              "uv tool install notebooklm-py && notebooklm login", opzionale=True))
        else:
            ok, det = notebooklm_cli.auth_status()
            out.append(_check("login notebooklm", ok, det, "notebooklm login", opzionale=True))
    return out


def run_checks(settings: Settings, quick: bool = False) -> list[dict[str, Any]]:
    return [*check_config(settings), *check_binaries(), *check_models(settings),
            *check_webex(settings), *([] if quick else check_model_news(settings)), *check_secrets(), *check_login(), *check_optional(settings)]

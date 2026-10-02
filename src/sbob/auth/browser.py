"""`sbob login`: accesso di Ateneo in una finestra di Chrome (profilo dedicato), senza incollare cookie.

Dal login si ricavano:
  - il token dell'app mobile di Moodle per WeBeep (dura mesi) → ~/.config/sbob/webeep_token (0600)
  - il cookie MoodleSession (sorgente `webeep` del downloader)
  - il cookie `ticket` di Webex (download delle registrazioni)

Il profilo resta in ~/.config/sbob/browser: finché la sessione di Ateneo è valida, `sbob login --rinnova`
ripete tutto in headless, senza finestra. Flusso del token come webeep-sync (src/modules/login.ts):
dopo /my/ si chiama admin/tool/mobile/launch.php e il redirect `moodlemobile://token=<base64>` contiene il token.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

from sbob.config import CONFIG_HOME
from sbob.core.report import NeedsHuman

PROFILE_DIR = CONFIG_HOME / "browser"
# I cookie di sessione (accesso di Ateneo) non sopravvivono alla chiusura del browser: li salviamo e reiniettiamo,
# così `--rinnova` funziona finché la sessione è valida lato server.
STATE_FILE = CONFIG_HOME / "browser_state.json"
TOKEN_FILE = CONFIG_HOME / "webeep_token"
WEBEEP = "https://webeep.polimi.it"
LOGIN_URL = f"{WEBEEP}/auth/shibboleth/index.php"
MY_URL = re.compile(r"^https://webeep\.polimi\.it/my/")
LAUNCH_URL = f"{WEBEEP}/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport={{}}"
WEBEX = "https://politecnicomilano.webex.com"


def parse_token_location(location: str) -> str:
    """`moodlemobile://token=<base64("sitehash:::TOKEN[:::privatetoken]")>` → TOKEN."""
    m = re.search(r"token=([A-Za-z0-9+/=_-]+)", location or "")
    if not m:
        raise ValueError("risposta senza token")
    raw = base64.b64decode(m.group(1) + "=" * (-len(m.group(1)) % 4)).decode()
    parts = raw.split(":::")
    if len(parts) < 2 or not parts[1]:
        raise ValueError("formato del token inatteso")
    return parts[1]


def save_token(token: str) -> None:
    CONFIG_HOME.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_HOME, 0o700)
    fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # mai leggibile da altri, neanche per un istante
    with os.fdopen(fd, "w") as f:
        f.write(token)


def load_token() -> str | None:
    if tok := os.environ.get("WEBEEP_TOKEN", "").strip():
        return tok
    return TOKEN_FILE.read_text(encoding="utf-8").strip() if TOKEN_FILE.exists() else None


@contextmanager
def browser(headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise NeedsHuman("Playwright non installato", action='uv tool install --reinstall "sbobinatore[all]"') from None
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(CONFIG_HOME, 0o700)
    os.chmod(PROFILE_DIR, 0o700)       # il profilo contiene la sessione di Ateneo
    with sync_playwright() as p:
        try:
            ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), channel="chrome", headless=headless,
                                                       viewport={"width": 1100, "height": 800})
        except Exception:  # noqa: BLE001 — Chrome non installato: si prova con il Chromium di Playwright
            try:
                ctx = p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)
            except Exception as e:  # noqa: BLE001
                raise NeedsHuman(f"Impossibile avviare il browser: {e}",
                                 action="installa Google Chrome oppure: uv tool run playwright install chromium") from None
        if STATE_FILE.exists():
            try:
                ctx.add_cookies(json.loads(STATE_FILE.read_text())["cookies"])
            except Exception:  # noqa: BLE001 — stato corrotto o vecchio: si ignora
                pass
        try:
            yield ctx
        finally:
            try:
                state = ctx.storage_state()
                fd = os.open(STATE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as f:
                    json.dump(state, f)
            except Exception:  # noqa: BLE001
                pass
            ctx.close()


def _webeep_session(ctx, headless: bool, timeout_s: int, log: Callable[[str], None]):
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(LOGIN_URL)
    if not MY_URL.match(page.url):
        if headless:
            try:
                page.wait_for_url(MY_URL, timeout=20_000)       # SSO ancora valido: redirect automatici
            except Exception:  # noqa: BLE001
                raise NeedsHuman("Sessione di Ateneo scaduta", action="sbob login") from None
        else:
            log("Completa l'accesso di Ateneo nella finestra di Chrome che si è aperta…")
            page.wait_for_url(MY_URL, timeout=timeout_s * 1000)
    return page


def _webeep_token(ctx) -> str:
    resp = ctx.request.get(LAUNCH_URL.format(secrets.token_hex(8)), max_redirects=0)
    location = resp.headers.get("location", "")
    if not location.startswith("moodlemobile://"):
        raise RuntimeError(f"WeBeep non ha restituito il token (HTTP {resp.status})")
    return parse_token_location(location)


def _cookie(ctx, url: str, name: str) -> str | None:
    return next((c["value"] for c in ctx.cookies(url) if c["name"] == name), None)


def _wait_cookie(ctx, url: str, name: str, seconds: int, page) -> str | None:
    for _ in range(max(1, seconds)):
        if value := _cookie(ctx, url, name):
            return value
        page.wait_for_timeout(1000)
    return None


def _webex_ticket(ctx, page, headless: bool, email: str | None, timeout_s: int,
                  log: Callable[[str], None]) -> str | None:
    """Webex: "Sign in" → pagina Cisco che chiede l'email → accesso del Poli (già aperto, passa da solo) → cookie `ticket`."""
    page.goto(f"{WEBEX}/webappng/sites/politecnicomilano/dashboard")
    try:
        page.wait_for_load_state("networkidle", timeout=30_000)
    except Exception:  # noqa: BLE001
        pass
    if ticket := _cookie(ctx, WEBEX, "ticket"):
        return ticket
    sign_in = page.locator("button, a").filter(has_text=re.compile(r"^\s*(sign in|accedi)\s*$", re.I))
    if sign_in.count():
        sign_in.first.click()
    try:
        email_box = page.locator("input[type=email], input[name=email], input#IDToken1").first
        email_box.wait_for(state="visible", timeout=15_000)
        if email:
            email_box.fill(email)
            email_box.press("Enter")
        elif headless:
            log("Webex: serve l'email del Poli per l'accesso automatico (aggiungi [login] email = \"...\" in sbob.toml)")
            return None
        else:
            log("Webex: scrivi la tua email del Poli nella finestra di Chrome e premi Invio…")
    except Exception:  # noqa: BLE001 — nessun campo email: forse l'accesso è già in corso
        pass
    ticket = _wait_cookie(ctx, WEBEX, "ticket", 30 if headless else timeout_s, page)
    if not ticket:
        names = sorted({c["name"] for c in ctx.cookies(WEBEX)})
        log(f"Webex: cookie 'ticket' non trovato (pagina {page.url[:80]}, cookie: {names})")
    return ticket


def login(settings, headless: bool = False, timeout_s: int = 600,
          log: Callable[[str], None] = print) -> dict[str, bool]:
    """Esegue (o rinnova, con headless=True) l'accesso e salva token e cookie. Restituisce cosa è stato ottenuto."""
    from sbob.steps.download import set_cookie

    got = {"webeep_token": False, "MoodleSession": False, "ticket": False}
    with browser(headless) as ctx:
        page = _webeep_session(ctx, headless, timeout_s, log)
        save_token(_webeep_token(ctx))
        got["webeep_token"] = True
        if ms := _cookie(ctx, WEBEEP, "MoodleSession"):
            set_cookie(settings, "MoodleSession", ms)
            got["MoodleSession"] = True
        email = (settings.raw.get("login", {}) or {}).get("email")
        if ticket := _webex_ticket(ctx, page, headless, email, timeout_s, log):
            set_cookie(settings, "ticket", ticket)
            got["ticket"] = True
    return got

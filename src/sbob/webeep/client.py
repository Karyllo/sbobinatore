"""Client dell'API di WeBeep (Moodle), con le stesse chiamate di webeep-sync (src/modules/moodle.ts).

Il token (da `sbob login`) non viene mai stampato né incluso in messaggi di errore.
"""

from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from sbob.core.report import NeedsHuman

BASE = "https://webeep.polimi.it"
API = f"{BASE}/webservice/rest/server.php"
# moduli senza file utili (come webeep-sync)
EXCLUDED_MODNAMES = {"page", "forum", "url", "wooclap", "choice", "feedback", "label", "lesson"}


class MissingOnServer(RuntimeError):
    """File elencato da WeBeep ma che il server non trova (404): è rotto lì, non per colpa nostra."""


@dataclass(frozen=True)
class RemoteFile:
    sezione: str
    percorso: str          # sottocartelle sotto la sezione (può essere vuoto)
    nome: str
    url: str
    size: int
    modified: int          # timestamp unix
    module: int = 0        # id del modulo WeBeep (serve per scaricare la cartella intera come zip)
    inner: str = ""        # percorso del file dentro la cartella del modulo (come appare nello zip)

    @property
    def relpath(self) -> str:
        return "/".join(p for p in (self.sezione, self.percorso, self.nome) if p)

    @property
    def key(self) -> str:
        """Identità stabile del file (senza il token): modulo + nome nella sua posizione."""
        return self.relpath


def safe_name(name: str) -> str:
    """Nome valido su disco: niente separatori di percorso, niente caratteri di controllo, mai vuoto o '..'."""
    name = re.sub(r"[\\/]", "_", name)
    name = re.sub(r"[\x00-\x1f]", "", name).strip().strip(".")
    return name or "senza_nome"


def safe_path(path: str) -> str:
    """Percorso relativo fatto di segmenti sicuri (blocca `..` e percorsi assoluti dai metadati del server)."""
    return "/".join(safe_name(p) for p in path.split("/") if p not in ("", ".", ".."))


class WebeepClient:
    def __init__(self, token: str, session: requests.Session | None = None):
        if not token:
            raise NeedsHuman("Token WeBeep mancante", action="sbob login")
        self._token = token
        self._http = session or requests.Session()

    def call(self, wsfunction: str, **params: Any) -> Any:
        try:
            r = self._http.post(API, data={"wstoken": self._token, "wsfunction": wsfunction,
                                           "moodlewsrestformat": "json", **params}, timeout=60)
            data = r.json()
        except (requests.RequestException, ValueError) as e:
            raise RuntimeError(f"WeBeep non raggiungibile: {type(e).__name__}") from None
        if isinstance(data, dict) and data.get("exception"):
            if data.get("errorcode") in ("invalidtoken", "accessexception"):
                raise NeedsHuman("Token WeBeep scaduto o non valido", action="sbob login")
            raise RuntimeError(f"WeBeep {wsfunction}: {data.get('errorcode')} {data.get('message', '')[:120]}")
        return data

    def site_info(self) -> dict:
        return self.call("core_webservice_get_site_info")

    def courses(self) -> list[dict]:
        """Corsi in cui l'utente è iscritto: anche degli anni passati (base per l'archivio)."""
        uid = self.site_info()["userid"]
        out = []
        for c in self.call("core_enrol_get_users_courses", userid=uid):
            m = re.search(r"\[(\d{4})-(\d{2})\]", c.get("fullname", ""))
            anno = f"{m.group(1)}-{m.group(2)}" if m else None
            if anno is None and c.get("startdate"):               # senza [AAAA-AA] nel nome: dall'inizio del corso
                d = datetime.fromtimestamp(c["startdate"])
                y = d.year if d.month >= 8 else d.year - 1
                anno = f"{y}-{(y + 1) % 100:02d}"
            out.append({"id": c["id"], "nome": c.get("fullname", ""), "breve": c.get("shortname", ""),
                        "anno": anno,
                        "inizio": c.get("startdate"), "fine": c.get("enddate")})
        return sorted(out, key=lambda c: (c["anno"] or "", c["nome"]), reverse=True)

    def files(self, course_id: int) -> list[RemoteFile]:
        """File del corso con le regole di webeep-sync, più la sezione come primo livello di cartella."""
        out: list[RemoteFile] = []
        for section in self.call("core_course_get_contents", courseid=course_id):
            sezione = safe_name(section.get("name") or "Generale")
            for module in section.get("modules", []):
                if module.get("modname") in EXCLUDED_MODNAMES or not module.get("contents"):
                    continue
                contents = [f for f in module["contents"] if f.get("type") == "file"]
                for f in contents:
                    nome, percorso = f.get("filename", ""), (f.get("filepath") or "/").strip("/")
                    if module.get("modname") == "resource" and len(module["contents"]) == 1:
                        nome = module.get("name", nome) + os.path.splitext(nome)[1]
                    else:
                        percorso = "/".join(p for p in (module.get("name", ""), percorso) if p)
                    inner = "/".join(p for p in ((f.get("filepath") or "/").strip("/"), f.get("filename", "")) if p)
                    out.append(RemoteFile(sezione, safe_path(percorso), safe_name(nome), f["fileurl"],
                                          int(f.get("filesize") or 0), int(f.get("timemodified") or 0),
                                          int(module.get("id") or 0), inner))
        return out

    def folder_zip(self, module: int, moodle_session: str):
        """Cartella intera come zip dal sito (sessione web, non il token): serve per i file con nomi che il percorso
        normale non riesce a raggiungere (nomi con `%28`, `%C3%A0`…: pluginfile dà 404, lo zip li contiene).
        Restituisce un `zipfile.ZipFile` o None se non riesce. Il cookie non si stampa né si salva."""
        import io
        import zipfile

        s = requests.Session()
        s.cookies.set("MoodleSession", moodle_session, domain="webeep.polimi.it")
        try:
            page = s.get(f"{BASE}/mod/folder/view.php?id={module}", timeout=60, allow_redirects=False)
            m = re.search(r'sesskey["\']?\s*[:=]\s*["\']([A-Za-z0-9]+)', page.text) or re.search(r"sesskey=([A-Za-z0-9]+)", page.text)
            if page.status_code != 200 or not m:
                return None
            z = s.get(f"{BASE}/mod/folder/download_folder.php", params={"id": module, "sesskey": m.group(1)}, timeout=300)
            return zipfile.ZipFile(io.BytesIO(z.content)) if z.ok else None
        except (requests.RequestException, zipfile.BadZipFile):
            return None

    def text(self, fileurl: str) -> str:
        """Contenuto testuale di un file Moodle (es. l'index.html di una pagina)."""
        try:
            r = self._http.get(fileurl, params={"token": self._token}, timeout=60)
        except requests.RequestException as e:
            raise RuntimeError(f"WeBeep non raggiungibile: {type(e).__name__}") from None
        return r.text if r.ok else ""

    def download(self, f: RemoteFile, dst: Path) -> None:
        """Scarica in un file temporaneo e lo rinomina (mai un file a metà); mtime = data di modifica del server.
        Nota: per i file Moodle il parametro si chiama `token`, non `wstoken`."""
        dst.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=dst.parent, prefix=".dl-")
        try:
            with os.fdopen(fd, "wb") as out, self._http.get(f.url, params={"token": self._token}, stream=True,
                                                            timeout=120) as r:
                if r.status_code == 404:
                    raise MissingOnServer("elencato da WeBeep ma non scaricabile (404): file rotto sul server")
                r.raise_for_status()
                for chunk in r.iter_content(1 << 16):
                    out.write(chunk)
            if f.modified:
                os.utime(tmp, (f.modified, f.modified))
            os.replace(tmp, dst)
        except requests.RequestException as e:
            raise RuntimeError(f"download fallito: {type(e).__name__}") from None
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

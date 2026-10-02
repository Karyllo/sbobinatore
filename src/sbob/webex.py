"""Registrazioni Webex del Politecnico: id dai link, informazioni e link di download, scarico.

Sostituisce il downloader esterno `prd` (polimi_recordings_downloader, di Paolo Basso, MIT), da cui riprende il
funzionamento di base: id dal link → API `recordings/<id>/stream` con il cookie `ticket` → `mp4URL` → aria2c.

Il `ticket` viene da `sbob login` (secrets.load_cookie) e non esce mai da qui: non si stampa, non va nei report
né sulla riga di comando (aria2c riceve solo i link firmati dei file, che non contengono credenziali di sessione).
"""

from __future__ import annotations

import html
import re
import shutil
import subprocess
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote

import requests

SITE = "https://politecnicomilano.webex.com"
API = SITE + "/webappng/api/v1/recordings/{id}/stream?siteurl=politecnicomilano"
TIMEOUT = 30
_ID = re.compile(r"[0-9a-f]{32}", re.I)
# link a una registrazione: recordingservice/webappng .../recording/[playback/]<id>[/playback]
_RECORDING = re.compile(r"/(?:recordingservice|webappng)/sites/[^/]+/recording/(?:playback/)?([0-9a-f]{32})", re.I)
_LDR = re.compile(r"/ldr\.php\?RCID=([0-9a-f]+)", re.I)
_GOOGLE = re.compile(r"https?://www\.google\.com/url\?q=([^&]+)")
_HREF = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_WEBEX_URL = re.compile(r"https?://[\w.-]*webex\.com/[^\s\"'<>\\]+", re.I)
LOGIN_CODES = {53004}               # "Recording required logged before access": ticket assente o scaduto


class TicketError(RuntimeError):
    """Il ticket Webex manca o è scaduto: serve `sbob login` (o il rinnovo automatico)."""


@dataclass
class Recording:
    id: str
    name: str                 # titolo Webex (spesso "Stanza personale di ...": non è un argomento)
    created: datetime         # ora locale dell'account Webex (quella che usava prd)
    created_gmt: datetime | None
    url: str                  # mp4 (o hls se il download è disattivato dal docente)
    hls: bool = False
    duration_s: int = 0
    size: int = 0

    @property
    def legacy_key(self) -> str:
        """Chiave usata da prd e nei manifest esistenti ("YYYY-MM-DD HH-MM")."""
        return self.created.strftime("%Y-%m-%d %H-%M")


def session(ticket: str | None) -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = "sbob"
    if ticket:
        s.cookies.set("ticket", ticket, domain="politecnicomilano.webex.com")
    return s


def is_recording_link(url: str) -> bool:
    url = unquote(url)
    return bool(_RECORDING.search(url) or _LDR.search(url))


def unwrap(url: str) -> str:
    url = html.unescape(url.strip())
    if m := _GOOGLE.match(url):
        url = unquote(m.group(1))
    return url


def video_id(url: str, http: requests.Session) -> str | None:
    """Id della registrazione da un link (o None se il link non è una registrazione). Per `ldr.php?RCID=` serve
    una richiesta: l'RCID non è l'id e Webex reindirizza alla pagina della registrazione."""
    url = unwrap(url)
    if _ID.fullmatch(url):
        return url.lower()
    decoded = unquote(url)
    if m := _RECORDING.search(decoded):
        return m.group(1).lower()
    if _LDR.search(decoded):
        try:
            r = http.get(url, timeout=TIMEOUT)
        except requests.RequestException as e:
            raise RuntimeError(f"Webex non raggiungibile: {type(e).__name__}") from None
        if m := _RECORDING.search(unquote(r.url)) or _RECORDING.search(r.text):
            return m.group(1).lower()
        raise RuntimeError("link ldr.php: registrazione non trovata (link scaduto o rimosso?)")
    return None


def links_in_html(text: str) -> list[str]:
    """Link alle registrazioni in una pagina HTML (href, anche dietro i redirect di Google), in ordine, senza doppioni."""
    found = [unwrap(h) for h in _HREF.findall(text)] + [unwrap(u) for u in _WEBEX_URL.findall(text)]
    out: dict[str, str] = {}
    for u in found:
        d = unquote(u)
        m = _RECORDING.search(d) or _LDR.search(d)
        if m:
            out.setdefault(m.group(1).lower(), u)          # stesso video citato più volte (href e testo): uno solo
    return list(out.values())


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S") if text else None
    except ValueError:
        return None


def recording(vid: str, http: requests.Session) -> Recording:
    try:
        r = http.get(API.format(id=vid), timeout=TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"Webex non raggiungibile: {type(e).__name__}") from None
    if "json" not in (r.headers.get("content-type") or ""):
        raise TicketError("Webex non ha risposto con i dati della registrazione (ticket scaduto?)")
    data = r.json()
    if data.get("code") in LOGIN_CODES or not data.get("downloadRecordingInfo"):
        if r.status_code in (401, 403) or data.get("code") in LOGIN_CODES:
            raise TicketError("ticket Webex mancante o scaduto")
        raise RuntimeError(f"registrazione {vid[:8]}…: {data.get('message') or 'nessun link di download'}")
    info = data["downloadRecordingInfo"].get("downloadInfo", {})
    url, hls = info.get("mp4URL"), False
    if data.get("preventDownload") or not url:          # download disattivato: si prende lo stream
        url, hls = info.get("hlsURL") or data.get("fallbackPlaySrc"), True
    if not url:
        raise RuntimeError(f"registrazione {vid[:8]}…: nessun link scaricabile")
    created = _when(data.get("createTime")) or _when(data.get("gmtCreateTime")) or datetime.min
    return Recording(id=vid, name=str(data.get("recordName") or "").strip(), created=created,
                     created_gmt=_when(data.get("gmtCreateTime")), url=url, hls=hls or ".m3u8" in url.split("?")[0],
                     duration_s=int(data.get("duration") or 0) // 1000, size=int(data.get("fileSize") or 0))


def recordings(ids: Iterable[str], http: requests.Session, workers: int = 8) -> dict[str, Recording | Exception]:
    """Informazioni di più registrazioni in parallelo. Un TicketError si propaga subito (vale per tutte)."""
    ids = list(dict.fromkeys(ids))

    def one(v: str) -> Recording | Exception:
        try:
            return recording(v, http)
        except TicketError:
            raise
        except RuntimeError as e:
            return e
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(zip(ids, pool.map(one, ids)))


# --------------------------------------------------------------------------- scarico

def download(items: list[tuple[Recording, str]], dest: Path, log: Callable[[str], None] = print,
             connections: int = 16) -> dict[str, Path]:
    """Scarica in `dest` come <nome>.mp4 e restituisce {id: file} di quelli completi.
    mp4 → aria2c (riprende da dove si era fermato grazie ai file .aria2), oppure in Python se aria2c manca.
    hls → ffmpeg (copia dei flussi, senza ricodifica)."""
    dest.mkdir(parents=True, exist_ok=True)
    mp4 = [(r, n) for r, n in items if not r.hls]
    hls = [(r, n) for r, n in items if r.hls]
    if mp4:
        if shutil.which("aria2c"):
            _aria2c(mp4, dest, connections)
        else:
            for r, n in mp4:
                _python_download(r.url, dest / f"{n}.mp4")
    for r, n in hls:
        log(f"download: {n} ha il download disattivato, salvo lo stream con ffmpeg…")
        try:
            _ffmpeg_hls(r.url, dest / f"{n}.mp4")
        except RuntimeError as e:
            log(f"download: {n}: {e}")
    done = {}
    for r, n in items:
        f = dest / f"{n}.mp4"
        if f.exists() and not (dest / f"{n}.mp4.aria2").exists() and f.stat().st_size > 0:
            done[r.id] = f
    return done


def _aria2c(items: list[tuple[Recording, str]], dest: Path, connections: int) -> None:
    listing = dest / "aria2_input.txt"
    listing.write_text("".join(f"{r.url}\n  out={n}.mp4\n" for r, n in items), encoding="utf-8")
    try:
        subprocess.run(["aria2c", f"--input-file={listing}", f"--dir={dest}", "--continue=true",
                        "--max-concurrent-downloads=4", f"--max-connection-per-server={connections}",
                        f"--split={connections}", "--auto-file-renaming=false", "--allow-overwrite=false",
                        "--disable-ipv6=true", "--console-log-level=warn", "--summary-interval=0",
                        "--download-result=hide"], check=False)
    finally:
        listing.unlink(missing_ok=True)         # contiene i link firmati: non lo si lascia in giro


def _python_download(url: str, dst: Path) -> None:
    """Riserva senza aria2c: ripresa con Range su <file>.part, rinomina solo a file completo."""
    part = dst.with_name(dst.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={have}-"} if have else {}
    with requests.get(url, headers=headers, stream=True, timeout=TIMEOUT) as r:
        if r.status_code == 416:            # già tutto scaricato
            part.replace(dst)
            return
        r.raise_for_status()
        mode = "ab" if have and r.status_code == 206 else "wb"
        with open(part, mode) as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    part.replace(dst)


def _ffmpeg_hls(url: str, dst: Path) -> None:
    tmp = dst.with_name(dst.stem + ".part.mp4")
    res = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", url, "-c", "copy", str(tmp)],
                         capture_output=True, text=True)
    if res.returncode == 0 and tmp.exists():
        tmp.replace(dst)
    else:
        tmp.unlink(missing_ok=True)
        raise RuntimeError("ffmpeg: stream non salvato " + (res.stderr.strip().splitlines() or [""])[-1][:120])

